#!/usr/bin/with-contenv bashio
set -e

OPENAI_API_KEY=$(bashio::config 'openai_api_key')
if [ -z "${OPENAI_API_KEY}" ]; then
    bashio::log.error "OpenAI API key is required"
    exit 1
fi

export OPENAI_API_KEY
export ASSISTANT_NAME="$(bashio::config 'assistant_name')"
export LANGUAGE="$(bashio::config 'language')"
export OPENAI_VOICE="$(bashio::config 'openai_voice')"
export EXPRESSION="$(bashio::config 'expression')"
export HOME_LOCATION="$(bashio::config 'home_location')"
export TIMEZONE="$(bashio::config 'timezone')"
export REPLY_INSTRUCTIONS="$(bashio::config 'reply_instructions')"
export SMART_HOME_INSTRUCTIONS="$(bashio::config 'smart_home_instructions')"
export WEB_SEARCH_INSTRUCTIONS="$(bashio::config 'web_search_instructions')"
export EXTRA_INSTRUCTIONS="$(bashio::config 'extra_instructions')"
export BACKEND_INSTRUCTIONS="$(bashio::config 'backend_instructions')"
export CONVERSATION_IDLE_TIMEOUT="$(bashio::config 'conversation_idle_timeout')"
export ENABLE_WEB_SEARCH="$(bashio::config 'enable_web_search')"

export_if_set() {
    local key="$1"
    local environment_name="$2"
    if bashio::config.has_value "${key}"; then
        export "${environment_name}=$(bashio::config "${key}")"
    fi
}

export_if_set 'speaking_speed' 'SPEAKING_SPEED'
export_if_set 'unit_system' 'UNIT_SYSTEM'
export_if_set 'backend_model' 'BACKEND_MODEL'
export_if_set 'web_search_max_sentences' 'WEB_SEARCH_MAX_SENTENCES'
export_if_set 'reconnect_grace_seconds' 'RECONNECT_GRACE_SECONDS'
export_if_set 'enable_home_assistant' 'ENABLE_HOME_ASSISTANT'
export_if_set 'ha_mcp_url' 'HA_MCP_URL'
export_if_set 'mcp_tool_allowlist' 'MCP_TOOL_ALLOWLIST'
export_if_set 'mcp_request_timeout' 'MCP_REQUEST_TIMEOUT'
export_if_set 'live_url' 'LIVE_URL'
export_if_set 'websocket_port' 'WEBSOCKET_PORT'
export_if_set 'log_transcripts' 'LOG_TRANSCRIPTS'
export_if_set 'log_tool_calls' 'LOG_TOOL_CALLS'

if bashio::config.has_value 'ha_access_token'; then
    export HA_ACCESS_TOKEN="$(bashio::config 'ha_access_token')"
elif [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    export HA_ACCESS_TOKEN="${SUPERVISOR_TOKEN}"
fi

exec python3 -m live_voice_gateway
