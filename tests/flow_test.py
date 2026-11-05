"""返事の流れ：判断 → 窓口 → 書く（2〜3案から選ぶ）→ 送る。待つ間に会話が進んだ時の、通す・書き直す・黙る。"""
import asyncio
import json
import os
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from harness import compare, writer
from harness.agent import Agent
from harness.outbox import Intent, Outbox
from harness.vault import Vault
from tests.fakes import FakeJev, FakeLLM, FakeSender, copy_template, msg

LIVE, SHADOW, COMPARE = "100000000000000001", "100000000000000002", "100000000000000003"


def cands(*texts):
    return json.dumps({"candidates": [{"messages": [t]} for t in texts]}, ensure_ascii=False)


def make(jev_rule=None, llm=None, wait=0.0):
    clock = {"t": datetime(2026, 11, 9, 20, 0)}    # 月曜の夜（予定なし）
    sender = FakeSender()
    a = Agent(Vault(copy_template()), FakeJev(jev_rule), llm or FakeLLM([]), sender,
              clock=lambda: clock["t"], notice=lambda p, addr: wait)
    a.quiet_sec = 0.05
    # 宛先の無い発言には混ざらない（混ざる確率の抽選でテストが揺れないように）
    a.persona.choose = lambda scene: None if scene["addressed"] else "none"
    sender.agent = a
    return a, sender, clock


def pov_ok(state, name, q):
    return 0.9 if name.startswith("c") else None


class WriterTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(writer.parse(cands("a", "b"), "normal"), [["a"], ["b"]])
        self.assertEqual(writer.parse(json.dumps({"messages": ["x", "y", "z"]}), "short"), [["x"]])
        self.assertEqual(writer.parse('{"candidates": [{"reaction": "😂"}]}', "reaction", ["😂"]), ["😂"])
        self.assertEqual(writer.parse("壊れた", "normal"), [])

    def test_guard_and_pov_pick_one(self):
        """敬語の案は検査で落とし、残りから立場チェックの一番高い案を選ぶ。"""
        def rule(s, n, q):
            return {"c0": 0.5, "c1": 0.8}.get(n)
        a, _, _ = make(rule, FakeLLM([cands("そうですね", "それは違う", "うん知ってる")]))
        out = asyncio.run(writer.write(a.llm, a.jev, a.vault, "あかり", {"今": "x"}, "normal",
                                       a.persona.guards()))
        self.assertEqual(out, ["うん知ってる"])

    def test_rewrite_once_then_give_up(self):
        a, _, _ = make(pov_ok, FakeLLM([cands("はいそうです"), cands("ありがとうございます")]))
        out = asyncio.run(writer.write(a.llm, a.jev, a.vault, "あかり", {}, "normal", a.persona.guards()))
        self.assertIsNone(out)
        self.assertEqual(len(a.llm.calls), 2)

    def test_system_prompt_follows_edits(self):
        """SOUL.md を Obsidian で直したら、次に書く時の system に入る（再起動しない）。"""
        a, _, _ = make()
        self.assertIn("古本屋", writer.system_prompt(a.vault))
        p = a.vault.path("Agents/SOUL.md")
        time.sleep(0.02)
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\nstatus: active\n---\nあなたは喫茶店の店主。\n")
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        self.assertTrue(writer.system_prompt(a.vault).startswith("あなたは喫茶店の店主。"))
        self.assertNotIn("date:", writer.system_prompt(a.vault))


class OutboxTest(unittest.TestCase):
    def test_one_window_dedupes_and_orders(self):
        t = datetime(2026, 11, 9, 20, 0)
        target = {"id": "5", "author": "ミナ", "text": "x", "author_id": "u"}
        o = Outbox()
        self.assertTrue(o.add(Intent("pickup", "c", "normal", t, target=target)))
        self.assertTrue(o.add(Intent("late", "c", "normal", t, target=target)))       # 同じ発言なら急ぎの方を残す
        self.assertFalse(o.add(Intent("reply", "c", "normal", t, target=target)))
        o.add(Intent("start", "c", "normal", t))
        self.assertEqual(o.take(t).source, "late")
        o.mark("5")
        self.assertFalse(o.add(Intent("reply", "c", "normal", t, target=target)))      # 返事済みには二度返さない
        self.assertIsNone(o.take(t + timedelta(minutes=11)))                          # 古い話題は捨てる


async def say(a, clock, id, text, author="ミナ", **kw):
    m = msg(id, LIVE, author, text, at=clock["t"], **kw)
    await a.on_message(m)
    return m


class FlowTest(unittest.TestCase):
    def test_addressed_reply_is_sent_as_reply(self):
        async def go():
            a, s, clock = make(pov_ok, FakeLLM([cands("どした？")]))
            await say(a, clock, "1", "あかり起きてる？")
            await asyncio.sleep(0.3)
            return a, s
        a, s = asyncio.run(go())
        self.assertEqual(s.sent, [(LIVE, ["どした？"], "1")])
        self.assertIn("1", a.outbox.answered)
        self.assertEqual(a.h.data["counts"]["total"], 1)
        user = a.llm.calls[0][1]["content"]
        self.assertIn("【返信先】", user)
        self.assertIn("【あなたが最近言ったこと】", user)
        self.assertNotIn("出どころ", user)

    def test_moved_on_then_jev_says_pass(self):
        """書いている間に別の発言が来た → Jev が「通す」→ そのまま送る（返信の形で宛先を示す）。"""
        def rule(s, n, q):
            return "通す" if n == "v" else pov_ok(s, n, q)
        async def go():
            a, s, clock = make(rule, FakeLLM([cands("おるよ")]), wait=0.3)
            await say(a, clock, "1", "あかりいる？")
            await asyncio.sleep(0.15)
            await say(a, clock, "2", "トオルもいる？", author="ミナ")
            await asyncio.sleep(0.6)
            return a, s
        a, s = asyncio.run(go())
        self.assertEqual(s.sent, [(LIVE, ["おるよ"], "1")])
        v_calls = [c for c in a.jev.calls if "v" in c[1]]
        self.assertTrue(v_calls)
        self.assertIn("トオルもいる？", v_calls[-1][0])          # 書き始めた後の発言を見て判断した
        self.assertNotIn("トオルもいる？", a.llm.calls[0][1]["content"])   # 書いた時点ではまだ無かった

    def test_moved_on_then_jev_says_silent(self):
        def rule(s, n, q):
            return "黙る" if n == "v" else pov_ok(s, n, q)
        async def go():
            a, s, clock = make(rule, FakeLLM([cands("おるよ")]), wait=0.3)
            await say(a, clock, "1", "あかりいる？")
            await asyncio.sleep(0.15)
            await say(a, clock, "2", "あ、もう寝るわ")
            await asyncio.sleep(0.6)
            return s
        self.assertEqual(asyncio.run(go()).sent, [])

    def test_moved_on_then_rewrite_once(self):
        """「書き直す」→ 新しい発言まで入れて1回だけ書き直す。書き直した後は通すか黙るかだけを聞く。"""
        answers = iter(["書き直す", "通す"])
        seen = []
        def rule(s, n, q):
            if n == "v":
                seen.append(sorted(q["criteria"]))
                return next(answers)
            return pov_ok(s, n, q)
        async def go():
            llm = FakeLLM([cands("おるよ"), cands("ミナも夜ふかし？")], delays=[0, 0.4])
            a, s, clock = make(rule, llm, wait=0.3)
            await say(a, clock, "1", "あかりいる？")
            await asyncio.sleep(0.15)
            await say(a, clock, "2", "私も今帰ってきた")
            await asyncio.sleep(0.35)       # 書き直している最中
            await say(a, clock, "3", "眠いわ")
            await asyncio.sleep(0.8)
            return a, s
        a, s = asyncio.run(go())
        self.assertEqual(s.sent, [(LIVE, ["ミナも夜ふかし？"], "1")])
        self.assertIn("私も今帰ってきた", a.llm.calls[1][1]["content"])     # 書き直しは新しい発言を入れて書いた
        self.assertEqual(seen[-1], ["通す", "黙る"])                       # 書き直しの後は2択

    def test_long_notice_is_not_counted_as_writing(self):
        """気付くまでの間が3分を超えても、書いた返事を捨てない。"""
        a, _, clock = make()
        sent = []

        async def compose(it):
            return ["起きてる"]

        async def deliver(it, out, pres):
            sent.append(out)

        def notice(pres, addressed):
            clock["t"] += timedelta(minutes=5)
            return 0
        a.compose, a.deliver, a.notice = compose, deliver, notice
        asyncio.run(a.handle(Intent("reply", "c", "short", clock["t"])))
        self.assertEqual(sent, [["起きてる"]])

    def test_time_in_queue_counts_as_notice(self):
        """窓口で待った分は、気付くまでの間から引く。"""
        a, _, clock = make(wait=100)
        pres = a.persona.presence(clock["t"])
        it = Intent("reply", "c", "short", clock["t"] - timedelta(seconds=30))
        self.assertEqual(a.wait_sec(it, pres, clock["t"]), 70)
        it.at = clock["t"] - timedelta(minutes=5)
        self.assertEqual(a.wait_sec(it, pres, clock["t"]), 0)
        self.assertEqual(a.wait_sec(Intent("late", "c", "short", clock["t"]), pres, clock["t"]), 0)

    def test_addressed_is_noticed_sooner(self):
        """呼ばれたら9割が5〜40秒。残りも5分まで。呼ばれていなければ7割のまま。"""
        a, _, _ = make()
        a.notice = None
        pres = SimpleNamespace(busy=False)
        cases = ((0.8, True, 5, 40), (0.8, False, 60, 300), (0.97, True, 60, 300), (0.97, False, 300, 900))
        for r, addressed, low, high in cases:
            with patch("harness.agent.random.random", return_value=r):
                self.assertTrue(low <= a.notice_sec(pres, addressed) <= high, (r, addressed))

    def _compare(self):
        a, s, clock = make()
        a.vault.load = lambda rel, parse, real=a.vault.load: (
            {**real(rel, parse), "compare_with": {"id": "rival", "name": "Minato"}} if rel == "Agents/config.yaml"
            else real(rel, parse))
        at = clock["t"]
        hi = msg("1", SHADOW, "ミナ", "ねむい", at, url="https://d/1")
        rival = msg("2", SHADOW, "感情AI", "（ミナへの返信） 寝ろ", at, bot=True, url="https://d/2", reply_to="1")
        rival["author_id"] = "rival"
        return a, s, clock, hi, rival, Intent("reply", SHADOW, "short", at, target=hi)

    def test_compare_waits_for_the_rival_then_lines_up(self):
        a, s, clock, hi, rival, it = self._compare()

        async def go():
            await a.on_message(hi)
            await a.compare.mine(it, "俺も", clock["t"])
            self.assertEqual(s.sent, [])                                   # Minato を待つ
            await a.on_message(rival)
            await a.on_message({**rival, "id": "3", "text": "おやすみ", "reply_to": None})
        asyncio.run(go())
        self.assertEqual(s.sent, [(COMPARE, ["-# [寝ろ](<https://d/2>)
俺も"], None)])

    def test_compare_when_the_rival_is_silent(self):
        a, s, clock, hi, _, it = self._compare()

        async def go():
            await a.on_message(hi)
            await a.compare.mine(it, "俺も", clock["t"])
            await a.compare.tick(clock["t"] + timedelta(minutes=2))
            self.assertEqual(s.sent, [])
            await a.compare.tick(clock["t"] + timedelta(minutes=3))
        asyncio.run(go())
        self.assertEqual(s.sent[0][1], ["-# Minatoは黙っていた（[返信先の発言](<https://d/1>)）\n俺も"])

    def test_compare_when_i_am_silent_then_late(self):
        a, s, clock, hi, rival, it = self._compare()

        async def go():
            await a.on_message(hi)
            await a.on_message(rival)
            await a.compare.tick(clock["t"] + timedelta(minutes=6))
            self.assertEqual(s.sent[0][1], ["-# [寝ろ](<https://d/2>)\n（あかりは黙った）"])
            await a.compare.mine(it, "俺も", clock["t"])                    # 遅れて書き終えたら、並べ直して出す
        asyncio.run(go())
        self.assertEqual(s.sent[1][1], ["-# [寝ろ](<https://d/2>)\n俺も"])

    def test_compare_takes_only_the_first_rival_message(self):
        a, s, clock, hi, rival, it = self._compare()

        async def go():
            await a.on_message(hi)
            await a.on_message(rival)
            second = {**rival, "id": "3", "text": "おやすみ", "url": "https://d/3", "reply_to": None}
            await a.on_message(second)
            await a.compare.mine(it, "俺も", clock["t"])
        asyncio.run(go())
        self.assertEqual(s.sent[0][1], ["-# [寝ろ](<https://d/2>)\n俺も"])

    def test_compare_line_is_safe(self):
        self.assertEqual(compare.line("（ミナの2時間前の発言への返信） a\n[b]"), "a ［b］")
        self.assertEqual(compare.line(""), "（本文なし）")
        self.assertEqual(len(compare.line("あ" * 300)), compare.TEXT_MAX)

    def test_shadow_posts_to_compare_channel(self):
        async def go():
            a, s, clock = make(pov_ok, FakeLLM([cands("どした？")]))
            await a.on_message(msg("1", SHADOW, "ミナ", "あかり起きてる？", at=clock["t"]))
            await asyncio.sleep(0.3)
            return s
        sent = asyncio.run(go()).sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], COMPARE)
        self.assertTrue(sent[0][1][0].endswith("どした？"))

    def test_after_read_records_and_waits_for_six(self):
        """ナギ宛ての発言は、後の6件か10分を待ってから、前後と一緒に Jev 1回で読む。"""
        def rule(s, n, q):
            if n == "record":
                return "常識" if "3円" in s.split("【読む発言】")[1].split("\n")[0] else "しない"
            return pov_ok(s, n, q)
        llm = FakeLLM(lambda ms: cands("へー") if "【型】" in ms[-1]["content"] else
                      json.dumps({"name": "レジ袋は1枚3円", "body": "コンビニのレジ袋は1枚3円くらい。", "keywords": ["レジ袋"]}))
        async def go():
            a, s, clock = make(rule, llm)
            await say(a, clock, "1", "あかり、レジ袋は3円だよ")
            await asyncio.sleep(0.3)
            await a.tick()
            self.assertFalse(a.vault.exists("01 Lessons/01 常識/レジ袋は1枚3円.md"))   # まだ読まない
            clock["t"] += timedelta(minutes=11)
            await a.tick()
            return a
        a = asyncio.run(go())
        self.assertTrue(a.vault.exists("01 Lessons/01 常識/レジ袋は1枚3円.md"))

    def test_state_and_logs_live_in_the_folder(self):
        async def go():
            a, s, clock = make(pov_ok, FakeLLM([cands("どした？")]))
            await say(a, clock, "1", "あかり起きてる？")
            await asyncio.sleep(0.3)
            a.flush(clock["t"], force=True)
            return a
        a = asyncio.run(go())
        self.assertTrue(a.vault.exists("03 Logs/chat/2026-11-09.jsonl"))
        self.assertEqual(a.vault.json("02 State/harness.json")["counts"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
