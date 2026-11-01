"""Live reply text in the web chat (#1444): pages/chat.js's port of
ReplyStreams.swift, under node with the Queued tray tests' DOM stub.

``message.delta`` frames grow a streaming bubble per reply id at the end
of the log; the next live ``message.appended`` of the same kind takes the
oldest open bubble's place when the reveal shows it. A seq gap stops
drawing a bubble, and an idle session or a reconnect drops what no final
claimed."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_webapp_chat_queue import CHAT_JS, PRELUDE

# The few DOM parts the bubbles need that the tray's stub leaves out.
DOM_EXTRA = r"""
function patchEl(e) {
  Object.defineProperty(e, "parentNode", { get: () => e.parent });
  Object.defineProperty(e, "childElementCount", { get: () => e.children.length });
  Object.defineProperty(e, "previousElementSibling", { get: () => {
    const p = e.parent; if (!p) return null;
    const i = p.children.indexOf(e); return i > 0 ? p.children[i - 1] : null; } });
  e.insertBefore = (c, ref) => {
    if (c.parent) c.remove();
    c.parent = e;
    const i = e.children.indexOf(ref);
    e.children.splice(i < 0 ? e.children.length : i, 0, c);
    return c;
  };
  e.matches = () => false;
  e.insertAdjacentHTML = (_, html) => { e._html += html; };
  return e;
}
const baseCreate = document.createElement;
document.createElement = (t) => patchEl(baseCreate(t));
for (const id in ids) patchEl(ids[id]);
"""

HELPERS = r"""
const T = SessionChat._test;
T.chat.key = 'claude:s1'; T.chat.gen = 1; T.chat.rowKnown = true;
T.chat.row = { status: 'active', ownership: 'attached' };
// The first live entry shows at once, as long after the last one.
T.chat.lastRevealAt = -1e9;
const log = ids['chat-log'];
const frame = (ev) => T.handleFrame('claude:s1', 'data: ' + JSON.stringify(Object.assign({ session: 'claude:s1' }, ev)));
const delta = (id, text, seq, done = false) => frame({ type: 'message.delta', data: { id, kind: 'assistant', text, seq, done } });
const appended = (seq, data) => frame({ type: 'message.appended', seq, data });
// Shows every queued event now, without the reveal's pauses.
const drain = () => {
  while (T.chat.pending.length) { clearTimeout(T.chat.revealTimer); T.chat.revealTimer = null; T.revealNext(); }
  clearTimeout(T.chat.revealTimer); T.chat.revealTimer = null;
};
const shape = () => log.children.map((c) => c === T.chat.streamBox
  ? ['streams', c.children.map((b) => [b.dataset.streamId, b.dataset.text])]
  : [c.className, c.dataset.seq || '']);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestLiveReply(unittest.TestCase):
    def run_js(self, body):
        source = CHAT_JS.read_text(encoding="utf-8")
        script = (PRELUDE + DOM_EXTRA + source + "\n" + HELPERS
                  + "(async () => {" + body + "})()"
                  + ".then((r) => process.stdout.write(JSON.stringify(r)))"
                  + ".catch((e) => { console.error(e); process.exit(1); });\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_rules_match_the_swift_copy(self):
        out = self.run_js("""
            const d = (id, text, seq, extra = {}) => Object.assign({ id, kind: 'assistant', text, seq, done: false }, extra);
            const r = T.replyStreams();
            const steps = [r.apply(d('a', 'Hel', 1)), r.apply(d('b', 'two', 1)), r.apply(d('t', 'hmm', 1, { kind: 'thinking' })),
                           r.apply(d('a', 'lo', 2, { done: true }))];
            const first = r.shown().map((b) => [b.id, b.text, b.done]);
            // A gap stops drawing a, but a's final still takes it, not b.
            const gap = [r.apply(d('a', '!', 4)), r.shown().map((b) => b.id), r.apply(d('a', '?', 5))];
            const claims = [r.claim('tool_use'), r.claim('assistant'), r.claim(undefined), r.claim('assistant')];
            // Claimed but drawn until released; its late deltas are dropped.
            const late = [r.apply(d('b', ' more', 2)), r.shown().map((b) => b.text), r.release('b'), r.shown().length,
                          r.apply(d('b', 'again', 1))];
            // Heard mid-way: matched, never drawn.
            const s = T.replyStreams();
            const mid = [s.apply(d('m', 'tail', 3)), s.shown().length, s.claim('assistant')];
            // Idle takes only the unclaimed bubbles that were open then.
            const i = T.replyStreams();
            i.apply(d('x', 'claimed', 1)); i.apply(d('y', 'orphan', 1));
            const open = i.openIds(); i.claim('assistant'); i.apply(d('z', 'next', 1));
            const swept = [i.sweep(open), i.shown().map((b) => b.id), i.apply(d('y', 'late', 2)), i.clear(), i.shown().length,
                           i.apply(d('z', 'late', 2))];
            return { steps, first, gap, claims, late, mid, swept };""")
        self.assertEqual(out["steps"], [True, True, False, True])
        self.assertEqual(out["first"], [["a", "Hello", True], ["b", "two", False]])
        self.assertEqual(out["gap"], [True, ["b"], False])
        self.assertEqual(out["claims"], ["", "a", "b", ""])
        self.assertEqual(out["late"], [False, ["two"], True, 0, False])
        self.assertEqual(out["mid"], [False, 0, "m"])
        self.assertEqual(out["swept"], [True, ["x", "z"], False, True, 0, False])

    def test_deltas_repaint_throttled_and_the_final_takes_the_bubbles_place(self):
        out = self.run_js("""
            delta('x', 'Hel', 1); delta('x', 'lo **wor', 2);
            const throttled = [T.chat.streamPaint !== null, shape()];
            await wait(T.STREAM_MS.paint + 20);
            const painted = shape();
            const html = T.chat.streamBox.children[0].innerHTML;
            // A tool card and the final arrive together: the card is shown
            // first, and the bubble stays until the final's turn.
            appended(5, { kind: 'tool_use', tool: { name: 'Bash', call_id: 'c1' } });
            appended(6, { kind: 'assistant', text: 'Hello **world**' });
            const held = shape();
            delta('x', 'ld**', 3, true);
            T.paintStreams();
            const late = shape();
            drain();
            const final = log.children[log.children.length - 1];
            return { throttled, painted, html, held, late, after: shape(), arrive: final.classList.contains('arrive'),
                     queueEmpty: T.chat.pending.length === 0 };""")
        self.assertEqual(out["throttled"], [True, []])
        self.assertEqual(out["painted"], [["streams", [["x", "Hello **wor"]]]])
        self.assertIn('class="text"', out["html"])
        self.assertEqual(out["held"], [["tool arrive", "5"], ["streams", [["x", "Hello **wor"]]]])
        self.assertEqual(out["late"], out["held"], "a claimed id's late delta is dropped")
        self.assertEqual(out["after"], [["tool arrive", "5"], ["msg assistant", "6"]])
        self.assertFalse(out["arrive"], "the final swaps in without the arrival animation")

    def test_entries_land_above_open_bubbles(self):
        out = self.run_js("""
            delta('x', 'still writing', 1); T.paintStreams();
            T.appendMessage({ kind: 'system', text: 'note' }, true);
            return shape();""")
        self.assertEqual(out, [["msg system arrive", ""], ["streams", [["x", "still writing"]]]])

    def test_a_gap_drops_the_bubble_and_its_final_still_matches_it(self):
        out = self.run_js("""
            delta('a', 'one', 1); delta('b', 'two', 1); delta('a', 'three', 3); T.paintStreams();
            const gap = shape();
            appended(9, { kind: 'assistant', text: 'a final' }); drain();
            return { gap, after: shape() };""")
        self.assertEqual(out["gap"], [["streams", [["b", "two"]]]])
        self.assertEqual(out["after"], [["msg assistant", "9"], ["streams", [["b", "two"]]]])

    def test_idle_sweeps_an_orphan_and_clear_drops_everything(self):
        out = self.run_js("""
            T.STREAM_MS.idleGrace = 0;
            delta('x', 'cut off', 1); T.paintStreams();
            frame({ type: 'session.status', seq: 3, data: { status: 'idle' } }); drain();
            const kept = shape();
            await wait(10);
            const swept = shape();
            delta('y', 'mid', 1); T.paintStreams();
            T.clearStreams();
            // Held through a resync, a delta is dropped rather than drawn later.
            T.chat.held = [];
            delta('z', 'during resync', 1); T.paintStreams();
            const held = [shape(), T.chat.held.length];
            T.chat.held = null;
            return { kept, swept, cleared: held };""")
        self.assertEqual(out["kept"], [["streams", [["x", "cut off"]]]], "the grace runs first")
        self.assertEqual(out["swept"], [])
        self.assertEqual(out["cleared"], [[], 0])


if __name__ == "__main__":
    unittest.main()
