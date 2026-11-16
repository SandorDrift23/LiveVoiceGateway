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
    "Hand a device request to the backend as soon as it is clear. Forward the "
    "user's words without adding or removing a room, place, or device name. "
    "Do not ask which device they mean before the backend answers. If the "
    "result lists several matches, ask one short question."
)
DEFAULT_WEB_SEARCH = (
    "Ask the backend for current facts such as weather, news, opening hours, "
    "and prices. If you already know the answer, say it directly. Speak the "
    "backend's result. Do not pretend to look something up."
)
DEFAULT_BACKEND = (
    "For a device request, call the one control function that performs the "
    "action, in the same step. Do not call GetLiveContext or any lookup first. "
    "Drop German articles (der, die, das, den, dem, des, ein, eine, einen) and "
    "the verb. Do not translate the remaining name. Set area only when the "
    "user names a room, such as 'im Schlafzimmer', and pass that room "
    "unchanged. Never set area or the device name from the home city, "
    "timezone, or weather location. If the user describes a kind of device at "
    "a spot, such as 'die Lampe am Sofa', pass the spot as the device name and "
    "the kind as the domain. If several devices match, return that list and "
    "do not guess. Use web search only for current external facts such as "
    "weather, news, opening hours, and prices. Never use web search for a "
    "device request or for an answer you already know."
)
DEFAULT_BACKEND_MODEL = "gpt-5.4-nano"
DEFAULT_MCP_BASE_URL = "http://supervisor/core/api/mcp"
DEFAULT_MA_MCP_URL = "http://127.0.0.1:8095/mcp/v1"

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
    voice: str = "juni"
    instructions: str = ""
    idle_seconds: float = 12.0
    web_search_idle_seconds: float = 45.0
    reconnect_grace_seconds: float = 8.0
    assistant_name: str = "Nabu"
    language: str = "de"
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
    enable_music_assistant: bool = False
    ma_mcp_url: str = DEFAULT_MA_MCP_URL
    ma_access_token: str = ""
    default_media_player: str = ""
    ma_tool_allowlist: tuple[str, ...] = ()
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
            voice=_api_name(_text("OPENAI_VOICE"), "juni"),
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
            assistant_name=_text("ASSISTANT_NAME") or "Nabu",
            language=_text("LANGUAGE") or "de",
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
            ha_mcp_url=_url(_text("HA_MCP_URL") or _mcp_url(_text("HA_MCP_API"))),
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
            enable_music_assistant=_bool("ENABLE_MUSIC_ASSISTANT", False),
            ma_mcp_url=_url(_text("MA_MCP_URL") or DEFAULT_MA_MCP_URL),
            ma_access_token=_text("MA_ACCESS_TOKEN"),
            default_media_player=_text("DEFAULT_MEDIA_PLAYER"),
            ma_tool_allowlist=_csv("MA_TOOL_ALLOWLIST"),
            log_transcripts=_bool("LOG_TRANSCRIPTS", True),
            log_tool_calls=_bool("LOG_TOOL_CALLS", True),
        )
        return cls(**{**settings.__dict__, "instructions": build_instructions(settings)})


def build_instructions(
    settings: Settings, *, home_tools: bool = False, music_tools: bool = False
) -> str:
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
            f"{settings.home_location}. Use it only for local questions such as "
            "weather and nearby places. Never use it as a Home Assistant area, "
            "room, or device name. Do not repeat the full location unless the "
            "user asks."
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
                "Say a device changed only after the tool result confirms it."
            )
        else:
            lines.append(
                "Home Assistant tools are not connected. If asked to control a "
                "device, say that you cannot do that yet. Never claim a light, "
                "switch, or other device changed."
            )
    if settings.enable_music_assistant:
        player = settings.default_media_player
        if music_tools and player:
            lines.append(
                "Hand a music or playback request to the backend immediately. "
                f"The default player is {player}. Do not ask which player "
                "unless the result lists several matches."
            )
        elif music_tools:
            lines.append(
                "Hand a music or playback request to the backend immediately. "
                "No default player is configured. If the user names no player, "
                "ask which one."
            )
        else:
            lines.append(
                "Music Assistant is not connected. If asked to play music, say "
                "that you cannot do that yet."
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
    if settings.enable_music_assistant:
        player = settings.default_media_player or "none"
        lines.append(
            "For music, radio, a playlist, an artist, pause, next, previous, "
            "stop, or volume, call one Music Assistant function in the same "
            "step. Do not use a Home Assistant turn-on tool or web search. "
            f"Leave player fields empty to use the default player ({player}). "
            "If the user names a player or room, pass that name unchanged and "
            "do not substitute the default. Do not translate titles, artists, "
            "or playlists. Return a short confirmation without ids or JSON."
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


def _url(value: str) -> str:
    return value.strip().rstrip("/")


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
