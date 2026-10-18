"""Environment configuration for the local gateway.

The same names are what a Home Assistant app options page should map into
the process environment. Tool flags are loaded now; web search and Home
Assistant calls use them when those features run.
"""

import os
from dataclasses import dataclass

import dotenv

DEFAULT_EXPRESSION = "Warm, friendly, and natural."
DEFAULT_REPLY = (
    "Keep answers short and natural. Usually use one or two sentences. "
    "Do not use markdown, URLs, or entity IDs."
)
DEFAULT_SMART_HOME = (
    "Carry out a clear smart-home request immediately. If the device or room "
    "is ambiguous, ask one short question."
)
DEFAULT_WEB_SEARCH = (
    "Use web search only for current facts such as weather, news, opening "
    "hours, and prices. Do not search for a smart-home request or for "
    "something you can answer directly. Speak the result. Do not pretend "
    "to look something up."
)
DEFAULT_BACKEND = (
    "Use web search only when the answer depends on current external "
    "information. Never use web search for a smart-home request. For a "
    "device request, call one Home Assistant function and return only what "
    "that tool result says. Do not claim a device changed unless the tool "
    "result confirms it. Answer from general knowledge when no tool is needed."
)
DEFAULT_BACKEND_MODEL = "gpt-5.4-nano"
DEFAULT_MCP_BASE_URL = "http://supervisor/core/api/mcp"

_SPEEDS = {
    "slow": "Speak at a relaxed pace.",
    "normal": "Speak at a natural conversational pace.",
    "fast": "Speak a little faster than usual, while staying clear.",
}

_LANGUAGES = {
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "hi": "Hindi",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "pt": "Portuguese",
}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    live_url: str = "wss://api.openai.com/v1/live/sessions"
    host: str = "0.0.0.0"
    port: int = 8080
    voice: str = "marin"
    instructions: str = ""
    idle_seconds: float = 12.0
    web_search_idle_seconds: float = 45.0
    reconnect_grace_seconds: float = 8.0
    assistant_name: str = "Live"
    language: str = "en"
    expression: str = DEFAULT_EXPRESSION
    speaking_speed: str = "normal"
    home_location: str = ""
    timezone: str = "Europe/Berlin"
    unit_system: str = "metric"
    reply_instructions: str = DEFAULT_REPLY
    smart_home_instructions: str = DEFAULT_SMART_HOME
    web_search_instructions: str = DEFAULT_WEB_SEARCH
    extra_instructions: str = ""
    enable_web_search: bool = True
    backend_model: str = DEFAULT_BACKEND_MODEL
    backend_instructions: str = DEFAULT_BACKEND
    web_search_max_sentences: int = 2
    enable_home_assistant: bool = True
    ha_mcp_url: str = f"{DEFAULT_MCP_BASE_URL}/assist"
    ha_access_token: str = ""
    mcp_tool_allowlist: tuple[str, ...] = ()
    tool_timeout_seconds: float = 20.0
    log_transcripts: bool = True
    log_tool_calls: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        root = os.path.dirname(os.path.dirname(__file__))
        dotenv.load_dotenv(os.path.join(root, ".env"))
        dotenv.load_dotenv()
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        extra = _text("EXTRA_INSTRUCTIONS")
        legacy = _text("INSTRUCTIONS")
        settings = cls(
            openai_api_key=api_key,
            live_url=_text("LIVE_URL") or "wss://api.openai.com/v1/live/sessions",
            host=_text("WEBSOCKET_HOST") or "0.0.0.0",
            port=_int("WEBSOCKET_PORT", 8080, minimum=1, maximum=65535),
            voice=_api_name(_text("OPENAI_VOICE"), "marin"),
            idle_seconds=_float_alias(
                ("CONVERSATION_IDLE_TIMEOUT", "IDLE_SECONDS"),
                12.0,
                minimum=2.0,
                maximum=120.0,
            ),
            web_search_idle_seconds=_float(
                "WEB_SEARCH_IDLE_TIMEOUT", 45.0, minimum=2.0, maximum=180.0
            ),
            reconnect_grace_seconds=_float(
                "RECONNECT_GRACE_SECONDS", 8.0, minimum=1.0, maximum=60.0
            ),
            assistant_name=_text("ASSISTANT_NAME") or "Live",
            language=_text("LANGUAGE") or "en",
            expression=_text("EXPRESSION") or DEFAULT_EXPRESSION,
            speaking_speed=_text("SPEAKING_SPEED") or "normal",
            home_location=_text("HOME_LOCATION"),
            timezone=_text("TIMEZONE") or "Europe/Berlin",
            unit_system=_text("UNIT_SYSTEM") or "metric",
            reply_instructions=_text("REPLY_INSTRUCTIONS") or DEFAULT_REPLY,
            smart_home_instructions=_text("SMART_HOME_INSTRUCTIONS") or DEFAULT_SMART_HOME,
            web_search_instructions=_text("WEB_SEARCH_INSTRUCTIONS") or DEFAULT_WEB_SEARCH,
            extra_instructions=extra or legacy,
            enable_web_search=_bool("ENABLE_WEB_SEARCH", True),
            backend_model=_api_name(
                _text("BACKEND_MODEL") or _text("WEB_SEARCH_MODEL"),
                DEFAULT_BACKEND_MODEL,
            ),
            backend_instructions=_text("BACKEND_INSTRUCTIONS") or DEFAULT_BACKEND,
            web_search_max_sentences=_int(
                "WEB_SEARCH_MAX_SENTENCES", 2, minimum=1, maximum=5
            ),
            enable_home_assistant=_bool("ENABLE_HOME_ASSISTANT", True),
            ha_mcp_url=_text("HA_MCP_URL") or _mcp_url(_text("HA_MCP_API")),
            ha_access_token=(
                _text("HA_ACCESS_TOKEN") or _text("SUPERVISOR_TOKEN")
            ),
            mcp_tool_allowlist=_csv("MCP_TOOL_ALLOWLIST"),
            tool_timeout_seconds=_float_alias(
                ("MCP_REQUEST_TIMEOUT", "TOOL_TIMEOUT_SECONDS"),
                20.0,
                minimum=1.0,
                maximum=120.0,
            ),
            log_transcripts=_bool("LOG_TRANSCRIPTS", True),
            log_tool_calls=_bool("LOG_TOOL_CALLS", True),
        )
        return cls(**{**settings.__dict__, "instructions": build_instructions(settings)})


def build_instructions(settings: Settings, *, home_tools: bool = False) -> str:
    """Join the preference fields into the single prompt gpt-live-1 accepts."""
    lines = [f"You are {settings.assistant_name}, a voice assistant."]
    if settings.language:
        language = _LANGUAGES.get(settings.language.lower(), settings.language)
        lines.append(f"Speak in {language}.")
    if settings.expression:
        lines.append(f"Expression: {settings.expression}")
    speed = settings.speaking_speed.strip().lower()
    lines.append(_SPEEDS.get(speed, f"Speaking pace: {settings.speaking_speed.strip()}"))
    if settings.home_location:
        lines.append(
            "The user's home location is: "
            f"{settings.home_location}. Use it for local questions such as "
            "weather and nearby places. Do not repeat the full location unless "
            "the user asks."
        )
    if settings.timezone:
        lines.append(f"Timezone: {settings.timezone}.")
    if settings.unit_system:
        lines.append(f"Use {settings.unit_system} units.")
    if settings.reply_instructions:
        lines.append(settings.reply_instructions)
    if settings.enable_home_assistant and settings.smart_home_instructions:
        lines.append(settings.smart_home_instructions)
        if home_tools:
            lines.append(
                "Delegate a smart-home request to the backend tools. Speak a "
                "device change only after the tool result confirms it."
            )
        else:
            lines.append(
                "Home Assistant tools are not connected. If asked to control a "
                "device, say that you cannot do that yet. Never claim a light, "
                "switch, or other device changed."
            )
    if settings.enable_web_search and settings.web_search_instructions:
        lines.append(settings.web_search_instructions)
    if settings.extra_instructions:
        lines.append(settings.extra_instructions)
    return "\n".join(lines)


def backend_prompt(settings: Settings) -> str:
    """Instructions for the delegated Responses model. Editable from config."""
    lines = []
    if settings.backend_instructions:
        lines.append(settings.backend_instructions)
    if settings.enable_web_search:
        lines.append(
            "Keep a web-search answer to "
            f"{settings.web_search_max_sentences} sentences."
        )
    else:
        lines.append(
            "Web search is disabled. Do not call it. Use Home Assistant "
            "functions for device requests."
        )
    return " ".join(lines)


def _text(name: str) -> str:
    return os.environ.get(name, "").strip()


def _voice(value: str) -> str:
    """Accept a descriptive add-on label while sending only the API name."""
    return _api_name(value, "marin")


def _api_name(value: str, default: str) -> str:
    """Keep the API identifier from a dropdown label such as 'name - detail'."""
    return value.split(maxsplit=1)[0] if value else default


def _mcp_url(api: str) -> str:
    """Select the Supervisor MCP endpoint exposed by Home Assistant."""
    if api.strip().lower() == "all":
        return DEFAULT_MCP_BASE_URL
    return f"{DEFAULT_MCP_BASE_URL}/assist"


def _csv(name: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in os.environ.get(name, "").split(",") if part.strip())


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _float_alias(
    names: tuple[str, ...], default: float, *, minimum: float, maximum: float
) -> float:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and raw.strip():
            return _float(name, default, minimum=minimum, maximum=maximum)
    return default


def _float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
