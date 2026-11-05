import os
import time
import unittest

from harness.schedule import Schedule, now_text
from harness.vault import NotWritable, Vault, split
from tests.fakes import copy_template


def touch_later(path, text):
    """更新時刻が確実に変わるように、少し待ってから書く。"""
    time.sleep(0.02)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.root = copy_template()
        self.v = Vault(self.root)

    def test_split_removes_frontmatter(self):
        n = split("---\ndate: 2026-11-03\n教わった人:\n  - ミナ\n---\n本文\n")
        self.assertEqual(n.meta["教わった人"], ["ミナ"])
        self.assertEqual(n.body.strip(), "本文")
        self.assertNotIn("date", self.v.text("Agents/SOUL.md"))

    def test_reload_when_rewritten(self):
        self.assertIn("古本屋", self.v.text("Agents/SOUL.md"))
        touch_later(self.v.path("Agents/SOUL.md"), "---\nstatus: active\n---\nあなたは新しい人格。\n")
        self.assertEqual(self.v.text("Agents/SOUL.md"), "あなたは新しい人格。")

    def test_changes_detects_edit_add_remove(self):
        self.assertEqual(self.v.changes(), [])          # 初回は起点を取るだけ
        touch_later(self.v.path("Agents/AGENTS.md"), "直した")
        with open(self.v.path("01 Lessons/01 常識/新しい.md"), "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(self.v.path("01 Lessons/03 その他/その他_Temp.md"))
        got = self.v.changes()
        self.assertIn("Agents/AGENTS.md", got)
        self.assertIn("01 Lessons/01 常識/新しい.md", got)
        self.assertIn("01 Lessons/03 その他/その他_Temp.md", got)
        self.assertEqual(self.v.changes(), [])

    def test_changes_ignores_own_writes(self):
        self.v.changes()
        self.v.write_text("01 Lessons/01 常識/自分で書いた.md", "x")
        self.v.write_json("02 State/x.json", {})
        self.assertEqual(self.v.changes(), [])

    def test_cannot_write_agents(self):
        with self.assertRaises(NotWritable):
            self.v.write_text("Agents/SOUL.md", "x")
        with self.assertRaises(NotWritable):
            self.v.write_text("../外.md", "x")
        self.v.write_text("02 State/x.json", "{}")

    def test_write_note_does_not_overwrite(self):
        rel = "01 Lessons/01 常識/文庫本の初版は奥付で分かる.md"
        self.assertFalse(self.v.write_note(rel, {"date": "2026-11-04"}, "上書き"))
        self.assertIn("奥付", self.v.text(rel))

    def test_set_meta_touches_only_that_key(self):
        rel = "01 Lessons/01 常識/文庫本の初版は奥付で分かる.md"
        before = open(self.v.path(rel), encoding="utf-8").read()
        self.v.set_meta(rel, "status", "inactive")
        after = open(self.v.path(rel), encoding="utf-8").read()
        self.assertEqual(self.v.note(rel).meta["status"], "inactive")
        self.assertEqual(self.v.note(rel).meta["思い出す言葉"], ["初版", "奥付", "文庫"])
        self.assertEqual(before.replace("status: active", "status: inactive"), after)
        rel2 = "01 Lessons/04 反省/昔の発言を突っ込まれたら先に確かめる.md"
        self.v.set_meta(rel2, "再発", ["2026-11-04"])
        self.assertEqual(self.v.note(rel2).meta["再発"], ["2026-11-04"])
        self.assertEqual(self.v.note(rel2).meta["場面"], "友達に昔の発言を突っ込まれた時")


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.s = Vault(copy_template()).load("Agents/SCHEDULE.md", Schedule.parse)

    def test_weekly_and_extra(self):
        from datetime import date, datetime
        self.assertEqual(self.s.day_text(date(2026, 11, 9)), "12:00〜18:00 古本屋のバイト")        # 月
        self.assertEqual(self.s.day_text(date(2026, 11, 12)), "予定なし")                         # 木だが休み
        self.assertEqual(self.s.day_text(date(2026, 11, 14)),
                         "10:00〜16:00 古本屋のバイト、14:00〜16:00 イラストの打ち合わせ")
        self.assertEqual(self.s.current(datetime(2026, 11, 9, 13, 0)).name, "古本屋のバイト")
        self.assertIsNone(self.s.current(datetime(2026, 11, 9, 19, 0)))
        self.assertEqual(self.s.week_text(datetime(2026, 11, 9, 2, 0)).split("／")[0], "日: 予定なし")   # 4時までは前の日

    def test_now_text(self):
        from datetime import datetime
        self.assertEqual(now_text(datetime(2026, 11, 2, 12, 50)), "11月2日（月）の昼 12:50")
        self.assertTrue(now_text(datetime(2026, 11, 3, 1, 0)).endswith("（月曜の夜の続き）"))


if __name__ == "__main__":
    unittest.main()
