"""送った返事の記録：said（軽い）と prompts（全文の trace）を同じ id で書く。送れた分だけ said に書く。
評価は Discord の発言 ID で said を引く。黙った判断も prompts に残す。"""
import asyncio
import json
import os
import re
import unittest
from datetime import datetime, timedelta

from harness import writer
from harness.outbox import Intent
from personas.nagi.engine import anger
from tests.fakes import FakeLLM
from tests.flow_test import LIVE, SHADOW, cands, make, pov_ok, say
from tests import nagi_test


def rows(a, kind, t):
    p = a.vault.path(f"03 Logs/{kind}/{t:%Y-%m}.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


class SaidLogTest(unittest.TestCase):
    def test_sent_reply_writes_said_and_trace_with_same_id(self):
        async def go():
            a, s, clock = make(pov_ok, FakeLLM([cands("どした？")]))
            await say(a, clock, "1", "あかり起きてる？")
            await asyncio.sleep(0.3)
            return a, clock
        a, clock = asyncio.run(go())
        said = rows(a, "said", clock["t"])
        self.assertEqual(len(said), 1)
        r = said[0]
        self.assertTrue(r["id"] and r["state"] and r["started_at"])
        self.assertEqual(r["message_ids"], ["me1"])                 # 送れた Discord の発言 ID
        self.assertNotIn("sections", r)                             # said は軽いまま
        trace = [x for x in rows(a, "prompts", clock["t"]) if x["id"] == r["id"]]
        self.assertEqual(len(trace), 1)
        tr = trace[0]
        self.assertEqual((tr["event"], tr["outcome"], tr["message_ids"]), ("reply", "sent", ["me1"]))
        d = tr["drafts"][0]
        self.assertIn("返信先", d["sections"])                        # 節の全文
        self.assertEqual(d["state"], r["state"])
        at = d["write"]["attempts"][0]
        self.assertEqual(at["model"], "fake")                        # 実際に答えたモデル
        self.assertEqual(at["candidates"], [["どした？"]])
        self.assertEqual(at["chosen"], ["どした？"])
        self.assertFalse(d["write"]["rewrote"])

    def test_state_is_taken_when_writing_starts(self):
        a, s, clock = make(pov_ok, FakeLLM([cands("おう")]))
        target = {"id": "1", "author": "ミナ", "author_id": "u-ミナ", "text": "あかり", "at": clock["t"].isoformat()}
        it = Intent("reply", LIVE, "normal", clock["t"], target=target)
        it.extra["trace"] = a.new_trace(it)
        start = clock["t"]

        async def go():
            out = await a.compose(it)
            clock["t"] = start + timedelta(minutes=15)                # 気付くまでの間が経ってから送る
            a.persona.snapshot = lambda t, target=None: "送った時点の値"
            await a.deliver(it, out, a.persona.presence(clock["t"]))
        asyncio.run(go())
        r = rows(a, "said", start)[0]
        self.assertEqual(r["started_at"], start.isoformat(timespec="seconds"))
        self.assertEqual(r["at"], (start + timedelta(minutes=15)).isoformat(timespec="seconds"))
        self.assertNotEqual(r["state"], "送った時点の値")

    def test_send_failure_writes_no_said(self):
        async def go():
            a, s, clock = make(pov_ok, FakeLLM([cands("どした？")]))
            s.fail = True
            await say(a, clock, "1", "あかり起きてる？")
            await asyncio.sleep(0.3)
            return a, clock
        a, clock = asyncio.run(go())
        self.assertEqual(rows(a, "said", clock["t"]), [])
        self.assertEqual([x["outcome"] for x in rows(a, "prompts", clock["t"]) if x["event"] == "reply"], ["send_failed"])
        self.assertNotIn("1", a.outbox.answered)                      # 送れていないので返事済みにしない
        self.assertEqual(a.h.data.get("counts", {}).get("total", 0), 0)

    def test_failed_reaction_writes_no_said(self):
        a, s, clock = make(pov_ok, FakeLLM([]))
        s.fail = True
        target = {"id": "1", "author": "ミナ", "author_id": "u-ミナ", "text": "x"}
        asyncio.run(a.deliver(Intent("reply", LIVE, "reaction", clock["t"], target=target), "😂",
                              a.persona.presence(clock["t"])))
        self.assertEqual(rows(a, "said", clock["t"]), [])

    def test_rating_finds_said_by_discord_id(self):
        """同じ本文の返事が2つあっても、評価された発言の ID で当たる方を引く。ID の無い古い行は本文で引く。"""
        a, s, clock = make()
        at = clock["t"].isoformat(timespec="seconds")
        a.logs.said({"id": "old", "at": at, "channel": LIVE, "text": "ねむい", "state": "古い行"})
        a.logs.said({"id": "a", "at": at, "channel": LIVE, "text": "ねむい", "state": "A", "message_ids": ["m1"]})
        a.logs.said({"id": "b", "at": at, "channel": LIVE, "text": "ねむい", "state": "B", "message_ids": ["m2"]})
        self.assertEqual(a.logs.find_said("ねむい", at, "m1")["id"], "a")
        self.assertEqual(a.logs.find_said("ねむい", at, "m2")["id"], "b")
        self.assertEqual(a.logs.find_said("ねむい", at, "m9")["id"], "old")    # ID で当たらなければ古い行だけ本文で
        self.assertEqual(a.logs.find_said("ねむい", at)["id"], "old")

    def test_rate_uses_message_id(self):
        a, s, clock = make()
        at = clock["t"].isoformat(timespec="seconds")
        a.logs.said({"id": "a", "at": at, "channel": LIVE, "text": "ねむい", "state": "A", "message_ids": ["m1"],
                     "version": "x", "source": "reply"})
        a.logs.said({"id": "b", "at": at, "channel": LIVE, "text": "ねむい", "state": "B", "message_ids": ["m2"],
                     "version": "x", "source": "reply"})
        asyncio.run(a.rate({"message_id": "m1", "channel": LIVE, "is_me": True, "label": "自然", "comment": "",
                            "text": "ねむい", "rater": "x", "at": at}))
        asyncio.run(a.rate({"message_id": "x9", "channel": LIVE, "is_me": False, "label": "自然", "comment": "",
                            "text": "人の発言", "rater": "x", "at": at}))
        got = rows(a, "ratings", clock["t"])
        self.assertEqual((got[0]["state"], got[0]["said_id"]), ("A", "a"))      # 評価から prompts の trace を引ける
        self.assertIsNone(got[1]["said_id"])

    def test_judge_silence_is_logged(self):
        async def go():
            a, s, clock = make(lambda st, n, q: 0.2 if n == "interest" else None)
            await say(a, clock, "1", "今日寒いね")                      # 宛先なし → make の choose が none
            await asyncio.sleep(0.3)
            return a, s, clock
        a, s, clock = asyncio.run(go())
        self.assertEqual(s.sent, [])
        j = [x for x in rows(a, "prompts", clock["t"]) if x["event"] == "judge"]
        self.assertEqual(len(j), 1)
        self.assertEqual((j[0]["decision"], j[0]["target_id"]), ("none", "1"))
        self.assertIn("answers", j[0])

    def test_write_failure_is_logged(self):
        async def go():
            a, s, clock = make(pov_ok, FakeLLM([None, None]))           # LLM が全滅
            await say(a, clock, "1", "あかり起きてる？")
            await asyncio.sleep(0.3)
            return a, clock
        a, clock = asyncio.run(go())
        self.assertEqual(rows(a, "said", clock["t"]), [])
        tr = [x for x in rows(a, "prompts", clock["t"]) if x["event"] == "reply"]
        self.assertEqual(tr[0]["outcome"], "write_failed")
        self.assertEqual([x["model"] for x in tr[0]["drafts"][0]["write"]["attempts"]], [None, None])
        self.assertTrue(tr[0]["drafts"][0]["write"]["rewrote"])

    def test_recheck_silence_is_logged_with_jev_answer(self):
        def rule(s, n, q):
            return "黙る" if n == "v" else pov_ok(s, n, q)

        async def go():
            a, s, clock = make(rule, FakeLLM([cands("おるよ")]), wait=0.3)
            await say(a, clock, "1", "あかりいる？")
            await asyncio.sleep(0.15)
            await say(a, clock, "2", "あ、もう寝るわ")
            await asyncio.sleep(0.6)
            return a, clock
        a, clock = asyncio.run(go())
        tr = [x for x in rows(a, "prompts", clock["t"]) if x["event"] == "reply"]
        self.assertEqual(tr[0]["outcome"], "recheck_silent")
        self.assertEqual(tr[0]["rechecks"][-1]["verdict"], "黙る")
        self.assertEqual(tr[0]["rechecks"][-1]["new"], ["2"])
        self.assertEqual(tr[0]["rechecks"][-1]["probs"]["黙る"], 1.0)

    def test_shadow_reply_is_logged_as_shadow(self):
        a, s, clock = make(pov_ok, FakeLLM([]))
        target = {"id": "1", "author": "ミナ", "author_id": "u-ミナ", "text": "x"}
        asyncio.run(a.deliver(Intent("reply", SHADOW, "normal", clock["t"], target=target), ["俺も"],
                              a.persona.presence(clock["t"])))
        r = rows(a, "said", clock["t"])[0]
        self.assertTrue(r["shadow"])
        self.assertNotIn("message_ids", r)                            # 影の返事は本文で突き合わせる
        self.assertEqual(rows(a, "prompts", clock["t"])[0]["outcome"], "shadow")


def two_lines():
    return json.dumps({"candidates": [{"messages": ["一行目", "二行目"]}]}, ensure_ascii=False)


class PartialSendTest(unittest.TestCase):
    """2通に分けた返事が1通目しか出なかった時。said・体・約束は送れた1通目だけで動かす。"""

    def _run(self, **fail):
        async def go():
            a, s, clock = make(pov_ok, FakeLLM([two_lines()]))
            a.persona.choose = lambda scene: "normal" if scene["addressed"] else "none"
            for k, v in fail.items():
                setattr(s, k, v)
            delivered, asked = [], []
            a.persona.on_delivered = lambda it, out: delivered.append(list(out))
            a.persona.sent_questions = lambda out: asked.append(list(out)) or {}
            await say(a, clock, "1", "あかり起きてる？")
            await asyncio.sleep(0.3)
            return a, s, clock, delivered, asked
        return asyncio.run(go())

    def _check(self, a, s, clock, delivered, asked):
        self.assertEqual(s.sent, [(LIVE, ["一行目"], "1")])
        r = rows(a, "said", clock["t"])
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["text"], r[0]["message_ids"], r[0]["partial"]), ("一行目", ["me1"], True))
        tr = [x for x in rows(a, "prompts", clock["t"]) if x["event"] == "reply"][0]
        self.assertEqual((tr["outcome"], tr["partial"]), ("sent", True))
        self.assertEqual(delivered, [["一行目"]])                     # 送っていない二行目で体・約束・出費を動かさない
        self.assertEqual(asked, [["一行目"]])
        self.assertIn("1", a.outbox.answered)                         # 1通目は出たので返事済み・数える
        self.assertEqual(a.h.data["counts"]["total"], 1)

    def test_exception_before_second_message(self):
        self._check(*self._run(raise_at=2))

    def test_second_message_not_sent(self):
        self._check(*self._run(stop_at=2))

    def test_full_send_is_not_partial(self):
        a, s, clock, delivered, _ = self._run()
        r = rows(a, "said", clock["t"])[0]
        self.assertEqual(r["text"], "一行目\n二行目")
        self.assertNotIn("partial", r)
        self.assertEqual(delivered, [["一行目", "二行目"]])


class GatewaySendTest(unittest.TestCase):
    """gateway.send の本物のコード。入力中の表示や接続の失敗が2通目の前で起きても、1通目の ID を返す。"""

    def _send(self, typing_error=None, send_error_at=None):
        from types import SimpleNamespace
        from harness.gateway import Gateway

        class Typing:
            def __init__(self, ch):
                self.ch = ch

            async def __aenter__(self):
                self.ch.typings += 1
                if typing_error and self.ch.typings == 2:
                    raise typing_error

            async def __aexit__(self, *a):
                return False

        class Channel:
            def __init__(self):
                self.typings, self.posted = 0, []

            def typing(self):
                return Typing(self)

            async def send(self, text):
                if send_error_at == len(self.posted) + 1:
                    raise OSError("接続が切れた")
                self.posted.append(text)
                return SimpleNamespace(id=100 + len(self.posted))

        ch = Channel()
        gw = SimpleNamespace(get_channel=lambda i: ch)
        ids = asyncio.run(Gateway.send(gw, "1", ["一行目", "二行目"], typing=[0.001, 0.001]))
        return ids, ch.posted

    def test_typing_failure_returns_sent_ids(self):
        self.assertEqual(self._send(typing_error=RuntimeError("typing")), (["101"], ["一行目"]))

    def test_connection_error_returns_sent_ids(self):
        self.assertEqual(self._send(typing_error=ConnectionResetError()), (["101"], ["一行目"]))
        self.assertEqual(self._send(send_error_at=2), (["101"], ["一行目"]))

    def test_first_failure_returns_empty(self):
        self.assertEqual(self._send(send_error_at=1), ([], []))

    def test_all_sent(self):
        self.assertEqual(self._send(), (["101", "102"], ["一行目", "二行目"]))


class WriterTraceTest(unittest.TestCase):
    def test_dropped_reasons_and_pick_scores(self):
        a, s, clock = make(lambda st, n, q: 0.9 if n == "c0" else None,
                           FakeLLM([cands("はい、そうですね", "ほんまそれ")]))
        tr = {}
        out = asyncio.run(writer.write(a.llm, a.jev, a.vault, "あかり", {"今": "x"}, "normal",
                                       [re.compile("です")], trace=tr))
        self.assertEqual(out, ["ほんまそれ"])
        at = tr["attempts"][0]
        self.assertEqual(at["dropped"], [{"candidate": 0, "why": "guard", "detail": "です"}])
        self.assertEqual(at["passed"], [1])
        self.assertEqual(at["pick"], [{"案0": 0.9}])
        self.assertEqual(at["score"], 0.9)


class NagiSnapshotTest(unittest.TestCase):
    def test_snapshot_has_wallet_and_anger(self):
        t = datetime(2026, 11, 12, 13, 0)
        a, _ = nagi_test.make(t)
        anger.hit(a.persona.anger_state.data, "u1", "ミナ", "attack", "うざ", t)
        snap = a.persona.snapshot(t, "u1")
        self.assertRegex(snap, r"残高[\d,]+円 今日使った[\d,]+円 今日使ってよい[\d,]+円")
        self.assertRegex(snap, r"相手への怒り\d+")
        self.assertNotIn("怒り", a.persona.snapshot(t, "u2"))


if __name__ == "__main__":
    unittest.main()
