# Live Voice Gateway

This add-on connects custom Live Voice firmware to OpenAI `gpt-live-1`. Audio
travels directly between the voice device, this add-on, and OpenAI. Smart-home
requests use Home Assistant's official MCP Server integration.

## Before starting

1. Enable the **Model Context Protocol Server** integration in Home Assistant.
   Enable **Control Home Assistant** and expose the entities that the voice
   assistant should be allowed to control.
2. Enter an OpenAI API key with API billing enabled.
3. Install the companion firmware from
   [LiveVoiceFirmware](https://github.com/SandorDrift23/LiveVoiceFirmware).
4. Point the firmware's `va_url` at
   `ws://<home-assistant-ip>:8080/`.

The stock Home Assistant Voice PE firmware does not implement this gateway's
audio protocol.

## Configuration

The configuration page is grouped into **Instructions**, **Basic settings**,
**Backend and web search**, **Home Assistant and device**, and **Music
Assistant**. Home Assistant shows each group as its own section, which can be
opened and closed. Voice options show their intended language and regional
influence. A web search uses the longer timeout, then the normal conversation
timeout starts again when the search finishes.

Instruction fields are still single-line. Use **Configuration → ⋮ → Edit in
YAML** when editing long text. An update from the older flat configuration
keeps the saved values and files them into these sections the first time the
add-on starts.

The custom MCP URL, access token, tool allow-list, and GPT-Live URL have no
default. Leave them empty when the add-on runs inside Home Assistant. If a
field is not on the page, enable **Show unused optional configuration
options**.

**Assist tools only** is the recommended MCP default. It selects
`/api/mcp/assist` and limits control to entities exposed to Assist. **All
configured APIs** selects `/api/mcp` and requires the corresponding MCP Server
configuration and administrator authorization.

When the add-on runs inside Home Assistant, leave **Custom Home Assistant MCP
URL** and **Home Assistant access token** empty. The add-on derives the
Supervisor endpoint from the dropdown and uses its Supervisor token. Only fill
those overrides when running the Python gateway on another computer.

Music Assistant is a separate MCP server. Enable the FastMCP Server plugin,
create a token in its connect wizard, and turn on playback control for that
token. Leave destructive confirmation off, because this gateway cannot answer
a confirmation prompt. Enable **Music Assistant control** and paste the token.
**Default media player** is optional; set a player name such as `Wohnzimmer`
when requests that name no player should use one. Leave **Music Assistant MCP
URL** empty to use `http://127.0.0.1:8095/mcp/v1`.

Changing the voice takes effect when a new GPT-Live conversation starts.

## Network

The add-on listens on TCP port `8080` using Home Assistant host networking.
Make sure that port is not used by another add-on or service.

## Privacy and cost

Microphone audio and instructions are sent to OpenAI. Home Assistant access
tokens remain in the add-on and are not sent to OpenAI. Transcript logging is
enabled by default and can be disabled under advanced settings.

OpenAI API usage is billed by OpenAI. Web search may create an additional
Responses API call.

## Hardware status

The Home Assistant Voice PE path is the currently tested target. ReSpeaker
XVF3800 support is experimental: its firmware has not yet been built or tested
on that hardware.
