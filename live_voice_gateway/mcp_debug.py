"""Local Home Assistant MCP client.

The gateway calls this endpoint on the LAN. The private URL and token are
never sent to OpenAI. Advertised tools are exposed as client-executed
functions on the Live session.
"""

import json
import logging
import threading
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class HomeAssistantClient:
    def __init__(
        self,
        url: str,
        token: str,
        timeout: float,
        allowlist: tuple[str, ...] = (),
    ):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.allowlist = {name.strip() for name in allowlist if name.strip()}
        self._session_id = ""
        self._tools: list[dict] = []
        self._lock = threading.Lock()
        self._rpc_id = 2

    def connect(self) -> bool:
        with self._lock:
            return self._connect_locked()

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
            if not self._tools and not self._connect_locked():
                return "Home Assistant is not connected."
            status, output = self._call_locked(name, arguments)
            if status != 200:
                self._session_id = ""
                self._tools = []
            return output

    def _connect_locked(self) -> bool:
        logger.info("Home Assistant MCP connect: %s", self.url)
        status, headers, body = _post(
            self.url, self.token, _initialize_body(), self.timeout, ""
        )
        logger.info("MCP initialize HTTP %s", status)
        if status != 200:
            _log_body("initialize", body)
            logger.warning("Home Assistant MCP is not usable")
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
            logger.info("MCP tools attached: %s", ", ".join(names))
            return True
        _log_body("tools/list", body)
        logger.warning("MCP tools/list returned no tool names")
        return False

    def _call_locked(self, name: str, arguments: str) -> tuple[int, str]:
        try:
            payload = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
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
            return status, f"Home Assistant error: HTTP {status}"
        return status, _result_text(response)


def _initialize_body() -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "live-voice-gateway", "version": "0.1.0"},
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
        logger.warning("Home Assistant MCP request failed: %s", exc)
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


def _result_text(body: str) -> str:
    payloads = _json_objects(body)
    if not payloads:
        compact = " ".join(body.split())
        return compact[:1000] or "Empty Home Assistant response"
    payload = payloads[-1]
    if payload.get("error"):
        error = payload["error"]
        message = error.get("message", error) if isinstance(error, dict) else error
        return f"Home Assistant error: {message}"
    result = payload.get("result") or {}
    text = _content_text(result) or "The tool finished without a message."
    if result.get("isError"):
        return f"Home Assistant error: {text}"
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
