"""ナギの部品を本体に差し込んで動くか（見本のフォルダの設定を persona: nagi に替えて試す）。"""
import asyncio
import unittest
from datetime import datetime

from harness.agent import Agent
from harness.vault import Vault
from personas.nagi.engine import decide
from tests.fakes import FakeJev, FakeLLM, FakeSender, copy_template


def make(t):
    root = copy_template()
    p = f"{root}/Agents/config.yaml"
    text = open(p, encoding="utf-8").read().replace("persona: basic", "persona: nagi")
    open(p, "w", encoding="utf-8").write(text)
    clock = {"t": t}
    return Agent(Vault(root), FakeJev(lambda s, n, q: 0.9 if n in ("did", "ate") else ("500" if n == "yen" else None)),
                 FakeLLM([]), FakeSender(), clock=lambda: clock["t"]), clock


class NagiTest(unittest.TestCase):
    def test_schedule_flows_into_timetable(self):
        a, _ = make(datetime(2026, 11, 9, 13, 0))      # 月曜 13時（古本屋のバイト 12〜18時）
        p = a.persona.presence(a.now())
        self.assertEqual(p.state, "バイト中")
        self.assertTrue(p.busy)
        self.assertIn(0, decide.BAITO_DAYS)

    def test_tick_moves_body_and_sent_reply_spends(self):
        a, clock = make(datetime(2026, 11, 10, 15, 0))  # 火曜（予定なし）
        before = a.persona.b["money"]
        for _ in range(3):
            asyncio.run(a.tick())
        qs = a.persona.sent_questions(["コンビニで500円の唐揚げ買った"])
        self.assertIn("yen", qs)
        ans = asyncio.run(a.jev.ask("x", qs))
        a.persona.on_sent(["コンビニで500円の唐揚げ買った"], ans)
        self.assertEqual(a.persona.b["money"], before - 500)
        self.assertTrue(any("唐揚げ" in e for e in a.h.data["events"]["2026-11-10"]))

    def test_inner_is_words_not_numbers(self):
        a, _ = make(datetime(2026, 11, 10, 15, 0))
        s = a.persona.inner(a.now(), None)["体の状態"]
        self.assertIn("財布", s)
        self.assertNotIn("energy", s)

    def test_guards(self):
        a, _ = make(datetime(2026, 11, 10, 15, 0))
        g = a.persona.guards()
        self.assertTrue(any(x.search("そうですね") for x in g))
        self.assertTrue(any(x.search("あかりは眠い") for x in g))   # 自分を名前で呼ばない
        self.assertFalse(any(x.search("それな") for x in g))


if __name__ == "__main__":
    unittest.main()
