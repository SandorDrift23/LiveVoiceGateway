"""Device WebSocket messages for a full-duplex Live session.

Binary frames are little-endian PCM16 mono. Text frames are compact JSON.
The microphone stays open while the assistant speaks. ``flush`` drops only
the audio already buffered on the device.
"""

from typing import Any

PROTOCOL_VERSION = 1
LIVE_RATE = 24000


def loads_hello(message: dict[str, Any]) -> dict[str, Any]:
    """Validate a device hello and return the session description."""
    if message.get("type") != "hello":
        raise ValueError("expected hello")
    protocol = int(message.get("protocol", 0))
    if protocol != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol {protocol}")
    device = str(message.get("device") or "").strip()
    if not device:
        raise ValueError("device name is required")
    input_rate = int(message["input_rate"])
    output_rate = int(message["output_rate"])
    if input_rate <= 0 or output_rate <= 0:
        raise ValueError("audio rates must be positive")
    if message.get("duplex") is not True:
        raise ValueError("this gateway only accepts full-duplex devices")
    return {
        "device": device,
        "input_rate": input_rate,
        "output_rate": output_rate,
        "aec_verified": bool(message.get("aec_verified", False)),
    }


def server_hello() -> dict[str, Any]:
    return {
        "type": "hello",
        "protocol": PROTOCOL_VERSION,
        "duplex": True,
        "input_rate": LIVE_RATE,
        "output_rate": LIVE_RATE,
    }


def phase_message(phase: str) -> dict[str, str]:
    if phase not in {"listening", "speaking", "idle"}:
        raise ValueError(f"unknown phase {phase}")
    return {"type": "phase", "phase": phase}


def flush_message() -> dict[str, str]:
    return {"type": "flush"}
