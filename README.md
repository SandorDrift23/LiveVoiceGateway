# Live Voice Gateway

Live Voice Gateway is a small, full-duplex bridge between custom ESPHome voice
firmware, OpenAI `gpt-live-1`, and Home Assistant. It runs either as a Home
Assistant add-on on a Raspberry Pi or as a regular Python process.

The microphone remains active while the assistant speaks. When the user
interrupts, the gateway flushes queued speaker audio and lets GPT-Live respond
to the latest request. Home Assistant device control uses the official MCP
Server integration; current-information questions can use OpenAI web search.

This repository contains the complete gateway implementation and does not use
Pipecat or the older OpenAI Realtime API. It needs the companion
[LiveVoiceFirmware](https://github.com/SandorDrift23/LiveVoiceFirmware) on the
voice device.

> [!NOTE]
> This project was developed with substantial generative-AI assistance
> ("KI"), using Cursor and AI models for implementation, review, and
> documentation. The maintainer directed the work and validates it on the
> target hardware. AI assistance does not replace hardware testing.

## Hardware status

- **Home Assistant Voice PE:** current development and tested target.
- **ReSpeaker XVF3800:** experimental only. Its firmware has not yet been
  built or tested on ReSpeaker hardware.

Voice PE acoustic echo cancellation is not yet verified. Test at low volume,
preferably through the 3.5 mm output, before relying on barge-in with the
internal speaker.

## Install as a Home Assistant add-on

1. In Home Assistant, enable the **Model Context Protocol Server** integration.
2. Open **Settings → Add-ons → Add-on store → ⋮ → Repositories**.
3. Add `https://github.com/SandorDrift23/LiveVoiceGateway`.
4. Install **Live Voice Gateway** and enter your OpenAI API key.
5. Install the companion firmware and set its gateway URL to
   `ws://<home-assistant-ip>:8080/`.

The main configuration page contains identity, voice, expression, instructions,
location, web search, and **Conversation idle timeout**. Enable Home
Assistant's **Show unused optional configuration options** control for speaking
speed, units, MCP overrides, backend settings, and logging.

Leave the MCP URL and access token empty when running as a Home Assistant
add-on. It automatically uses `http://supervisor/core/api/mcp` and the
Supervisor token. The MCP Server integration still needs to be enabled.

See the add-on [documentation](live_voice_gateway/DOCS.md) for configuration
details.

## Run as a Python process

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
python -m live_voice_gateway
```

Edit `.env` before starting. When the gateway runs outside Home Assistant,
`HA_MCP_URL` must be reachable from that computer and `HA_ACCESS_TOKEN` must be
a Home Assistant long-lived access token. Never commit `.env`.

The device WebSocket listens on `ws://0.0.0.0:8080/` by default.

## How it works

```text
Voice PE / ReSpeaker ── PCM16 WebSocket ── Live Voice Gateway
                                                   ├── OpenAI GPT-Live
                                                   ├── OpenAI web search
                                                   └── Home Assistant MCP
```

The gateway sends and receives little-endian mono PCM16. It resamples the
device's advertised input and output rates to the 24 kHz GPT-Live stream.
Text control frames carry wake, interrupt, playback flush, and conversation
phase messages.

Example device hello:

```json
{"type":"hello","protocol":1,"device":"voice_pe","input_rate":16000,"output_rate":24000,"duplex":true,"aec_verified":false}
```

`duplex` must be true. `aec_verified` reports hardware validation status; it
does not disable the full-duplex path.

## Development

Run the tests with:

```powershell
python -m unittest discover -s tests
```

The gateway intentionally has only two runtime Python dependencies:
`websockets` and `python-dotenv`.
