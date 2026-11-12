"""評価の再生: 正解候補の判定と、合計の数え方（FakeJev。実 Jev は使わない）。"""
import asyncio
import json
import os
import tempfile
import unittest

from harness import replay
from harness.vault import Vault
from tests.fakes import FakeJev


def lesson(root, kind, name, date, words):
    d = os.path.join(root, "01 Lessons", kind)
    os.makedirs(d, exist_ok=True)
    w = "".join(f"- {x}\n" for x in words)
    with open(os.path.join(d, name + ".md"), "w", encoding="utf-8") as f:
        f.write(f"---\ndate: {date}\nstatus: active\n種類: {kind}\n思い出す言葉:\n{w}---\n{name}の本文\n")


def rating(at, text, comment, ctx=("A: こんにちは", "B: 水飲む金ある？")):
    return {"at": at, "msg_at": at, "text": text, "comment": comment, "label": "流れとズレてる", "context": list(ctx)}


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="replay-")
        lesson(self.root, "常識", "水は安い", "2026-11-07", ["水", "安い"])        # 以降・語が合う
        lesson(self.root, "常識", "昔の水の話", "2026-11-01", ["水"])             # 評価より前
        lesson(self.root, "反省", "金額の話", "2026-11-09", ["円"])               # 以降・語が合う（別の件）
        lesson(self.root, "常識", "無関係", "2026-11-09", ["ギター"])             # 以降・語が合わない
        self.vault = Vault(self.root)
        self.rows = replay.context.lesson_rows(self.vault)
        self.r1 = rating("2026-11-06T01:37:01", "水飲む金あればエナドリ買うわ", "水の方が安いですよ")
        self.r2 = rating("2026-11-08T03:00:00", "寝る", "なんの話ですか？")

    def test_candidates(self):
        self.assertEqual([r.name for r in replay.candidates(self.rows, self.r1)], ["水は安い"])
        self.assertEqual(replay.candidates(self.rows, self.r2), [])

    def test_convo_text(self):
        self.assertEqual(replay.convo_text(self.r1), "A: こんにちは\nB: 水飲む金ある？")

    def _run(self, jev, ratings):
        return asyncio.run(replay.replay(jev, self.vault, ratings, "ナギ"))

    def test_hit_miss_none_and_total(self):
        r3 = rating("2026-11-09T10:00:00", "48000円ある", "48000円あります")     # 候補: 金額の話
        def rule(state, name, q):
            if "記憶の表" not in q["instructions"]:
                return 1.0
            line = state.split("【記憶の表】\n")[1].split("\n")[int(name[1:])]
            return 0.9 if "水は安い" in line else 0.0
        res = self._run(FakeJev(rule), [self.r1, self.r2, r3])
        self.assertEqual([x["verdict"] for x in res], [replay.HIT, replay.NONE, replay.MISS])
        self.assertEqual(res[0]["got"], ["01 Lessons/常識/水は安い.md"])
        self.assertIn(("水は安い", "常識"), res[0]["picked"])
        self.assertEqual(replay.total(res), (1, 2))
        self.assertIn("合計: ヒット 1 / 正解候補があった件 2（全3件）", replay.report(res))

    def test_nothing_picked_is_miss(self):
        res = self._run(FakeJev(), [self.r1])
        self.assertEqual(res[0]["verdict"], replay.MISS)
        self.assertEqual(replay.total(res), (0, 1))

    def test_main_fake_smoke(self):
        os.makedirs(os.path.join(self.root, "03 Logs", "ratings"))
        with open(os.path.join(self.root, "03 Logs", "ratings", "t.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(self.r1, ensure_ascii=False) + "\n")
        self.assertEqual(replay.main([self.root, "--fake"]), 0)


if __name__ == "__main__":
    unittest.main()
