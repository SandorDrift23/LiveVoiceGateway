"""WebSocket server between a full-duplex device and gpt-live-1."""

import asyncio
import json
import logging
import time

import websockets

from live_voice_gateway.config import Settings, backend_prompt, build_instructions
from live_voice_gateway.live_session import LIVE_RATE, LiveSession
from live_voice_gateway.mcp_debug import HomeAssistantClient
from live_voice_gateway.protocol import (
    flush_message,
    loads_hello,
    phase_message,
    server_hello,
)
from live_voice_gateway.resample import resample_pcm16

logger = logging.getLogger(__name__)


def _ignore_dead_windows_socket(loop, context) -> None:
    """Windows reports a second shutdown of an already-closed socket as an error."""
    exc = context.get("exception")
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10022:
        return
    loop.default_exception_handler(context)


async def _close_socket(socket) -> None:
    if socket is None:
        return
    try:
        await socket.close()
    except (OSError, websockets.exceptions.ConnectionClosed):
        return

# A Voice PE blip reconnects in about a second. Keep the OpenAI session
# long enough for that hello to arrive and continue the same conversation.
PARK_SECONDS = 8.0
_parked: dict[str, "LiveLink"] = {}
_park_lock = asyncio.Lock()


class LiveLink:
    """One OpenAI session. The device socket can be swapped after a reconnect."""

    def __init__(self, socket, device: dict, settings: Settings):
        self.socket = socket
        self.device = device
        self.settings = settings
        self.send_lock = asyncio.Lock()
        self.session: LiveSession | None = None
        self.live = None
        self.reader: asyncio.Task | None = None
        self.idle_task: asyncio.Task | None = None
        self.generation = 0
        self.opening = False
        self.device_up = True
        self.close_started = False
        self.last_activity = time.monotonic()
        self.tools_in_flight = 0
        self.parked_at: float | None = None
        self.home: HomeAssistantClient | None = None
        self.tool_lock = asyncio.Lock()
        self.live_send_lock = asyncio.Lock()
        # Microphone audio is continuous. Keep only a short queue so a slow
        # OpenAI send cannot stop the device socket being read.
        self.uplink: asyncio.Queue | None = None
        self.uplink_task: asyncio.Task | None = None


def peer_ip(socket) -> str:
    peer = getattr(socket, "remote_address", None)
    if isinstance(peer, tuple) and peer:
        return str(peer[0])
    return ""


def take_parked(ip: str, now: float, ttl: float = PARK_SECONDS) -> LiveLink | None:
    """Claim a session parked for this address, if it is still inside the window."""
    link = _parked.get(ip)
    if link is None or link.parked_at is None or link.session is None:
        return None
    if now - link.parked_at > ttl:
        return None
    _parked.pop(ip, None)
    link.parked_at = None
    return link


def _drop_parked(link: LiveLink) -> None:
    for ip, parked in list(_parked.items()):
        if parked is link:
            _parked.pop(ip, None)


def _begin_close(link: LiveLink) -> bool:
    if link.close_started:
        return False
    link.close_started = True
    link.generation += 1
    link.device_up = False
    return True


async def _send_text(link: LiveLink, text: str) -> None:
    if not link.device_up or link.socket is None:
        return
    try:
        async with link.send_lock:
            if not link.device_up or link.socket is None:
                return
            await link.socket.send(text)
    except websockets.exceptions.ConnectionClosed:
        link.device_up = False


# 40 ms at 24 kHz. A larger frame, sent while the microphone is also
# uploading, fills the ESP TCP window. Its client then aborts the socket.
SPEAKER_FRAME_BYTES = 1920


def pcm_frames(pcm: bytes, frame_bytes: int = SPEAKER_FRAME_BYTES) -> list[bytes]:
    if frame_bytes % 2:
        frame_bytes -= 1
    if frame_bytes < 2:
        return [pcm] if pcm else []
    return [pcm[offset : offset + frame_bytes] for offset in range(0, len(pcm), frame_bytes)]


def enqueue_uplink(queue: asyncio.Queue, event: dict) -> None:
    """Keep the newest microphone audio when OpenAI falls behind."""
    try:
        queue.put_nowait(event)
        return
    except asyncio.QueueFull:
        pass
    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        return
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        return


async def _send_binary(link: LiveLink, pcm: bytes) -> None:
    if not link.device_up or link.socket is None or not pcm:
        return
    try:
        async with link.send_lock:
            if not link.device_up or link.socket is None:
                return
            for frame in pcm_frames(pcm):
                if not link.device_up or link.socket is None:
                    return
                await link.socket.send(frame)
    except websockets.exceptions.ConnectionClosed:
        link.device_up = False


async def _send_json(link: LiveLink, message: dict) -> None:
    await _send_text(link, json.dumps(message, separators=(",", ":")))


def _visible_log(link: LiveLink, effect) -> bool:
    if not effect.log:
        return False
    if effect.transcript and not link.settings.log_transcripts:
        return False
    if effect.tool and not link.settings.log_tool_calls:
        return False
    return True


async def _send_live(link: LiveLink, message: dict) -> None:
    if link.live is None:
        return
    try:
        async with link.live_send_lock:
            if link.live is None:
                return
            await link.live.send(json.dumps(message))
    except Exception as exc:
        logger.warning("Live send failed: %s", exc)


def _queue_uplink(link: LiveLink, event: dict) -> None:
    if link.uplink is None:
        link.uplink = asyncio.Queue(maxsize=8)
        link.uplink_task = asyncio.create_task(_forward_uplink(link))
    enqueue_uplink(link.uplink, event)


async def _forward_uplink(link: LiveLink) -> None:
    queue = link.uplink
    if queue is None:
        return
    while True:
        event = await queue.get()
        if link.live is None or link.close_started:
            continue
        await _send_live(link, event)


def counts_as_activity(effect) -> bool:
    """Transcripts and tool calls keep the session. Audio does not.

    gpt-live-1 can keep sending audio frames after the spoken words are over.
    Counting those frames reset the idle clock, so a quiet room never timed out.
    """
    return bool(effect.transcript or effect.tool)


def conversation_is_idle(link: LiveLink, now: float) -> bool:
    """True when nobody has spoken, no tool is running, and the idle window elapsed.

    The clock starts at the last transcript or tool result. A reply that is
    still marked as playing only holds the session for the first second, so
    leftover audio frames cannot keep the Live connection open.
    """
    if link.opening or link.session is None or link.tools_in_flight:
        return False
    quiet = now - link.last_activity
    if link.session.playing and quiet < 1.0:
        return False
    if link.session.playing:
        link.session.playing = False
    return quiet >= link.settings.idle_seconds


async def _apply(link: LiveLink, effect) -> None:
    if _visible_log(link, effect):
        logger.info(effect.log)
    if counts_as_activity(effect):
        link.last_activity = time.monotonic()
    if effect.flush:
        await _send_json(link, flush_message())
    if effect.phase:
        await _send_json(link, phase_message(effect.phase))
    if effect.audio and link.device is not None:
        device_pcm = resample_pcm16(effect.audio, LIVE_RATE, link.device["output_rate"])
        if device_pcm:
            await _send_binary(link, device_pcm)


async def _close_link(link: LiveLink) -> None:
    if not _begin_close(link):
        return
    reader = link.reader
    link.reader = None
    link.session = None
    if reader is not None and reader is not asyncio.current_task():
        reader.cancel()
        try:
            await reader
        except (asyncio.CancelledError, Exception):
            pass
    live = link.live
    link.live = None
    await _close_socket(live)
    idle = link.idle_task
    link.idle_task = None
    if idle is not None and idle is not asyncio.current_task():
        idle.cancel()
        try:
            await idle
        except (asyncio.CancelledError, Exception):
            pass
    uplink = link.uplink_task
    link.uplink_task = None
    link.uplink = None
    if uplink is not None and uplink is not asyncio.current_task():
        uplink.cancel()
        try:
            await uplink
        except (asyncio.CancelledError, Exception):
            pass
    _drop_parked(link)


async def _read_live(link: LiveLink, generation: int) -> None:
    assert link.live is not None
    assert link.session is not None
    try:
        async for raw in link.live:
            if link.session is None or generation != link.generation:
                return
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            async with link.tool_lock:
                effect = link.session.handle_server_event(json.loads(raw))
            await _apply(link, effect)
            for outbound in effect.outbound:
                await _send_live(link, outbound)
            for call in effect.tool_calls:
                asyncio.create_task(_run_tool(link, generation, call))
            if effect.closed or effect.end_session:
                break
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Live socket ended: %s", exc)
    if generation != link.generation:
        return
    await _send_json(link, phase_message("idle"))
    if not _begin_close(link):
        return
    link.reader = None
    link.session = None
    live = link.live
    link.live = None
    await _close_socket(live)
    idle = link.idle_task
    link.idle_task = None
    if idle is not None and idle is not asyncio.current_task():
        idle.cancel()
    _drop_parked(link)


async def _run_tool(link: LiveLink, generation: int, call) -> None:
    home = link.home
    if home is None or link.session is None:
        return
    link.tools_in_flight += 1
    link.last_activity = time.monotonic()
    try:
        try:
            output = await asyncio.to_thread(home.call_tool, call.name, call.arguments)
        except Exception as exc:
            output = f"Home Assistant call failed: {exc}"
    finally:
        link.tools_in_flight -= 1
        link.last_activity = time.monotonic()
    if link.settings.log_tool_calls:
        compact = " ".join(output.split())
        if len(compact) > 500:
            compact = compact[:500] + "..."
        logger.info("MCP result %s: %s", call.name, compact)
    if link.session is None or generation != link.generation or link.live is None:
        return
    async with link.tool_lock:
        if link.session is None or generation != link.generation:
            return
        events = link.session.result_events(call.call_id, output)
    for event in events:
        await _send_live(link, event)


async def _watch_idle(link: LiveLink) -> None:
    try:
        while True:
            await asyncio.sleep(0.5)
            if not conversation_is_idle(link, time.monotonic()):
                continue
            logger.info("Conversation idle — closing Live session")
            await _send_json(link, phase_message("idle"))
            await _close_link(link)
            return
    except asyncio.CancelledError:
        return


async def _expire_parked(ip: str, link: LiveLink) -> None:
    await asyncio.sleep(link.settings.reconnect_grace_seconds)
    async with _park_lock:
        if _parked.get(ip) is not link:
            return
        _parked.pop(ip, None)
    logger.info("Reconnect window closed")
    await _close_link(link)


class DeviceConnection:
    """One device socket. An OpenAI session can outlive that socket briefly."""

    def __init__(self, socket, settings: Settings, home: HomeAssistantClient | None = None):
        self._socket = socket
        self._settings = settings
        self._home = home
        self._device: dict | None = None
        self._link: LiveLink | None = None
        self._send_lock = asyncio.Lock()

    async def run(self) -> None:
        try:
            async for message in self._socket:
                if isinstance(message, bytes):
                    await self._on_audio(message)
                else:
                    await self._on_text(message)
        except websockets.exceptions.ConnectionClosed as exc:
            logger.info("Device connection closed: %s", exc)
        finally:
            await self._release()

    async def _on_text(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignored non-JSON device message")
            return
        kind = message.get("type")
        if kind == "hello":
            await self._on_hello(message)
        elif kind == "wake":
            await self._on_wake()
        elif kind == "interrupt":
            await self._on_interrupt()
        elif kind == "end":
            await self._on_end()
        else:
            logger.info("Ignored device message %s", kind)

    async def _on_hello(self, message: dict) -> None:
        try:
            self._device = loads_hello(message)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Rejected device hello: %s", exc)
            await self._send_own({"type": "error", "error": str(exc)})
            return
        logger.info(
            "Device %s connected (in=%s Hz, out=%s Hz, aec_verified=%s)",
            self._device["device"],
            self._device["input_rate"],
            self._device["output_rate"],
            self._device["aec_verified"],
        )
        if not self._device["aec_verified"]:
            logger.warning(
                "AEC is not verified on %s. Full duplex is enabled, so test "
                "at low volume before trusting barge-in.",
                self._device["device"],
            )
        await self._send_own(server_hello())
        await self._adopt_parked()

    async def _adopt_parked(self) -> None:
        ip = peer_ip(self._socket)
        if not ip:
            return
        async with _park_lock:
            link = take_parked(
                ip,
                time.monotonic(),
                ttl=self._settings.reconnect_grace_seconds,
            )
        if link is None:
            return
        link.socket = self._socket
        link.device = self._device
        link.device_up = True
        link.last_activity = time.monotonic()
        self._link = link
        phase = "speaking" if link.session is not None and link.session.playing else "listening"
        await _send_json(link, phase_message(phase))
        logger.info("Resumed Live session after reconnect")

    async def _on_wake(self) -> None:
        if self._device is None:
            await self._send_own({"type": "error", "error": "hello required before wake"})
            return
        if self._link is not None:
            await _close_link(self._link)
            self._link = None
        link = LiveLink(self._socket, self._device, self._settings)
        self._link = link
        link.home = self._home
        link.opening = True
        link.last_activity = time.monotonic()
        try:
            link.generation += 1
            generation = link.generation
            functions = self._home.function_tools() if self._home is not None else []
            use_backend = self._settings.enable_web_search or bool(functions)
            link.session = LiveSession(
                voice=self._settings.voice,
                instructions=build_instructions(
                    self._settings, home_tools=bool(functions)
                ),
                web_search_model=self._settings.backend_model if use_backend else "",
                include_web_search=self._settings.enable_web_search,
                function_tools=functions,
                backend_instructions=backend_prompt(self._settings),
            )
            link.live = await websockets.connect(
                self._settings.live_url,
                additional_headers={
                    "Authorization": f"Bearer {self._settings.openai_api_key}"
                },
                max_size=8_000_000,
            )
            if link.session is None or generation != link.generation or link.close_started:
                live = link.live
                link.live = None
                if live is not None:
                    await live.close()
                return
            await link.live.send(json.dumps(link.session.start_event()))
            link.reader = asyncio.create_task(_read_live(link, generation))
            link.idle_task = asyncio.create_task(_watch_idle(link))
            link.last_activity = time.monotonic()
            await _send_json(link, phase_message("listening"))
            logger.info(
                "Live session opened (backend %s, web search %s, Home Assistant tools %s)",
                self._settings.backend_model if use_backend else "off",
                "on" if self._settings.enable_web_search else "off",
                len(functions),
            )
            if self._settings.enable_home_assistant and not functions:
                logger.warning(
                    "Home Assistant tools are not attached to this Live session"
                )
        except Exception:
            logger.exception("Could not open Live session")
            await _close_link(link)
        finally:
            link.opening = False

    async def _on_audio(self, pcm: bytes) -> None:
        link = self._link
        if link is None or link.device is None or link.session is None or link.live is None:
            return
        live_pcm = resample_pcm16(pcm, link.device["input_rate"], LIVE_RATE)
        event = link.session.append_audio(live_pcm)
        if event is None:
            return
        _queue_uplink(link, event)

    async def _on_interrupt(self) -> None:
        link = self._link
        if link is None or link.session is None:
            await self._send_own(flush_message())
            return
        await _apply(link, link.session.interrupt())

    async def _on_end(self) -> None:
        logger.info("Device ended the conversation")
        link = self._link
        if link is None:
            await self._send_own(phase_message("idle"))
            return
        await _send_json(link, phase_message("idle"))
        await _close_link(link)
        self._link = None

    async def _send_own(self, message: dict) -> None:
        """Send before a Live session exists. Replies during a session use the link."""
        if self._link is not None:
            await _send_json(self._link, message)
            return
        try:
            async with self._send_lock:
                await self._socket.send(json.dumps(message, separators=(",", ":")))
        except websockets.exceptions.ConnectionClosed:
            return

    async def _release(self) -> None:
        link = self._link
        if link is None:
            return
        if link.socket is not self._socket:
            self._link = None
            return
        link.device_up = False
        self._link = None
        if link.session is None or link.opening or link.close_started:
            await _close_link(link)
            return
        ip = peer_ip(self._socket)
        link.socket = None
        if not ip:
            await _close_link(link)
            return
        link.parked_at = time.monotonic()
        async with _park_lock:
            previous = _parked.get(ip)
            _parked[ip] = link
        if previous is not None and previous is not link:
            await _close_link(previous)
        logger.info(
            "Device link lost — holding the Live session for %.0f seconds",
            link.settings.reconnect_grace_seconds,
        )
        asyncio.create_task(_expire_parked(ip, link))


async def serve(
    settings: Settings, home: HomeAssistantClient | None = None
) -> None:
    asyncio.get_running_loop().set_exception_handler(_ignore_dead_windows_socket)

    async def handler(socket) -> None:
        peer = getattr(socket, "remote_address", None)
        logger.info("Device socket open: %s", peer)
        try:
            await DeviceConnection(socket, settings, home).run()
        finally:
            logger.info("Device socket closed: %s", peer)

    async with websockets.serve(handler, settings.host, settings.port, max_size=8_000_000):
        logger.info(
            "Live Voice Gateway listening on %s:%s (voice %s, backend %s, web search %s, Home Assistant %s)",
            settings.host,
            settings.port,
            settings.voice,
            settings.backend_model,
            "on" if settings.enable_web_search else "off",
            "on" if settings.enable_home_assistant else "off",
        )
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            return
