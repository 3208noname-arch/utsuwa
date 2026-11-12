"""近況：固定枠での想起、夜の蒸留（write が false の間は書かない、長すぎる案は保存しない）。"""
import asyncio
import json
import unittest
from datetime import date, datetime

from harness import digest, night
from harness.agent import Agent
from harness.outbox import Intent
from harness.vault import Vault
from tests.fakes import FakeJev, FakeLLM, FakeSender, copy_template

T = datetime(2026, 11, 12, 20, 0)
DAY = date(2026, 11, 12)

NOTE = """---
date: 2026-11-10
種類: 近況
status: active
---
# 体と生活
昼夜が逆転気味。財布が寂しい。

# 進行中のこと
レポートの締切が近い。

# 関係の変化
ミナと古本の話で近づいた。

# 最近の流れ
夜更かしが続いている。
"""
ANSWER = {"体と生活": "寝不足が続く。", "進行中のこと": "レポート。", "関係の変化": "ミナと仲が良い。", "最近の流れ": "夜型。"}


def agent(llm=None):
    return Agent(Vault(copy_template()), FakeJev(), llm or FakeLLM(lambda m: json.dumps(ANSWER)), FakeSender(),
                 clock=lambda: T, notice=lambda p, a: 0)


def sections(a):
    target = {"id": "9", "author": "ミナ", "author_id": "u1", "text": "やあ", "channel": "c"}
    s, _ = a.sections(Intent("reply", "c", "normal", T, target=target), T)
    return s


class RecallTest(unittest.TestCase):
    def test_given_when_note_exists_with_update_date(self):
        a = agent()
        a.vault.write_text(digest.REL, NOTE)
        s = sections(a)
        self.assertIn(digest.SECTION, s)
        self.assertTrue(s[digest.SECTION].startswith("（2026-11-10 更新、2日前）"))
        self.assertIn("レポートの締切", s[digest.SECTION])
        self.assertEqual(a.jev.calls, [])                    # Jev を介さない

    def test_not_given_when_missing_empty_or_inactive(self):
        a = agent()
        self.assertNotIn(digest.SECTION, sections(a))
        a.vault.write_text(digest.REL, "---\ndate: 2026-11-10\nstatus: active\n---\n")
        self.assertNotIn(digest.SECTION, sections(a))
        a.vault.write_text(digest.REL, NOTE.replace("status: active", "status: inactive"))
        self.assertNotIn(digest.SECTION, sections(a))

    def test_no_date_still_given(self):
        a = agent()
        a.vault.write_text(digest.REL, "# 体と生活\nねむい\n")
        self.assertTrue(sections(a)[digest.SECTION].startswith("（更新日は不明）"))

    def test_section_comes_in_prompt_text(self):
        from harness import writer
        a = agent()
        a.vault.write_text(digest.REL, NOTE)
        self.assertIn("【近況】", writer.sections_text(sections(a)))


class UpdateTest(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        a = agent()
        self.assertEqual(asyncio.run(digest.update(a, DAY, write_=False)), [(digest.REL, False)])
        self.assertFalse(a.vault.exists(digest.REL))

    def test_write_saves_with_date_and_sections(self):
        a = agent()
        self.assertEqual(asyncio.run(digest.update(a, DAY, write_=True)), [(digest.REL, True)])
        n = a.vault.note(digest.REL)
        self.assertEqual((str(n.meta["date"]), n.meta["種類"]), ("2026-11-12", "近況"))
        self.assertEqual([h for h in digest.HEAD if f"# {h}" in n.body], list(digest.HEAD))
        self.assertTrue(sections(a)[digest.SECTION].startswith("（2026-11-12 更新、今日）"))   # 書いた後は想起される

    def test_too_long_is_not_saved(self):
        for bad in [{**ANSWER, "最近の流れ": "あ" * 900}, {}, {h: "" for h in digest.HEAD}]:
            a = agent(FakeLLM(lambda m, bad=bad: json.dumps(bad)))
            self.assertEqual(asyncio.run(digest.update(a, DAY, write_=True)), [])
            self.assertFalse(a.vault.exists(digest.REL))
        a = agent(FakeLLM(["壊れた応答"]))
        self.assertEqual(asyncio.run(digest.update(a, DAY, write_=True)), [])

    def test_limit_is_in_bytes(self):
        ok = {**ANSWER, "最近の流れ": "あ" * 700}                 # 約2.1KB
        self.assertIsNone(digest.check(ok)[1])
        self.assertIn("長すぎる", digest.check({**ok, "進行中のこと": "い" * 200})[1])    # 2.7KB

    def test_existing_note_not_overwritten_by_bad_answer(self):
        a = agent(FakeLLM(lambda m: json.dumps({**ANSWER, "最近の流れ": "あ" * 900})))
        a.vault.write_text(digest.REL, NOTE)
        asyncio.run(digest.update(a, DAY, write_=True))
        self.assertIn("レポートの締切", a.vault.text(digest.REL))

    def test_material_has_recent_three_days_only(self):
        a = agent()
        a.vault.write_text("00 Diary/2026-11-10.md", "---\ndate: 2026-11-10\n---\n三日前の日記")
        a.vault.write_text("00 Diary/2026-11-09.md", "---\ndate: 2026-11-09\n---\n四日前の日記")
        a.h.data.setdefault("events", {})["2026-11-12"] = ["古本屋のバイトに行った"]
        a.vault.write_text(digest.REL, NOTE)
        m = digest.material(a, DAY)
        self.assertIn("三日前の日記", m)
        self.assertNotIn("四日前の日記", m)
        self.assertIn("古本屋のバイトに行った", m)
        self.assertIn("レポートの締切", m)                      # 今の近況を渡す

    def test_night_run_respects_write_false(self):
        a = agent()
        out = asyncio.run(night.run(a, datetime(2026, 11, 13, 4, 0)))   # config は write: false
        self.assertEqual(out["digest"], [(digest.REL, False)])
        self.assertFalse(a.vault.exists(digest.REL))


if __name__ == "__main__":
    unittest.main()
