"""想起のタイミング：書き直しで最新の会話から引き直す／リアクション返信では想起を飛ばす。"""
import asyncio
import json
import unittest

from harness import people
from harness.outbox import Intent
from tests.flow_test import LIVE, cands, make, pov_ok, say
from tests.fakes import FakeLLM
from tests.people_test import PAGE, T


def recall_rule(s, n, q):
    """種類・行の想起の問いには全部高く答える（想起が走れば節が付く）。"""
    if n[0] in "kr" and n != "v":
        return 0.9
    return pov_ok(s, n, q)


def kind_calls(a):
    return [c for c in a.jev.calls if "k0" in c[1]]


class RewriteRecallTest(unittest.TestCase):
    def test_rewrite_recalls_again_with_latest_conversation(self):
        answers = iter(["書き直す", "通す"])

        def rule(s, n, q):
            return next(answers) if n == "v" else recall_rule(s, n, q)

        async def go():
            llm = FakeLLM([cands("おるよ"), cands("ミナも夜ふかし？")], delays=[0, 0.4])
            a, s, clock = make(rule, llm, wait=0.3)
            await say(a, clock, "1", "あかりいる？")
            await asyncio.sleep(0.15)
            await say(a, clock, "2", "私も今帰ってきた")
            await asyncio.sleep(0.8)
            return a
        a = asyncio.run(go())
        ks = kind_calls(a)
        self.assertEqual(len(ks), 2)                          # 最初と書き直しで、想起が2回走った
        self.assertNotIn("私も今帰ってきた", ks[0][0])
        self.assertIn("私も今帰ってきた", ks[1][0])           # 引き直しは最新の会話で


class ReactionRecallTest(unittest.TestCase):
    def compose(self, source, kind):
        a, _, _ = make(recall_rule, FakeLLM([json.dumps({"candidates": [{"reaction": "😀"}]})
                                             if kind == "reaction" else cands("うん")]))
        a.vault.write_text("05 人物/ミナ.md", PAGE)
        target = {"id": "9", "author": "ミナ", "author_id": "u-ミナ", "text": "最近コーヒー淹れてる？", "channel": "c"}
        a.conv("c").add({**target, "at": T.isoformat(timespec="seconds"), "bot": False, "me": False})
        asyncio.run(a.compose(Intent(source, "c", kind, T, target=target)))
        return a, a.llm.calls[0][1]["content"]

    def test_reaction_reply_skips_recall_but_keeps_fixed_frames(self):
        a, user = self.compose("reply", "reaction")
        self.assertEqual(kind_calls(a), [])                   # 想起（Jev）を呼ばない
        self.assertNotIn("【覚えていること】", user)
        self.assertNotIn("【最近の日記】", user)
        self.assertIn(f"【{people.SECTION}】", user)          # 固定枠はそのまま付く

    def test_normal_reply_recalls(self):
        a, user = self.compose("reply", "normal")
        self.assertTrue(kind_calls(a))
        self.assertTrue("【覚えていること】" in user or "【最近の日記】" in user or "【スマホのメモ】" in user)
        self.assertIn(f"【{people.SECTION}】", user)

    def test_announce_reaction_is_not_changed(self):
        a, user = self.compose("announce", "reaction")
        self.assertTrue(kind_calls(a))                        # reply 以外は従来どおり想起する
