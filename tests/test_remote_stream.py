"""Live token streaming: ``message.delta`` (stream.py and its channels).

Each channel gets a hand-written event stream in the shape its server
sends: Claude's ``stream_event`` lines, Codex app-server's
``item/agentMessage/delta`` notifications, opencode serve's
``message.part.*`` SSE payloads. Deltas never reach a journal.
"""

import http.client
import io
import json
import unittest
from unittest.mock import patch

from swe.remote import agent as agent_mod
from swe.remote import inject, protocol, stream, supervise
from tests._tempdir import temp_dir
from tests.test_remote_adapters import _FakeServeProc
from tests.test_remote_hub import HubBase, _session
from tests.test_remote_inject import _FakeProc
from tests.test_remote_inject import _session as _claude_session


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Timers:
    """threading.Timer stand-in: records each timer, fires on demand."""

    def __init__(self):
        self.made = []

    def __call__(self, delay, fn, args=()):
        timers = self

        class _T:
            daemon = False
            cancelled = False

            def start(self):
                timers.made.append(self)

            def cancel(self):
                self.cancelled = True

            def fire(self):
                if not self.cancelled:
                    fn(*args)

        t = _T()
        t.delay = delay
        return t


def _stream(interval=0.1):
    out = []
    clock = _Clock()
    timers = _Timers()
    ds = stream.DeltaStream(lambda k, ev: out.append(ev), interval=interval,
                            clock=clock)
    return ds, out, clock, timers


def _texts(events):
    return "".join(ev["data"]["text"] for ev in events)


class TestDeltaStream(unittest.TestCase):
    def test_coalesces_within_the_interval_and_flushes_on_the_timer(self):
        ds, out, clock, timers = _stream()
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k", "m", "a", "assistant", "Hello ")
            self.assertEqual(_texts(out), "Hello ")  # first chunk goes now
            clock.t += 0.02
            ds.push("k", "m", "a", "assistant", "there ")
            clock.t += 0.02
            ds.push("k", "m", "a", "assistant", "friend ")
            self.assertEqual(len(out), 1)            # held for the window
            self.assertEqual(len(timers.made), 1)    # one timer, not two
            self.assertAlmostEqual(timers.made[0].delay, 0.08)
            clock.t += 0.06
            timers.made[0].fire()
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1]["data"]["text"], "there friend ")
        self.assertEqual([e["data"]["seq"] for e in out], [1, 2])

    def test_finish_sends_the_rest_with_done(self):
        ds, out, clock, timers = _stream()
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k", "m", "a", "assistant", "Hel")
            ds.push("k", "m", "a", "assistant", "lo")
            ds.finish("k", "a")
        self.assertEqual(_texts(out), "Hello")
        last = out[-1]
        self.assertTrue(last["data"]["done"])
        self.assertTrue(timers.made[0].cancelled)
        # Finished: a late chunk for the same id starts nothing stale.
        ds.finish("k", "a")
        self.assertEqual(len(out), 1)

    def test_envelope_shape_has_no_seq(self):
        ds, out, _clock, _timers = _stream()
        ds.push("codex:t", "mbp", "item-1", "assistant", "Hi ")
        ev = out[0]
        self.assertEqual(ev["type"], "message.delta")
        self.assertEqual(ev["session"], "codex:t")
        self.assertEqual(ev["machine"], "mbp")
        self.assertNotIn("seq", ev)
        self.assertEqual(ev["data"], {"id": "item-1", "kind": "assistant",
                                      "text": "Hi ", "seq": 1,
                                      "done": False})

    def test_unknown_kind_and_empty_chunks_are_ignored(self):
        ds, out, _clock, _timers = _stream()
        ds.push("k", "m", "a", "<b>", "x ")
        ds.push("k", "m", "a", "assistant", "")
        ds.push("k", "m", "", "assistant", "x ")
        self.assertEqual(out, [])

    def test_trailing_word_waits_for_whitespace(self):
        ds, out, clock, timers = _stream()
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k", "m", "a", "assistant", "The answer is forty")
            self.assertEqual(_texts(out), "The answer is ")
            ds.finish("k", "a")
        self.assertEqual(_texts(out), "The answer is forty")

    def test_secret_split_across_chunks_is_redacted(self):
        ds, out, clock, timers = _stream(interval=0)
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k", "m", "a", "assistant", "key: sk-ant-")
            ds.push("k", "m", "a", "assistant", "abcdefghijklmnopqrstuv done")
            ds.finish("k", "a")
        text = _texts(out)
        self.assertNotIn("abcdefghij", text)
        self.assertIn("[REDACTED]", text)

    def test_credential_line_is_held_until_it_ends(self):
        ds, out, clock, timers = _stream(interval=0)
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k", "m", "a", "assistant", "Set it:\nPASSWORD=\"hunter ")
            self.assertEqual(_texts(out), "Set it:\n")
            ds.push("k", "m", "a", "assistant", "two\" then\n")
            ds.finish("k", "a")
        text = _texts(out)
        self.assertNotIn("hunter", text)
        self.assertIn("PASSWORD=[REDACTED]", text)

    def test_private_key_block_is_held_until_its_end(self):
        ds, out, clock, timers = _stream(interval=0)
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k", "m", "a", "assistant",
                    "Here:\n-----BEGIN RSA PRIVATE KEY-----\nMIIE abc \n")
            self.assertEqual(_texts(out), "Here:\n")
            ds.push("k", "m", "a", "assistant",
                    "-----END RSA PRIVATE KEY-----\nok ")
            ds.finish("k", "a")
        text = _texts(out)
        self.assertNotIn("MIIE", text)
        self.assertIn("[REDACTED PRIVATE KEY]", text)

    def test_a_partial_end_marker_keeps_the_key_hidden(self):
        ds, out, clock, timers = _stream(interval=0)
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k", "m", "a", "assistant",
                    "Here:\n-----BEGIN RSA PRIVATE KEY-----\nMIIE abc \n"
                    "-----END ")
            ds.push("k", "m", "a", "assistant", "RSA PRIV ")
        self.assertNotIn("MIIE", _texts(out))

    def test_a_key_cut_off_by_a_dead_reader_never_shows(self):
        ds, out, clock, timers = _stream(interval=0)
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k", "m", "a", "assistant",
                    "Here:\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl bW \n")
            ds.finish_all("k")
        self.assertEqual(_texts(out), "Here:\n")
        self.assertTrue(out[-1]["data"]["done"])

    def test_an_open_block_after_a_closed_one_is_still_held(self):
        text = ("-----BEGIN EC PRIVATE KEY-----\nAAA\n"
                "-----END EC PRIVATE KEY-----\nthen "
                "-----BEGIN RSA PRIVATE KEY-----\nSECRET \n")
        safe = stream.safe_prefix(text, final=True)
        self.assertNotIn("AAA", safe)
        self.assertNotIn("SECRET", safe)
        self.assertTrue(safe.endswith("then "))

    def test_certificates_and_public_keys_stream_on(self):
        text = ("-----BEGIN CERTIFICATE-----\nMIIC \n"
                "-----BEGIN PUBLIC KEY-----\nMFkw more ")
        self.assertEqual(stream.safe_prefix(text, final=False), text)
        # A label still arriving may turn private: held.
        self.assertEqual(stream.safe_prefix("ok -----BEGIN RSA PRIV", False),
                         "ok ")
        self.assertEqual(
            stream.safe_prefix("ok -----BEGIN RSA PRIVATE KEY-----\nX \n",
                               False), "ok ")

    def test_rewrite_of_sent_text_stalls_the_stream(self):
        ds, out, clock, timers = _stream(interval=0)
        with patch.object(stream, "safe_prefix",
                          side_effect=["one ", "ONE two ", "ONE two"]):
            ds.push("k", "m", "a", "assistant", "one ")
            ds.push("k", "m", "a", "assistant", "two ")
            ds.finish("k", "a")
        self.assertEqual(_texts(out), "one ")  # nothing after the rewrite

    def test_runaway_message_stops_streaming(self):
        ds, out, clock, timers = _stream(interval=0)
        with patch.object(stream, "MAX_STREAM_CHARS", 10):
            ds.push("k", "m", "a", "assistant", "short ")
            ds.push("k", "m", "a", "assistant", "much too long now ")
            ds.finish("k", "a")
        self.assertEqual(_texts(out), "short ")

    def test_finish_all_closes_only_that_session(self):
        ds, out, clock, timers = _stream()
        with patch.object(stream.threading, "Timer", timers):
            ds.push("k1", "m", "a", "assistant", "x")
            ds.push("k1", "m", "b", "assistant", "y")
            ds.push("k2", "m", "c", "assistant", "z")
            ds.finish_all("k1")
        done = sorted(e["data"]["id"] for e in out if e["data"]["done"])
        self.assertEqual(done, ["a", "b"])

    def test_a_failing_sink_never_raises(self):
        def boom(k, ev):
            raise RuntimeError("down")

        ds = stream.DeltaStream(boom, interval=0)
        ds.push("k", "m", "a", "assistant", "hi ")
        ds.finish("k", "a")


# -- channels -----------------------------------------------------------


class _Sink:
    """A DeltaStream stand-in that records push/finish calls."""

    def __init__(self):
        self.calls = []

    def push(self, key, machine, msg_id, kind, chunk):
        self.calls.append(("push", key, machine, msg_id, kind, chunk))

    def finish(self, key, msg_id):
        self.calls.append(("finish", key, msg_id))

    def finish_all(self, key):
        self.calls.append(("finish_all", key))

    def text(self, msg_id):
        return "".join(c[5] for c in self.calls
                       if c[0] == "push" and c[3] == msg_id)


def _claude_event(event, parent=None):
    return json.dumps({"type": "stream_event", "event": event,
                       "parent_tool_use_id": parent, "session_id": "abc-123"})


# One turn as `claude -p --output-format stream-json --verbose
# --include-partial-messages` prints it: a thinking block, a text block,
# a subagent's text (parent_tool_use_id set), then the usual records.
_CLAUDE_TURN = [
    json.dumps({"type": "system", "subtype": "init"}),
    _claude_event({"type": "message_start",
                   "message": {"id": "msg_01", "role": "assistant"}}),
    _claude_event({"type": "content_block_start", "index": 0,
                   "content_block": {"type": "thinking", "thinking": ""}}),
    _claude_event({"type": "content_block_delta", "index": 0,
                   "delta": {"type": "thinking_delta", "thinking": "hmm"}}),
    _claude_event({"type": "content_block_stop", "index": 0}),
    _claude_event({"type": "content_block_start", "index": 1,
                   "content_block": {"type": "text", "text": ""}}),
    _claude_event({"type": "content_block_delta", "index": 1,
                   "delta": {"type": "text_delta", "text": "PO"}}),
    _claude_event({"type": "content_block_delta", "index": 1,
                   "delta": {"type": "text_delta", "text": "NG"}}),
    _claude_event({"type": "content_block_delta", "index": 1,
                   "delta": {"type": "text_delta", "text": "x"}},
                  parent="toolu_9"),
    _claude_event({"type": "content_block_stop", "index": 1}),
    _claude_event({"type": "message_stop"}),
    json.dumps({"type": "assistant", "message": {"id": "msg_01"}}),
    json.dumps({"type": "result", "result": "PONG"}),
]


class TestHeadlessClaudeStreams(unittest.TestCase):
    def setUp(self):
        self.sink = _Sink()
        self.inj = inject.ClaudeHeadlessInjector(
            temp_dir(self), "claude:x", deltas=self.sink, machine="mbp")
        supervise.mark(self.inj.home, self.inj.key, {"mode": "headless"})
        inject.ClaudeHeadlessInjector._partial = True
        self.addCleanup(setattr, inject.ClaudeHeadlessInjector,
                        "_partial", True)

    def test_argv_asks_for_partial_messages(self):
        argv = self.inj.argv(_claude_session(), "hi")
        self.assertIn("--include-partial-messages", argv)
        self.assertLess(argv.index("--include-partial-messages"),
                        argv.index("--"))
        self.assertNotIn("--include-partial-messages",
                         self.inj.argv(_claude_session(), "hi",
                                       partial=False))

    def test_text_blocks_stream_and_the_result_still_settles(self):
        with patch.object(inject.subprocess, "Popen",
                          return_value=_FakeProc(_CLAUDE_TURN)):
            out = self.inj.send(_claude_session(),
                                {"text": "go", "mode": "queue"}, "done")
        self.assertTrue(out.ok)
        self.assertEqual(out.note, "PONG")
        self.assertEqual(self.sink.text("msg_01:1"), "PONG")
        self.assertEqual(self.sink.text("msg_01:0"), "")  # thinking: no
        self.assertIn(("finish", "claude:x", "msg_01:1"), self.sink.calls)
        self.assertEqual(self.sink.calls[-1], ("finish_all", "claude:x"))
        pushes = [c for c in self.sink.calls if c[0] == "push"]
        self.assertTrue(all(c[2] == "mbp" and c[4] == "assistant"
                            for c in pushes))

    def test_a_claude_without_the_flag_reruns_without_it(self):
        old = _FakeProc(["error: unknown option '--include-partial-messages'"],
                        rc=1)
        new = _FakeProc([json.dumps({"type": "result", "result": "ok"})])
        with patch.object(inject.subprocess, "Popen",
                          side_effect=[old, new]) as popen:
            out = self.inj.send(_claude_session(),
                                {"text": "go", "mode": "queue"}, "done")
        self.assertTrue(out.ok)
        self.assertIn("--include-partial-messages", popen.call_args_list[0][0][0])
        self.assertNotIn("--include-partial-messages",
                         popen.call_args_list[1][0][0])
        self.assertFalse(inject.ClaudeHeadlessInjector._partial)

    def test_a_failure_after_work_started_does_not_rerun(self):
        lines = [
            json.dumps({"type": "system", "subtype": "init"}),
            "warning: --include-partial-messages: unknown option in plugin",
        ]
        with patch.object(inject.subprocess, "Popen",
                          return_value=_FakeProc(lines, rc=1)) as popen:
            out = self.inj.send(_claude_session(),
                                {"text": "go", "mode": "queue"}, "done")
        self.assertFalse(out.ok)
        self.assertEqual(popen.call_count, 1)
        self.assertTrue(inject.ClaudeHeadlessInjector._partial)

    def test_a_mention_of_the_flag_alone_does_not_rerun(self):
        with patch.object(inject.subprocess, "Popen", return_value=_FakeProc(
                ["note: --include-partial-messages is slow"], rc=1)) as popen:
            self.inj.send(_claude_session(),
                          {"text": "go", "mode": "queue"}, "done")
        self.assertEqual(popen.call_count, 1)

    def test_other_failures_do_not_rerun(self):
        with patch.object(inject.subprocess, "Popen",
                          return_value=_FakeProc(["boom"], rc=2)) as popen:
            out = self.inj.send(_claude_session(),
                                {"text": "go", "mode": "queue"}, "done")
        self.assertFalse(out.ok)
        self.assertEqual(popen.call_count, 1)

    def test_injector_for_hands_deltas_to_every_api_channel(self):
        home = temp_dir(self)
        for mode, tool in (("headless", "claude"), ("serve", "opencode"),
                           ("appserver", "codex")):
            inj = inject.injector_for(
                home, "k", {"mode": mode, "tool_name": tool},
                deltas=self.sink, machine="m")
            self.assertIs(inj.deltas, self.sink, mode)
        tmux = inject.injector_for(home, "k", {"mode": "tmux"},
                                   deltas=self.sink)
        self.assertFalse(hasattr(tmux, "deltas"))


class _AppProc:
    def __init__(self, lines):
        self.stdout = io.BytesIO(
            b"".join(json.dumps(m).encode() + b"\n" for m in lines))


class TestCodexAppServerStreams(unittest.TestCase):
    def test_agent_message_deltas_stream_and_finish(self):
        sink = _Sink()
        server = inject._AppServer(_AppProc([
            {"method": "turn/started", "params": {"turn": {"id": "t1"}}},
            {"method": "item/started",
             "params": {"item": {"type": "agentMessage", "id": "msg_a"}}},
            {"method": "item/agentMessage/delta",
             "params": {"threadId": "thr", "turnId": "t1",
                        "itemId": "msg_a", "delta": "Hel"}},
            {"method": "item/agentMessage/delta",
             "params": {"itemId": "msg_a", "delta": "lo"}},
            {"method": "item/reasoning/textDelta",
             "params": {"itemId": "rs_1", "delta": "think"}},
            {"method": "item/completed",
             "params": {"item": {"type": "agentMessage", "id": "msg_a",
                                 "text": "Hello"}}},
            {"method": "turn/completed", "params": {"turn": {"id": "t1"}}},
        ]), "thr")
        inject._appserver_read_loop(server, "codex:k", None, "mbp",
                                    deltas=sink)
        self.assertEqual(sink.text("msg_a"), "Hello")
        self.assertEqual(sink.text("rs_1"), "")
        self.assertIn(("finish", "codex:k", "msg_a"), sink.calls)
        self.assertIn(("finish_all", "codex:k"), sink.calls)
        self.assertEqual(sink.calls[-1], ("finish_all", "codex:k"))  # EOF

    def test_no_deltas_leaves_the_loop_as_before(self):
        server = inject._AppServer(_AppProc([
            {"method": "item/agentMessage/delta",
             "params": {"itemId": "msg_a", "delta": "x"}},
        ]), "thr")
        inject._appserver_read_loop(server, "codex:k", None, "mbp")
        self.assertTrue(server.dead)

    def test_reader_thread_gets_the_injectors_deltas(self):
        sink = _Sink()
        inj = inject.CodexAppServerInjector(None, "codex:k", deltas=sink)
        self.assertIs(inj.deltas, sink)


def _oc(etype, props):
    return json.dumps({"type": etype, "properties": props}).encode()


class TestOpencodeServeStreams(unittest.TestCase):
    def setUp(self):
        self.sink = _Sink()
        self.serve = inject._ServeProc(_FakeServeProc(), "http://x")
        self.serve.subs["ses_1"] = ("opencode:ses_1", None, "mbp")
        self.serve.deltas["ses_1"] = self.sink

    def _part(self, ptype, delta=None, end=None, pid="prt_1"):
        props = {"part": {"id": pid, "sessionID": "ses_1",
                          "messageID": "msg_1", "type": ptype,
                          "time": {"start": 1, **({"end": end} if end else {})}}}
        if delta is not None:
            props["delta"] = delta
        inject._serve_event(self.serve, _oc("message.part.updated", props))

    def test_part_updated_with_delta_streams_text(self):
        self._part("text", "Hel")
        self._part("text", "lo")
        self._part("text", end=5)
        self.assertEqual(self.sink.text("prt_1"), "Hello")
        self.assertIn(("finish", "opencode:ses_1", "prt_1"), self.sink.calls)

    def test_part_delta_events_stream_known_text_parts_only(self):
        self._part("text")
        self._part("reasoning", pid="prt_r")
        for pid, field, delta in (("prt_1", "text", "Hi "),
                                  ("prt_r", "text", "think"),
                                  ("prt_x", "text", "unknown part"),
                                  ("prt_1", "other", "no")):
            inject._serve_event(self.serve, _oc("message.part.delta", {
                "sessionID": "ses_1", "messageID": "msg_1",
                "partID": pid, "field": field, "delta": delta}))
        self.assertEqual(self.sink.text("prt_1"), "Hi ")
        self.assertEqual(self.sink.text("prt_r"), "")
        self.assertEqual(self.sink.text("prt_x"), "")

    def test_session_idle_finishes_open_parts(self):
        self._part("text", "x")
        inject._serve_event(self.serve, _oc("session.idle",
                                            {"sessionID": "ses_1"}))
        self.assertEqual(self.sink.calls[-1],
                         ("finish_all", "opencode:ses_1"))

    def test_other_sessions_and_tool_parts_do_not_stream(self):
        inject._serve_event(self.serve, _oc("message.part.updated", {
            "part": {"id": "p9", "sessionID": "ses_other", "type": "text"},
            "delta": "nope"}))
        self._part("tool", "x", pid="prt_t")
        self.assertEqual([c for c in self.sink.calls if c[0] == "push"], [])

    def test_permission_events_still_route(self):
        events = []
        self.serve.subs["ses_1"] = ("opencode:ses_1",
                                    lambda k, ev: events.append(ev), "mbp")
        inject._serve_event(self.serve, _oc("permission.asked", {
            "id": "perm-1", "sessionID": "ses_1", "title": "bash"}))
        self.assertEqual(events[0]["type"], "approval.requested")

    def test_release_drops_the_sessions_stream(self):
        self.serve.refs = 2
        inj = inject.OpencodeServeInjector(None, "opencode:ses_1")
        cwd = "/tmp/proj"
        inject.OpencodeServeInjector._serves[cwd] = self.serve
        self.addCleanup(inject.OpencodeServeInjector._serves.clear)
        session = _session(sid="ses_1", tool="opencode", path=cwd)
        with patch.object(inject.os.path, "realpath", return_value=cwd):
            inj._release(session)
        self.assertNotIn("ses_1", self.serve.deltas)
        self.assertEqual(self.sink.calls[-1],
                         ("finish_all", "opencode:ses_1"))

    def test_a_dropped_event_stream_finishes_open_messages(self):
        with patch.object(inject.urllib.request, "urlopen",
                          return_value=io.BytesIO(b"data: {}\n\n")):
            inject._serve_event_loop(self.serve)
        self.assertEqual(self.sink.calls[-1],
                         ("finish_all", "opencode:ses_1"))


# -- hub and satellite --------------------------------------------------


class TestHubRelay(HubBase):
    def test_publish_transient_reaches_the_chat_but_not_the_journal(self):
        key = "codex:quiet"  # no tailer, so nothing else lands here
        session_q = self.srv.state.bus.subscribe(key)
        global_q = self.srv.state.bus.subscribe(None)
        ev = protocol.delta_event(key, "mbp", "a", "assistant", "hi ", 1)
        self.srv.state.publish_transient(key, ev)
        got = session_q.get_nowait()
        self.assertEqual(got["type"], "message.delta")
        self.assertNotIn("seq", got)
        seen = []
        while not global_q.empty():  # a background tail may emit there
            seen.append(global_q.get_nowait()["type"])
        self.assertNotIn("message.delta", seen)
        self.assertEqual(self.srv.state.journal.last_seq(key), 0)

    def test_sse_frame_has_no_id_and_the_cursor_survives(self):
        auth = self.auth()
        key = "claude:s1"
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        try:
            conn.request("GET", f"/v1/sessions/{key}/events?last=0",
                         headers=auth)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            frame, frames, sent = [], [], False
            for _ in range(200):
                line = resp.readline().decode()
                if not line:
                    break
                if line != "\n":
                    frame.append(line)
                    continue
                frames.append(frame)
                if any("journal.caught-up" in x for x in frame) and not sent:
                    sent = True
                    self.srv.state.publish_transient(key, protocol.delta_event(
                        key, "mbp", "a", "assistant", "live ", 1))
                    self.srv.state.emit(key, protocol.event(
                        "message.appended", key, "mbp",
                        protocol.message("assistant", "live")))
                if any('"live"' in x for x in frame):
                    break
                frame = []
        finally:
            conn.close()
        delta = next(f for f in frames
                     if any("event: message.delta" in x for x in f))
        self.assertFalse(any(x.startswith("id:") for x in delta))
        appended = frames[-1]
        self.assertTrue(any(x.startswith("id: ") for x in appended))

    def test_register_reply_advertises_message_delta(self):
        cred = self.srv.pair(role="satellite")
        auth = {"Authorization": f"Bearer {cred['credential']}"}
        status, data = self.srv.request(
            "POST", "/v1/agent/register", {"machine": {"id": "sat-m1"}},
            headers=auth)
        self.assertEqual(status, 200)
        self.assertIs(json.loads(data)["message_delta"], True)

    def test_satellite_deltas_are_relayed_not_journaled(self):
        auth = self.srv.satellite("sat-m1")
        hub_key = protocol.remote_key("sat-m1", "codex:t")
        q = self.srv.state.bus.subscribe(hub_key)
        good = protocol.delta_event("codex:t", "sat-m1", "msg_a",
                                    "assistant", "Hi ", 1)
        bad = [
            dict(good, data=dict(good["data"], kind="<script>")),
            dict(good, data=dict(good["data"], seq=0)),
            dict(good, data=dict(good["data"], id="")),
            dict(good, data=dict(good["data"], text=None)),
            dict(good, session=""),
        ]
        status, _ = self.srv.request(
            "POST", "/v1/agent/events",
            {"machine_id": "sat-m1", "events": [good, *bad]}, headers=auth)
        self.assertEqual(status, 200)
        got = q.get_nowait()
        self.assertEqual(got["session"], hub_key)
        self.assertEqual(got["machine"], "sat-m1")
        self.assertEqual(got["data"]["text"], "Hi ")
        self.assertNotIn("seq", got)
        self.assertTrue(q.empty())
        self.assertEqual(self.srv.state.journal.last_seq(hub_key), 0)

    def test_hub_injector_gets_the_hub_delta_stream(self):
        supervise.mark(self.srv.home, "codex:t",
                       {"mode": "appserver", "tool_name": "codex"})
        handler = type("H", (), {"_state": self.srv.state})()
        from swe.remote.hub import _Handler
        inj = _Handler._injector(handler, "codex:t")
        self.assertIs(inj._inner.deltas, self.srv.state.deltas)


class TestSatelliteDeltas(unittest.TestCase):
    def setUp(self):
        self.agent = agent_mod.RemoteAgent("http://hub", "cred",
                                           home=temp_dir(self))

    def test_nothing_queued_until_the_hub_advertises_deltas(self):
        self.agent._queue_delta("k", {"type": "message.delta"})
        self.assertEqual(len(self.agent._delta_out), 0)
        with patch.object(agent_mod, "_post",
                          return_value=(200, {"ok": True,
                                              "message_delta": True})):
            self.assertTrue(self.agent._register())
        self.agent._queue_delta("k", {"type": "message.delta"})
        self.assertEqual(len(self.agent._delta_out), 1)

    def test_older_hub_reply_turns_deltas_off(self):
        self.agent._hub_deltas = True
        with patch.object(agent_mod, "_post",
                          return_value=(200, {"ok": True})):
            self.agent._register()
        self.assertFalse(self.agent._hub_deltas)

    def test_push_sends_a_batch_and_never_touches_the_outbox(self):
        self.agent._hub_deltas = True
        self.agent.deltas = stream.DeltaStream(self.agent._queue_delta,
                                               interval=0)
        self.agent.deltas.push("codex:t", "m", "a", "assistant", "Hi ")
        self.agent.deltas.finish("codex:t", "a")
        with patch.object(agent_mod, "_post",
                          return_value=(200, {})) as post:
            self.agent._push_deltas()
        body = post.call_args[0][3]
        self.assertEqual([e["data"]["seq"] for e in body["events"]], [1, 2])
        self.assertEqual(len(self.agent._delta_out), 0)
        self.assertEqual(self.agent.outbox, [])


if __name__ == "__main__":
    unittest.main()
