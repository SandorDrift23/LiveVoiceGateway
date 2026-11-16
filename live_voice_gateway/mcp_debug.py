"""Local MCP clients for Home Assistant and Music Assistant.

The gateway calls these endpoints on the LAN. Private URLs and tokens are
never sent to OpenAI. Advertised tools are exposed as client-executed
functions on the Live session.
"""

import json
import logging
import threading
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_PLAYER_FIELDS = ("player_id", "player", "queue_id", "target_player")


class HomeAssistantClient:
    def __init__(
        self,
        url: str,
        token: str,
        timeout: float,
        allowlist: tuple[str, ...] = (),
        label: str = "Home Assistant",
    ):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.label = label
        self.allowlist = {name.strip() for name in allowlist if name.strip()}
        self._session_id = ""
        self._tools: list[dict] = []
        self._lock = threading.Lock()
        self._rpc_id = 2

    def connect(self) -> bool:
        with self._lock:
            ready = self._connect_locked()
            if ready:
                self._after_connect_locked()
            return ready

    def function_tools(self) -> list[dict]:
        with self._lock:
            tools = list(self._tools)
        selected = []
        for tool in tools:
            name = str(tool.get("name") or "").strip()
            if not name or (self.allowlist and name not in self.allowlist):
                continue
            schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
            description = str(tool.get("description") or tool.get("title") or name)
            selected.append(
                {
                    "type": "function",
                    "name": name,
                    "description": description,
                    "parameters": schema,
                }
            )
        return selected

    def call_tool(self, name: str, arguments: str) -> str:
        with self._lock:
            if self.allowlist and name not in self.allowlist:
                return f"Tool {name} is not allowed."
            if not self._tools:
                if not self._connect_locked():
                    return f"{self.label} is not connected."
                self._after_connect_locked()
            status, output = self._call_locked(name, arguments)
            if status != 200:
                self._session_id = ""
                self._tools = []
            return output

    def _connect_locked(self) -> bool:
        logger.info("%s MCP connect: %s", self.label, self.url)
        status, headers, body = _post(
            self.url, self.token, _initialize_body(), self.timeout, ""
        )
        logger.info("MCP initialize HTTP %s", status)
        if status != 200:
            _log_body("initialize", body)
            logger.warning("%s MCP is not usable", self.label)
            self._tools = []
            return False
        self._session_id = headers.get("mcp-session-id", "")
        if self._session_id:
            _post(
                self.url,
                self.token,
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                self.timeout,
                self._session_id,
            )
        status, _, body = _post(
            self.url, self.token, _tools_body(), self.timeout, self._session_id
        )
        logger.info("MCP tools/list HTTP %s", status)
        self._tools = _tool_defs(body)
        names = [str(tool.get("name") or "") for tool in self._tools]
        names = [name for name in names if name]
        if self.allowlist:
            names = [name for name in names if name in self.allowlist]
        if names:
            logger.info("%s MCP tools attached: %s", self.label, ", ".join(names))
            return True
        _log_body("tools/list", body)
        logger.warning("MCP tools/list returned no tool names")
        return False

    def _after_connect_locked(self) -> None:
        return

    def _call_locked(self, name: str, arguments: str) -> tuple[int, str]:
        try:
            payload = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload = self._prepare_payload(name, payload)
        self._rpc_id += 1
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self._rpc_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": payload},
            }
        )
        status, _, response = _post(
            self.url, self.token, body, self.timeout, self._session_id
        )
        if status != 200:
            return status, f"{self.label} error: HTTP {status}"
        return status, _result_text(response, self.label)

    def _prepare_payload(self, name: str, payload: dict) -> dict:
        return payload

    def _schema_properties(self, name: str) -> dict:
        for tool in self._tools:
            if tool.get("name") != name:
                continue
            schema = tool.get("inputSchema") or {}
            props = schema.get("properties") or {}
            return props if isinstance(props, dict) else {}
        return {}


def _initialize_body() -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "live-voice-gateway", "version": "0.2.1"},
            },
        }
    )


def _tools_body() -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})


def _post(
    url: str, token: str, body: str, timeout: float, session_id: str
) -> tuple[int, dict[str, str], str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        url, data=body.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(262144).decode("utf-8", "replace")
            return response.status, _header_map(response.headers), payload
    except urllib.error.HTTPError as exc:
        payload = exc.read(262144).decode("utf-8", "replace")
        return exc.code, _header_map(exc.headers), payload
    except Exception as exc:
        logger.warning("MCP request failed: %s", exc)
        return 0, {}, ""


def _header_map(headers) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _json_objects(body: str) -> list[dict]:
    found = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            found.append(payload)
    return found


def _tool_defs(body: str) -> list[dict]:
    tools: list[dict] = []
    for payload in _json_objects(body):
        for tool in (payload.get("result") or {}).get("tools") or []:
            if isinstance(tool, dict) and tool.get("name"):
                tools.append(tool)
    return tools


def _tool_names(body: str) -> list[str]:
    return [str(tool.get("name") or "") for tool in _tool_defs(body)]


def _result_text(body: str, label: str = "Home Assistant") -> str:
    payloads = _json_objects(body)
    if not payloads:
        compact = " ".join(body.split())
        return compact[:1000] or f"Empty {label} response"
    payload = payloads[-1]
    if payload.get("error"):
        error = payload["error"]
        message = error.get("message", error) if isinstance(error, dict) else error
        return f"{label} error: {message}"
    result = payload.get("result") or {}
    text = _content_text(result) or "The tool finished without a message."
    if result.get("isError"):
        return f"{label} error: {text}"
    if len(text) > 4000:
        text = text[:4000]
    return text


def _content_text(result: dict) -> str:
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("text"):
            parts.append(str(item["text"]))
    return " ".join(parts).strip()


def _log_body(label: str, body: str) -> None:
    compact = " ".join(body.split())
    if len(compact) > 500:
        compact = compact[:500] + "..."
    logger.info("MCP %s response: %s", label, compact or "<empty>")


class MusicAssistantClient(HomeAssistantClient):
    """Music Assistant MCP client that fills an optional default player."""

    def __init__(
        self,
        url: str,
        token: str,
        timeout: float,
        allowlist: tuple[str, ...] = (),
        default_player: str = "",
    ):
        super().__init__(url, token, timeout, allowlist, label="Music Assistant")
        self.default_player = default_player.strip()
        self._players: dict[str, str] = {}
        self._resolved_default = self.default_player

    def _after_connect_locked(self) -> None:
        if self.default_player:
            self._load_players_locked()

    def _prepare_payload(self, name: str, payload: dict) -> dict:
        prepared = dict(payload)
        if _targets_player(name, self._schema_properties(name)):
            return self._fill_player(prepared, self._schema_properties(name))
        if name != "call_tool":
            return prepared
        command = str(prepared.get("name") or prepared.get("command") or "")
        for key in ("arguments", "args"):
            inner = _object(prepared.get(key))
            if inner is None or not _targets_player(command, {}):
                continue
            prepared[key] = self._fill_player(inner, {})
        return prepared

    def _fill_player(self, payload: dict, schema: dict) -> dict:
        filled = dict(payload)
        provided = next(
            (key for key in _PLAYER_FIELDS if str(filled.get(key) or "").strip()),
            "",
        )
        if provided:
            filled[provided] = self._player_id(str(filled[provided]))
            return filled
        if not self._resolved_default:
            return filled
        targets = [key for key in _PLAYER_FIELDS if key in schema]
        filled[targets[0] if targets else "player_id"] = self._resolved_default
        logger.info("Music Assistant default player: %s", self._resolved_default)
        return filled

    def _player_id(self, spoken: str) -> str:
        return self._players.get(spoken.strip().casefold(), spoken.strip())

    def _load_players_locked(self) -> None:
        tool = _player_list_tool(self._tools, self.allowlist)
        if not tool:
            logger.info(
                "Music Assistant default player %s (no player list tool)",
                self.default_player,
            )
            return
        status, output = self._call_locked(tool, "{}")
        if status != 200:
            logger.warning("Music Assistant player list failed: %s", output)
            return
        self._players = _player_index(output)
        resolved = self._player_id(self.default_player)
        self._resolved_default = resolved
        if resolved == self.default_player and self.default_player.casefold() not in self._players:
            logger.warning(
                "Music Assistant has no player named %s; passing that text as the player",
                self.default_player,
            )
            return
        logger.info(
            "Music Assistant default player %s resolved to %s",
            self.default_player,
            resolved,
        )


class ToolRouter:
    """Send each model tool call to the MCP client that advertised it."""

    def __init__(self, clients: list[HomeAssistantClient]):
        self.clients = [client for client in clients if client is not None]
        self._routes: dict[str, tuple[HomeAssistantClient, str]] = {}
        self._counts: dict[str, int] = {}

    def function_tools(self) -> list[dict]:
        selected = []
        routes: dict[str, tuple[HomeAssistantClient, str]] = {}
        counts = {client.label: 0 for client in self.clients}
        used: set[str] = set()
        for client in self.clients:
            for tool in client.function_tools():
                original = str(tool.get("name") or "")
                exposed = original
                if exposed in used:
                    exposed = f"{_slug(client.label)}__{original}"
                    tool = {**tool, "name": exposed}
                used.add(exposed)
                routes[exposed] = (client, original)
                counts[client.label] = counts.get(client.label, 0) + 1
                selected.append(tool)
        self._routes = routes
        self._counts = counts
        return selected

    def count(self, label: str) -> int:
        return self._counts.get(label, 0)

    def call_tool(self, name: str, arguments: str) -> str:
        route = self._routes.get(name)
        if route is None:
            self.function_tools()
            route = self._routes.get(name)
        if route is None:
            return f"Tool {name} is not available."
        client, original = route
        return client.call_tool(original, arguments)


def _targets_player(name: str, schema: dict) -> bool:
    if any(key in schema for key in _PLAYER_FIELDS):
        return True
    lowered = name.lower()
    if "list_player" in lowered or lowered in {"search_tools", "get_tool_schema", "call_tool"}:
        return False
    return any(part in lowered for part in ("playback", "queue", "volume", "player"))


def _player_list_tool(tools: list[dict], allowlist: set[str]) -> str:
    for tool in tools:
        name = str(tool.get("name") or "")
        lowered = name.lower()
        if allowlist and name not in allowlist:
            continue
        if lowered in {"players_list_players", "list_players"} or lowered.endswith("list_players"):
            return name
    return ""


def _player_index(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    parsed = _decode_json(text)
    if parsed is not None:
        _collect_players(parsed, found)
    return found


def _collect_players(value, found: dict[str, str]) -> None:
    if isinstance(value, dict):
        player_id = value.get("player_id")
        name = next(
            (
                str(value[key]).strip()
                for key in ("name", "display_name", "player_name")
                if isinstance(value.get(key), str) and str(value.get(key)).strip()
            ),
            "",
        )
        if isinstance(player_id, str) and player_id.strip() and name:
            found[name.casefold()] = player_id.strip()
            found[player_id.strip().casefold()] = player_id.strip()
        for child in value.values():
            _collect_players(child, found)
        return
    if isinstance(value, list):
        for child in value:
            _collect_players(child, found)


def _decode_json(text: str):
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _object(value) -> dict | None:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return dict(parsed) if isinstance(parsed, dict) else None


def _slug(label: str) -> str:
    return "_".join(label.lower().split())
