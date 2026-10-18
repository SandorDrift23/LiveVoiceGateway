"""Run the full-duplex gpt-live-1 gateway."""

import asyncio
import logging
import sys

from live_voice_gateway.config import Settings
from live_voice_gateway.mcp_debug import HomeAssistantClient
from live_voice_gateway.server import serve


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    home = None
    if settings.enable_home_assistant:
        if settings.ha_access_token:
            home = HomeAssistantClient(
                settings.ha_mcp_url,
                settings.ha_access_token,
                settings.tool_timeout_seconds,
                settings.mcp_tool_allowlist,
            )
            home.connect()
        else:
            logging.getLogger(__name__).warning(
                "Home Assistant is enabled but HA_ACCESS_TOKEN is empty"
            )
    try:
        asyncio.run(serve(settings, home))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
