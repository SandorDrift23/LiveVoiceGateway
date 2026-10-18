import asyncio
import base64
import os
import unittest
from unittest.mock import patch

from live_voice_gateway.config import (
    Settings,
    _api_name,
    _mcp_url,
    _voice,
    backend_prompt,
    build_instructions,
)
from live_voice_gateway.mcp_debug import HomeAssistantClient, _result_text, _tool_names
from live_voice_gateway.live_session import LIVE_MODEL, LiveEffect, LiveSession
from live_voice_gateway.protocol import flush_message, loads_hello, phase_message
from live_voice_gateway.resample import resample_pcm16
from live_voice_gateway.server import (
    PARK_SECONDS,
    _parked,
    conversation_is_idle,
    counts_as_activity,
    enqueue_uplink,
    pcm_frames,
    take_parked,
)


def _pcm(count: int, value: int = 1000) -> bytes:
    return (value.to_bytes(2, "little", signed=True)) * count


class ResampleTests(unittest.TestCase):
    def test_sixteen_khz_becomes_twenty_four_khz(self):
        output = resample_pcm16(_pcm(160), 16000, 24000)
        self.assertEqual(len(output), 240 * 2)

    def test_equal_rate_is_unchanged(self):
        pcm = _pcm(4)
        self.assertEqual(resample_pcm16(pcm, 24000, 24000), pcm)


class AudioPathTests(unittest.TestCase):
    def test_speaker_audio_is_split_into_short_frames(self):
        frames = pcm_frames(b"\x00" * 4000, 1920)
        self.assertEqual([len(frame) for frame in frames], [1920, 1920, 160])

    def test_uplink_keeps_the_newest_audio_when_full(self):
        queue = asyncio.Queue(maxsize=2)
        enqueue_uplink(queue, {"audio": "a"})
        enqueue_uplink(queue, {"audio": "b"})
        enqueue_uplink(queue, {"audio": "c"})
        self.assertEqual(queue.get_nowait()["audio"], "b")
        self.assertEqual(queue.get_nowait()["audio"], "c")


class ProtocolTests(unittest.TestCase):
    def test_hello_requires_full_duplex(self):
        with self.assertRaises(ValueError):
            loads_hello({
                "type": "hello",
                "protocol": 1,
                "device": "voice_pe",
                "input_rate": 16000,
                "output_rate": 24000,
                "duplex": False,
            })

    def test_phase_and_flush_are_explicit(self):
        self.assertEqual(phase_message("speaking")["phase"], "speaking")
        self.assertEqual(flush_message()["type"], "flush")


class LiveSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = LiveSession(voice="marin", instructions="Be brief.")

    def test_start_is_gpt_live_without_tools(self):
        event = self.session.start_event()
        self.assertEqual(event["type"], "session.start")
        self.assertEqual(event["session"]["model"], LIVE_MODEL)
        self.assertNotIn("delegation", event["session"])
        self.assertNotIn("tools", event["session"])

    def test_web_search_is_delegated_to_responses(self):
        session = LiveSession(
            voice="marin",
            instructions="Be brief.",
            web_search_model="gpt-5.4-mini",
        )
        delegation = session.start_event()["session"]["delegation"]
        self.assertEqual(delegation["type"], "responses")
        self.assertEqual(delegation["responses"]["tools"], [{"type": "web_search"}])

    def test_home_tools_are_client_functions_beside_web_search(self):
        session = LiveSession(
            voice="marin",
            instructions="Be brief.",
            web_search_model="gpt-5.4-mini",
            function_tools=[
                {
                    "type": "function",
                    "name": "intent__HassTurnOn",
                    "description": "Turn on",
                    "parameters": {"type": "object"},
                }
            ],
        )
        tools = session.start_event()["session"]["delegation"]["responses"]["tools"]
        self.assertEqual(tools[0], {"type": "web_search"})
        self.assertEqual(tools[1]["name"], "intent__HassTurnOn")

    def test_disabled_web_search_leaves_only_home_functions(self):
        session = LiveSession(
            voice="marin",
            instructions="Be brief.",
            web_search_model="gpt-5.4-nano",
            include_web_search=False,
            backend_instructions="Call one Home Assistant function.",
            function_tools=[{"type": "function", "name": "intent__HassTurnOn"}],
        )
        backend = session.start_event()["session"]["delegation"]["responses"]
        self.assertEqual(backend["model"], "gpt-5.4-nano")
        self.assertEqual(backend["tools"], [{"type": "function", "name": "intent__HassTurnOn"}])
        self.assertIn("Home Assistant", backend["instructions"])
        self.assertNotIn("web_search", str(backend["tools"]))

    def test_web_search_uses_a_separate_idle_window(self):
        started = self.session.handle_server_event({
            "type": "response.event",
            "event": {"type": "response.web_search_call.in_progress"},
        })
        self.assertEqual(self.session.web_searches, 1)
        self.assertFalse(started.activity)
        self.session.handle_server_event({
            "type": "response.event",
            "event": {"type": "response.web_search_call.searching"},
        })
        self.assertEqual(self.session.web_searches, 1)
        finished = self.session.handle_server_event({
            "type": "response.event",
            "event": {"type": "response.web_search_call.completed"},
        })
        self.assertEqual(self.session.web_searches, 0)
        self.assertTrue(finished.activity)

    def test_unfinished_web_search_resets_when_the_backend_finishes(self):
        self.session.handle_server_event({
            "type": "response.event",
            "event": {"type": "response.web_search_call.in_progress"},
        })
        done = self.session.handle_server_event({
            "type": "response.event",
            "event": {"type": "response.completed"},
        })
        self.assertEqual(self.session.web_searches, 0)
        self.assertTrue(done.activity)

    def test_function_result_continues_after_the_backend_finishes(self):
        effect = self.session.handle_server_event({
            "type": "response.event",
            "event": {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "intent__HassTurnOn",
                    "arguments": '{"name":"Arbeitsplatz"}',
                },
            },
        })
        self.assertEqual(effect.tool_calls[0].name, "intent__HassTurnOn")
        early = self.session.result_events("call_1", "Arbeitsplatz is on")
        self.assertEqual(early[0]["item"]["type"], "function_call_output")
        self.assertEqual(len(early), 1)
        done = self.session.handle_server_event({
            "type": "response.event",
            "event": {"type": "response.completed"},
        })
        self.assertEqual(done.outbound[0]["type"], "response.create")

    def test_spoken_stop_does_not_end_the_session(self):
        effect = self.session.handle_server_event({
            "type": "session.input_transcript.delta",
            "delta": ", stopp",
        })
        self.assertFalse(effect.end_session)
        weather = self.session.handle_server_event({
            "type": "session.input_transcript.delta",
            "delta": " Wetter morgen",
        })
        self.assertFalse(weather.end_session)

    def test_microphone_stays_open_during_playback(self):
        self.session.handle_server_event({
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(_pcm(10)).decode("ascii"),
        })
        self.assertTrue(self.session.playing)
        appended = self.session.append_audio(_pcm(8))
        self.assertIsNotNone(appended)
        self.assertEqual(appended["type"], "session.input_audio.append")

    def test_user_speech_flushes_playback_and_keeps_the_next_reply(self):
        self.session.handle_server_event({
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(_pcm(10)).decode("ascii"),
        })
        effect = self.session.handle_server_event({
            "type": "session.input_transcript.delta",
            "delta": "wait",
        })
        self.assertTrue(effect.flush)
        self.assertEqual(effect.phase, "listening")
        self.assertFalse(self.session.playing)
        nxt = self.session.handle_server_event({
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(_pcm(10)).decode("ascii"),
        })
        self.assertTrue(nxt.audio)
        self.assertFalse(self.session.playing)
        again = self.session.handle_server_event({
            "type": "session.input_transcript.delta",
            "delta": "today",
        })
        self.assertFalse(again.flush)

    def test_device_interrupt_drops_audio_until_the_user_speaks(self):
        self.session.handle_server_event({
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(_pcm(10)).decode("ascii"),
        })
        effect = self.session.interrupt()
        self.assertTrue(effect.flush)
        dropped = self.session.handle_server_event({
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(_pcm(4)).decode("ascii"),
        })
        self.assertEqual(dropped.audio, b"")
        self.session.handle_server_event({
            "type": "session.input_transcript.delta",
            "delta": "lights",
        })
        resumed = self.session.handle_server_event({
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(_pcm(4)).decode("ascii"),
        })
        self.assertTrue(resumed.audio)


class McpDebugTests(unittest.TestCase):
    def test_allowlist_limits_the_functions_sent_to_live(self):
        client = HomeAssistantClient("http://ha/api/mcp", "token", 5, ("intent__HassTurnOn",))
        client._tools = [
            {"name": "intent__HassTurnOn", "description": "On", "inputSchema": {"type": "object"}},
            {"name": "intent__HassTurnOff", "description": "Off"},
        ]
        names = [tool["name"] for tool in client.function_tools()]
        self.assertEqual(names, ["intent__HassTurnOn"])

    def test_tool_result_text_comes_from_content(self):
        body = (
            '{"jsonrpc":"2.0","id":3,"result":{"content":'
            '[{"type":"text","text":"Arbeitsplatz is on"}]}}'
        )
        self.assertEqual(_result_text(body), "Arbeitsplatz is on")

    def test_tool_names_are_read_from_a_json_event(self):
        body = (
            'data: {"jsonrpc":"2.0","id":2,"result":{"tools":'
            '[{"name":"HassTurnOn"},{"name":"HassTurnOff"}]}}'
        )
        self.assertEqual(_tool_names(body), ["HassTurnOn", "HassTurnOff"])


class InstructionTests(unittest.TestCase):
    def test_home_assistant_addon_uses_supervisor_token(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test",
                "HA_ACCESS_TOKEN": "",
                "HA_MCP_URL": "",
                "LANGUAGE": "",
                "SUPERVISOR_TOKEN": "supervisor-test-token",
                "TIMEZONE": "",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.ha_access_token, "supervisor-test-token")
        self.assertEqual(settings.ha_mcp_url, "http://supervisor/core/api/mcp/assist")
        self.assertEqual(settings.web_search_idle_seconds, 45.0)
        self.assertEqual(settings.language, "en")
        self.assertEqual(settings.timezone, "Europe/Berlin")
        self.assertIn("Speak in English.", settings.instructions)

    def test_addon_voice_label_becomes_openai_voice_name(self):
        self.assertEqual(_voice("vesper - English (British, masculine)"), "vesper")
        self.assertEqual(_voice(""), "marin")
        self.assertEqual(
            _api_name("gpt-5-nano - minimum budget", "gpt-5.4-nano"),
            "gpt-5-nano",
        )

    def test_home_assistant_mcp_api_selects_supervisor_path(self):
        self.assertEqual(
            _mcp_url("assist"), "http://supervisor/core/api/mcp/assist"
        )
        self.assertEqual(_mcp_url("all"), "http://supervisor/core/api/mcp")

    def test_home_location_is_one_entry(self):
        settings = Settings(
            openai_api_key="test",
            home_location="Berlin, example street 1, 52.52, 13.40",
            language="German",
            speaking_speed="slow",
        )
        text = build_instructions(settings)
        self.assertIn("Berlin, example street 1, 52.52, 13.40", text)
        self.assertIn("Speak in German.", text)
        self.assertIn("relaxed pace", text)
        self.assertIn("Never claim a light", text)
        connected = build_instructions(settings, home_tools=True)
        self.assertIn("tool result confirms", connected)
        self.assertNotIn("not connected", connected)
        self.assertIn(settings.web_search_instructions, text)

    def test_disabled_tools_omit_their_instructions(self):
        settings = Settings(
            openai_api_key="test",
            enable_web_search=False,
            enable_home_assistant=False,
            home_location="",
        )
        text = build_instructions(settings)
        self.assertNotIn("web search", text.lower())
        self.assertNotIn("smart-home", text.lower())
        self.assertNotIn("home location", text.lower())

    def test_disabled_web_search_tells_the_backend_not_to_search(self):
        settings = Settings(openai_api_key="test", enable_web_search=False)
        text = backend_prompt(settings)
        self.assertIn("Web search is disabled", text)
        self.assertNotIn("Keep a web-search answer", text)


class IdleTests(unittest.TestCase):
    def _link(self, age, *, playing=False, tools=0, idle=12, searches=0, search_idle=45):
        link = type("Link", (), {})()
        link.opening = False
        link.tools_in_flight = tools
        link.last_activity = 100.0 - age
        link.settings = type(
            "Settings",
            (),
            {"idle_seconds": idle, "web_search_idle_seconds": search_idle},
        )()
        link.session = type(
            "Session", (), {"playing": playing, "web_searches": searches}
        )()
        return link

    def test_silence_closes_after_the_idle_window(self):
        self.assertFalse(conversation_is_idle(self._link(11), 100.0))
        self.assertTrue(conversation_is_idle(self._link(12), 100.0))

    def test_recent_playback_keeps_the_session(self):
        link = self._link(0.2, playing=True)
        self.assertFalse(conversation_is_idle(link, 100.0))
        self.assertTrue(link.session.playing)

    def test_finished_playback_starts_the_idle_clock(self):
        link = self._link(2, playing=True)
        self.assertFalse(conversation_is_idle(link, 100.0))
        self.assertFalse(link.session.playing)

    def test_a_running_tool_holds_the_session(self):
        self.assertFalse(conversation_is_idle(self._link(30, tools=1), 100.0))

    def test_web_search_uses_its_longer_timeout(self):
        during_search = self._link(8, idle=6, searches=1, search_idle=45)
        self.assertFalse(conversation_is_idle(during_search, 100.0))
        expired_search = self._link(45, idle=6, searches=1, search_idle=45)
        self.assertTrue(conversation_is_idle(expired_search, 100.0))

    def test_lingering_playback_does_not_hold_past_the_idle_window(self):
        link = self._link(12, playing=True)
        self.assertTrue(conversation_is_idle(link, 100.0))
        self.assertFalse(link.session.playing)

    def test_audio_frames_are_not_activity(self):
        self.assertFalse(counts_as_activity(LiveEffect(audio=b"\x00\x01")))
        self.assertTrue(counts_as_activity(LiveEffect(transcript=True)))
        self.assertTrue(counts_as_activity(LiveEffect(tool=True)))


class ParkedSessionTests(unittest.TestCase):
    def setUp(self):
        _parked.clear()

    def tearDown(self):
        _parked.clear()

    def test_reconnect_claims_the_same_session(self):
        link = LiveSession(voice="marin", instructions="Be brief.")
        parked = type("Link", (), {"session": link, "parked_at": 10.0})()
        _parked["192.168.178.52"] = parked
        claimed = take_parked("192.168.178.52", now=12.0)
        self.assertIs(claimed, parked)
        self.assertIsNone(claimed.parked_at)
        self.assertIsNone(take_parked("192.168.178.52", now=12.0))

    def test_claim_refuses_a_session_past_the_window(self):
        parked = type("Link", (), {"session": object(), "parked_at": 10.0})()
        _parked["192.168.178.52"] = parked
        self.assertIsNone(take_parked("192.168.178.52", now=10.0 + PARK_SECONDS + 0.1))
        self.assertIn("192.168.178.52", _parked)


if __name__ == "__main__":
    unittest.main()
