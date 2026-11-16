#!/usr/bin/with-contenv bashio
set -e

# Prefer a saved flat option from 0.2.1 when both shapes are present. The
# nested value is the default Home Assistant inserted for the new sections.
read_pref() {
    local nested="$1"
    local legacy="$2"
    if bashio::config.exists "${legacy}"; then
        bashio::config "${legacy}"
    else
        bashio::config "${nested}"
    fi
}

read_optional() {
    local nested="$1"
    local legacy="$2"
    if bashio::config.exists "${legacy}"; then
        bashio::config "${legacy}"
    elif bashio::config.exists "${nested}"; then
        bashio::config "${nested}"
    fi
}

OPENAI_API_KEY="$(bashio::config 'openai_api_key')"
ASSISTANT_NAME="$(read_pref 'basic.assistant_name' 'assistant_name')"
LANGUAGE="$(read_pref 'basic.language' 'language')"
OPENAI_VOICE="$(read_pref 'basic.openai_voice' 'openai_voice')"
EXPRESSION="$(read_pref 'basic.expression' 'expression')"
HOME_LOCATION="$(read_pref 'basic.home_location' 'home_location')"
TIMEZONE="$(read_pref 'basic.timezone' 'timezone')"
SPEAKING_SPEED="$(read_pref 'basic.speaking_speed' 'speaking_speed')"
UNIT_SYSTEM="$(read_pref 'basic.unit_system' 'unit_system')"
CONVERSATION_IDLE_TIMEOUT="$(read_pref 'basic.conversation_idle_timeout' 'conversation_idle_timeout')"
RECONNECT_GRACE_SECONDS="$(read_pref 'basic.reconnect_grace_seconds' 'reconnect_grace_seconds')"
REPLY_INSTRUCTIONS="$(read_pref 'instructions.reply_instructions' 'reply_instructions')"
SMART_HOME_INSTRUCTIONS="$(read_pref 'instructions.smart_home_instructions' 'smart_home_instructions')"
WEB_SEARCH_INSTRUCTIONS="$(read_pref 'instructions.web_search_instructions' 'web_search_instructions')"
EXTRA_INSTRUCTIONS="$(read_pref 'instructions.extra_instructions' 'extra_instructions')"
BACKEND_INSTRUCTIONS="$(read_pref 'instructions.backend_instructions' 'backend_instructions')"
ENABLE_WEB_SEARCH="$(read_pref 'web_search.enable_web_search' 'enable_web_search')"
WEB_SEARCH_IDLE_TIMEOUT="$(read_pref 'web_search.web_search_idle_timeout' 'web_search_idle_timeout')"
BACKEND_MODEL="$(read_pref 'web_search.backend_model' 'backend_model')"
WEB_SEARCH_MAX_SENTENCES="$(read_pref 'web_search.web_search_max_sentences' 'web_search_max_sentences')"
LIVE_URL="$(read_optional 'web_search.live_url' 'live_url')"
ENABLE_HOME_ASSISTANT="$(read_pref 'home_assistant.enable_home_assistant' 'enable_home_assistant')"
HA_MCP_API="$(read_pref 'home_assistant.ha_mcp_api' 'ha_mcp_api')"
MCP_REQUEST_TIMEOUT="$(read_pref 'home_assistant.mcp_request_timeout' 'mcp_request_timeout')"
HA_MCP_URL="$(read_optional 'home_assistant.ha_mcp_url' 'ha_mcp_url')"
HA_ACCESS_TOKEN="$(read_optional 'home_assistant.ha_access_token' 'ha_access_token')"
MCP_TOOL_ALLOWLIST="$(read_optional 'home_assistant.mcp_tool_allowlist' 'mcp_tool_allowlist')"
WEBSOCKET_PORT="$(read_pref 'home_assistant.websocket_port' 'websocket_port')"
LOG_TRANSCRIPTS="$(read_pref 'home_assistant.log_transcripts' 'log_transcripts')"
LOG_TOOL_CALLS="$(read_pref 'home_assistant.log_tool_calls' 'log_tool_calls')"
ENABLE_MUSIC_ASSISTANT="$(read_optional 'music_assistant.enable_music_assistant' 'enable_music_assistant')"
MA_MCP_URL="$(read_optional 'music_assistant.ma_mcp_url' 'ma_mcp_url')"
MA_ACCESS_TOKEN="$(read_optional 'music_assistant.ma_access_token' 'ma_access_token')"
DEFAULT_MEDIA_PLAYER="$(read_optional 'music_assistant.default_media_player' 'default_media_player')"
MA_TOOL_ALLOWLIST="$(read_optional 'music_assistant.ma_tool_allowlist' 'ma_tool_allowlist')"

# File the previous flat options into the sections so the page shows the
# values this process is about to use. A failure here does not block startup.
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    python3 - <<'PY' || bashio::log.warning "Could not move the previous configuration into sections"
import json
import os
import sys
import urllib.error
import urllib.request

LEGACY = {
    "reply_instructions": "instructions",
    "smart_home_instructions": "instructions",
    "web_search_instructions": "instructions",
    "extra_instructions": "instructions",
    "backend_instructions": "instructions",
    "assistant_name": "basic",
    "language": "basic",
    "openai_voice": "basic",
    "expression": "basic",
    "home_location": "basic",
    "timezone": "basic",
    "speaking_speed": "basic",
    "unit_system": "basic",
    "conversation_idle_timeout": "basic",
    "reconnect_grace_seconds": "basic",
    "enable_web_search": "web_search",
    "web_search_idle_timeout": "web_search",
    "backend_model": "web_search",
    "web_search_max_sentences": "web_search",
    "live_url": "web_search",
    "enable_home_assistant": "home_assistant",
    "ha_mcp_api": "home_assistant",
    "mcp_request_timeout": "home_assistant",
    "ha_mcp_url": "home_assistant",
    "ha_access_token": "home_assistant",
    "mcp_tool_allowlist": "home_assistant",
    "websocket_port": "home_assistant",
    "log_transcripts": "home_assistant",
    "log_tool_calls": "home_assistant",
}
GROUPS = ("instructions", "basic", "web_search", "home_assistant")


def supervisor(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://supervisor{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if isinstance(body, dict) and body.get("result") not in (None, "ok"):
        raise RuntimeError(body.get("message") or "Supervisor request failed")
    return body.get("data", body) if isinstance(body, dict) else body


def saved_options():
    data = supervisor("GET", "/addons/self/options/config")
    if isinstance(data, dict) and isinstance(data.get("options"), dict):
        if "openai_api_key" not in data and "instructions" not in data:
            return data["options"]
    if not isinstance(data, dict):
        raise RuntimeError("Supervisor returned no configuration")
    return data


try:
    current = saved_options()
except Exception as exc:
    print(f"Configuration migration skipped: {exc}", file=sys.stderr)
    sys.exit(0)

if not any(key in current for key in LEGACY):
    sys.exit(0)

grouped = {}
for name in GROUPS:
    section = current.get(name)
    grouped[name] = dict(section) if isinstance(section, dict) else {}
    for key, destination in LEGACY.items():
        if destination == name and key in current:
            grouped[name][key] = current[key]

if any(not isinstance(current.get(name), dict) for name in GROUPS):
    print(
        "Configuration sections are not saved yet; keeping the previous values for this start.",
        file=sys.stderr,
    )
    sys.exit(0)

try:
    supervisor(
        "POST",
        "/addons/self/options",
        {"options": {"openai_api_key": current.get("openai_api_key", ""), **grouped}},
    )
except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
    print(f"Could not save grouped configuration: {exc}", file=sys.stderr)
    sys.exit(0)

print("Moved the previous configuration into page sections.", file=sys.stderr)
PY
fi

if [ -z "${OPENAI_API_KEY}" ] || [ "${OPENAI_API_KEY}" = "null" ]; then
    bashio::log.error "OpenAI API key is required"
    exit 1
fi

export_value() {
    local environment_name="$1"
    local value="$2"
    if [ "${value}" != "null" ]; then
        export "${environment_name}=${value}"
    fi
}

export OPENAI_API_KEY
export_value ASSISTANT_NAME "${ASSISTANT_NAME}"
export_value LANGUAGE "${LANGUAGE}"
export_value OPENAI_VOICE "${OPENAI_VOICE}"
export_value EXPRESSION "${EXPRESSION}"
export_value HOME_LOCATION "${HOME_LOCATION}"
export_value TIMEZONE "${TIMEZONE}"
export_value SPEAKING_SPEED "${SPEAKING_SPEED}"
export_value UNIT_SYSTEM "${UNIT_SYSTEM}"
export_value CONVERSATION_IDLE_TIMEOUT "${CONVERSATION_IDLE_TIMEOUT}"
export_value RECONNECT_GRACE_SECONDS "${RECONNECT_GRACE_SECONDS}"
export_value REPLY_INSTRUCTIONS "${REPLY_INSTRUCTIONS}"
export_value SMART_HOME_INSTRUCTIONS "${SMART_HOME_INSTRUCTIONS}"
export_value WEB_SEARCH_INSTRUCTIONS "${WEB_SEARCH_INSTRUCTIONS}"
export_value EXTRA_INSTRUCTIONS "${EXTRA_INSTRUCTIONS}"
export_value BACKEND_INSTRUCTIONS "${BACKEND_INSTRUCTIONS}"
export_value ENABLE_WEB_SEARCH "${ENABLE_WEB_SEARCH}"
export_value WEB_SEARCH_IDLE_TIMEOUT "${WEB_SEARCH_IDLE_TIMEOUT}"
export_value BACKEND_MODEL "${BACKEND_MODEL}"
export_value WEB_SEARCH_MAX_SENTENCES "${WEB_SEARCH_MAX_SENTENCES}"
export_value ENABLE_HOME_ASSISTANT "${ENABLE_HOME_ASSISTANT}"
export_value HA_MCP_API "${HA_MCP_API}"
export_value MCP_REQUEST_TIMEOUT "${MCP_REQUEST_TIMEOUT}"
export_value WEBSOCKET_PORT "${WEBSOCKET_PORT}"
export_value LOG_TRANSCRIPTS "${LOG_TRANSCRIPTS}"
export_value LOG_TOOL_CALLS "${LOG_TOOL_CALLS}"
export_value ENABLE_MUSIC_ASSISTANT "${ENABLE_MUSIC_ASSISTANT}"
export_value DEFAULT_MEDIA_PLAYER "${DEFAULT_MEDIA_PLAYER}"

export_if_present() {
    local environment_name="$1"
    local value="$2"
    if [ -n "${value}" ] && [ "${value}" != "null" ]; then
        export "${environment_name}=${value}"
    fi
}

export_if_present LIVE_URL "${LIVE_URL}"
export_if_present HA_MCP_URL "${HA_MCP_URL}"
export_if_present MCP_TOOL_ALLOWLIST "${MCP_TOOL_ALLOWLIST}"
export_if_present MA_MCP_URL "${MA_MCP_URL}"
export_if_present MA_ACCESS_TOKEN "${MA_ACCESS_TOKEN}"
export_if_present MA_TOOL_ALLOWLIST "${MA_TOOL_ALLOWLIST}"

if [ -n "${HA_ACCESS_TOKEN}" ] && [ "${HA_ACCESS_TOKEN}" != "null" ]; then
    export HA_ACCESS_TOKEN
elif [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    export HA_ACCESS_TOKEN="${SUPERVISOR_TOKEN}"
fi

exec python3 -m live_voice_gateway
