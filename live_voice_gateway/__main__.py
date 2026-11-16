"""Run the full-duplex gpt-live-1 gateway."""

import asyncio
import logging
import sys

from live_voice_gateway.config import Settings
from live_voice_gateway.mcp_debug import HomeAssistantClient, MusicAssistantClient, ToolRouter
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
    clients = []
    if settings.enable_home_assistant:
        if settings.ha_access_token:
            home = HomeAssistantClient(
                settings.ha_mcp_url,
                settings.ha_access_token,
                settings.tool_timeout_seconds,
                settings.mcp_tool_allowlist,
            )
            home.connect()
            clients.append(home)
        else:
            logging.getLogger(__name__).warning(
                "Home Assistant is enabled but HA_ACCESS_TOKEN is empty"
            )
    if settings.enable_music_assistant:
        if settings.ma_access_token:
            music = MusicAssistantClient(
                settings.ma_mcp_url,
                settings.ma_access_token,
                settings.tool_timeout_seconds,
                settings.ma_tool_allowlist,
                settings.default_media_player,
            )
            music.connect()
            clients.append(music)
        else:
            logging.getLogger(__name__).warning(
                "Music Assistant is enabled but MA_ACCESS_TOKEN is empty"
            )
    try:
        asyncio.run(serve(settings, ToolRouter(clients) if clients else None))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
