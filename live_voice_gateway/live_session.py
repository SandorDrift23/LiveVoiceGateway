"""gpt-live-1 session behavior for full-duplex audio.

Microphone audio is forwarded while the assistant is speaking. User speech
during playback flushes the device speaker buffer. A physical stop or the
device ``interrupt`` message drops the rest of the current reply until the
user speaks again.
"""

import base64
import json
import time
from dataclasses import dataclass, field

LIVE_MODEL = "gpt-live-1"
LIVE_RATE = 24000

FULL_DUPLEX_SUFFIX = (
    " You are in a full-duplex live voice session. The user can speak while "
    "you are speaking. Stop as soon as they interrupt, then answer only their "
    "latest request. Keep every reply short enough to say aloud."
)


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class LiveEffect:
    audio: bytes = b""
    flush: bool = False
    phase: str = ""
    closed: bool = False
    end_session: bool = False
    log: str = ""
    transcript: bool = False
    tool: bool = False
    tool_calls: tuple[ToolCall, ...] = ()
    outbound: tuple[dict, ...] = field(default_factory=tuple)


class LiveSession:
    """Pure protocol state. The socket owner performs the actual sends."""

    def __init__(
        self,
        *,
        voice: str,
        instructions: str,
        web_search_model: str = "",
        include_web_search: bool = True,
        function_tools: list | None = None,
        backend_instructions: str = "",
    ):
        self.voice = voice or "marin"
        self.instructions = (instructions or "").strip() + FULL_DUPLEX_SUFFIX
        self.web_search_model = web_search_model.strip()
        self.include_web_search = include_web_search
        self.backend_instructions = backend_instructions.strip()
        self.function_tools = list(function_tools or [])
        self.playing = False
        self.drop_output = False
        self._event_number = 0
        self._odd = b""
        self._barge_latched = False
        self._last_user_at = 0.0
        self._calls: dict[str, str] = {}
        self._sent: set[str] = set()
        self._round_finished = False
        self._continued = False

    def start_event(self) -> dict:
        event = {
            "type": "session.start",
            "event_id": self._event_id(),
            "session": {
                "model": LIVE_MODEL,
                "instructions": self.instructions,
                "audio": {
                    "format": {"type": "audio/pcm", "rate": LIVE_RATE},
                    "output": {"voice": self.voice},
                },
            },
        }
        tools: list[dict] = []
        if self.web_search_model and self.include_web_search:
            tools.append({"type": "web_search"})
        tools.extend(self.function_tools)
        if self.web_search_model and tools:
            backend: dict = {
                "model": self.web_search_model,
                "tools": tools,
                "tool_choice": "auto",
            }
            if self.backend_instructions:
                backend["instructions"] = self.backend_instructions
            event["session"]["delegation"] = {
                "type": "responses",
                "responses": backend,
            }
        return event

    def append_audio(self, pcm: bytes) -> dict | None:
        """Forward microphone audio whether or not the assistant is speaking."""
        if not pcm:
            return None
        pending = self._odd + pcm
        even = len(pending) - (len(pending) % 2)
        self._odd = pending[even:]
        if even == 0:
            return None
        return {
            "type": "session.input_audio.append",
            "event_id": self._event_id(),
            "audio": base64.b64encode(pending[:even]).decode("ascii"),
        }

    def handle_server_event(self, event: dict) -> LiveEffect:
        kind = str(event.get("type") or "")
        if kind == "session.output_audio.delta":
            return self._output_audio(str(event.get("delta") or ""))
        if kind == "session.input_transcript.delta":
            return self._user_transcript(str(event.get("delta") or ""))
        if kind == "session.output_transcript.delta":
            text = str(event.get("delta") or "")
            return LiveEffect(
                log=f"assistant: {text}" if text else "",
                transcript=bool(text),
            )
        if kind == "session.closed":
            self.playing = False
            return LiveEffect(phase="idle", closed=True)
        if kind == "response.event":
            return self._response_event(event)
        if kind == "error":
            return LiveEffect(log=f"live error: {event.get('error')}")
        return LiveEffect()

    def interrupt(self) -> LiveEffect:
        """Drop the reply already queued after a device stop or button press."""
        self.playing = False
        self.drop_output = True
        return LiveEffect(flush=True, phase="listening")

    def _output_audio(self, delta_b64: str) -> LiveEffect:
        if self.drop_output or not delta_b64:
            return LiveEffect()
        try:
            pcm = base64.b64decode(delta_b64)
        except (ValueError, TypeError):
            return LiveEffect(log="dropped invalid output audio")
        if not pcm:
            return LiveEffect()
        # Transcript deltas for one sentence arrive between leftover audio
        # frames. Marking each frame as playback made every following word
        # flush the speaker again.
        if self._barge_latched and (time.monotonic() - self._last_user_at) < 0.8:
            return LiveEffect(audio=pcm)
        self._barge_latched = False
        self.playing = True
        return LiveEffect(audio=pcm, phase="speaking")

    def _user_transcript(self, delta: str) -> LiveEffect:
        if not delta.strip():
            return LiveEffect()
        self._last_user_at = time.monotonic()
        was_playing = self.playing
        self.playing = False
        if self.drop_output:
            self.drop_output = False
            self._barge_latched = False
            return LiveEffect(log=f"user: {delta}", phase="listening", transcript=True)
        if was_playing and not self._barge_latched:
            self._barge_latched = True
            return LiveEffect(
                flush=True,
                phase="listening",
                log=f"user interrupted: {delta}",
                transcript=True,
            )
        self._barge_latched = True
        return LiveEffect(log=f"user: {delta}", phase="listening", transcript=True)

    def result_events(self, call_id: str, output: str) -> list[dict]:
        """Return the Live events that publish one local tool result."""
        if call_id not in self._calls:
            return []
        self._sent.add(call_id)
        events = [
            {
                "type": "response.item.create",
                "event_id": self._event_id(),
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        ]
        events.extend(self._continuation())
        return events

    def _response_event(self, envelope: dict) -> LiveEffect:
        nested = envelope.get("event") or {}
        kind = str(nested.get("type") or "")
        if kind == "response.created":
            if not self._calls or self._sent == set(self._calls):
                self._calls = {}
                self._sent = set()
                self._round_finished = False
                self._continued = False
            return LiveEffect(log=f"backend: {kind}")
        if kind == "response.output_item.done":
            call = _function_call(nested.get("item") or {})
            if call is None:
                return LiveEffect()
            self._calls[call.call_id] = call.name
            return LiveEffect(
                tool=True,
                tool_calls=(call,),
                log=f"tool call: {call.name} {call.arguments}",
            )
        if kind == "response.completed":
            self._round_finished = True
            return LiveEffect(
                log=f"backend: {kind}",
                outbound=tuple(self._continuation()),
            )
        if "search" in kind:
            return LiveEffect(log=f"web search: {kind}")
        return LiveEffect()

    def _continuation(self) -> list[dict]:
        if self._continued or not self._round_finished or not self._calls:
            return []
        if self._sent != set(self._calls):
            return []
        self._continued = True
        return [{"type": "response.create", "event_id": self._event_id()}]

    def _event_id(self) -> str:
        self._event_number += 1
        return f"evt-{self._event_number}"


def _function_call(item: dict) -> ToolCall | None:
    if item.get("type") != "function_call":
        return None
    call_id = str(item.get("call_id") or "").strip()
    name = str(item.get("name") or "").strip()
    if not call_id or not name:
        return None
    arguments = item.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments or {})
    return ToolCall(call_id=call_id, name=name, arguments=arguments)
