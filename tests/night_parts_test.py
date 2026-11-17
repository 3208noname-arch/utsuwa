"""夜の部品ごとのスイッチ（night.parts）と夜の報告（03 Logs/night/日付.json）。"""
import asyncio
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from harness import digest, night, people, promises
from tests.fakes import FakeJev, FakeLLM
from tests.digest_test import ANSWER as DIGEST_ANSWER
from tests.people_test import ANSWER as PEOPLE_ANSWER, with_chat
from tests.promises_test import make, rec, seed

NIGHT = datetime(2026, 11, 13, 4, 0)       # 4時は前の日（11-12）の整理
REPORT = "03 Logs/night/2026-11-12.json"
ALL = ("diary", "lessons", "people", "digest", "promises")


def build(night_cfg):
    """全部の部品が1件ずつ生成できる夜。"""
    a = make()
    a.llm = FakeLLM(lambda m: json.dumps({"body": "静かな日だった。", **PEOPLE_ANSWER, **DIGEST_ANSWER}))
    a.jev = FakeJev(lambda s, n, q: 0.9 if n.startswith("p") and n[1:].isdigit() else None)
    a.config["night"] = night_cfg
    with_chat(a)
    for name, body in (("初版の見分け方", "初版かどうかは奥付で分かる。"), ("文庫本の初版は奥付", "文庫本の初版は奥付で分かる。")):
        a.vault.write_note(f"01 Lessons/01 常識/{name}.md",
                           {"date": date(2026, 11, 4), "教わった人": ["トオル"], "status": "active", "種類": "常識",
                            "出どころ": "評価", "思い出す言葉": ["初版", "奥付"]}, body)
    seed(a, [rec("1", "期限切れの約束", due="2026-11-01")])
    return a


def written(a):
    v = a.vault
    return {
        "diary": v.exists("00 Diary/2026-11-12.md"),
        "lessons": v.note("01 Lessons/01 常識/初版の見分け方.md").meta["status"] == "inactive"
        or v.note("01 Lessons/01 常識/文庫本の初版は奥付.md").meta["status"] == "inactive",
        "people": v.exists("05 人物/u-ミナ.md"),
        "digest": v.exists(digest.REL),
        "promises": promises.ledger(a).data[0]["状態"] != "未完了",
    }


def run_night(cfg):
    a = build(cfg)
    out = asyncio.run(night.run(a, NIGHT))
    return a, out, json.loads(a.vault.text(REPORT))


class PartsTest(unittest.TestCase):
    def test_default_writes_nothing_new_but_diary_as_before(self):
        a, out, _ = run_night({"hour": 4, "write": False})
        self.assertEqual(written(a), {"diary": True, "lessons": False, "people": False, "digest": False, "promises": False})

    def test_no_night_section_is_same_as_write_false(self):
        a, out, _ = run_night({})
        self.assertEqual(written(a), {"diary": True, "lessons": False, "people": False, "digest": False, "promises": False})

    def test_write_true_writes_all(self):
        a, out, rep = run_night({"write": True})
        self.assertEqual(written(a), dict.fromkeys(ALL, True))

    def test_only_listed_parts_write(self):
        a, out, rep = run_night({"write": False, "parts": {"promises": True}})
        self.assertEqual(written(a), {"diary": True, "lessons": False, "people": False, "digest": False, "promises": True})
        a, out, rep = run_night({"write": False, "parts": {"digest": True, "diary": False}})
        self.assertEqual(written(a), {"diary": False, "lessons": False, "people": False, "digest": True, "promises": False})

    def test_part_false_beats_write_true(self):
        a, out, rep = run_night({"write": True, "parts": {"people": False}})
        self.assertEqual(written(a), {**dict.fromkeys(ALL, True), "people": False})


class ReportTest(unittest.TestCase):
    def test_report_written_even_when_write_false_with_counts(self):
        a, out, rep = run_night({"write": False})
        run1 = rep["runs"][0]
        self.assertEqual(rep["date"], "2026-11-12")
        self.assertEqual(run1["parts_on"], {"diary": True, "lessons": False, "people": False, "digest": False, "promises": False})
        c = run1["parts"]
        self.assertEqual(c["diary"], {"generated": 1, "saved": 1, "skipped": 0})
        n = c["lessons"]["generated"]            # 見本の記憶とも組になるので、件数は固定にしない
        self.assertGreaterEqual(n, 1)
        self.assertEqual(c["lessons"], {"generated": n, "saved": 0, "skipped": n})
        for k in ("people", "digest", "promises"):
            self.assertEqual(c[k], {"generated": 1, "saved": 0, "skipped": 1}, k)

    def test_report_counts_saved_for_enabled_parts(self):
        a, out, rep = run_night({"write": False, "parts": {"promises": True, "people": True}})
        c = rep["runs"][0]["parts"]
        self.assertEqual(c["promises"], {"generated": 1, "saved": 1, "skipped": 0})
        self.assertEqual(c["people"], {"generated": 1, "saved": 1, "skipped": 0})
        self.assertEqual(c["digest"], {"generated": 1, "saved": 0, "skipped": 1})
        n = c["lessons"]["generated"]
        self.assertEqual(c["lessons"], {"generated": n, "saved": 0, "skipped": n})
        a, out, rep = run_night({"write": True})
        self.assertEqual(rep["runs"][0]["parts"]["lessons"], {"generated": n, "saved": n, "skipped": 0})

    def test_diary_draft_counted_as_skipped_when_off(self):
        a, out, rep = run_night({"write": False, "parts": {"diary": False}})
        self.assertEqual(rep["runs"][0]["parts"]["diary"], {"generated": 1, "saved": 0, "skipped": 1})
        self.assertFalse(a.vault.exists("00 Diary/2026-11-12.md"))

    def test_rerun_appends_run(self):
        a, out, rep = run_night({"write": True})
        asyncio.run(night.run(a, NIGHT))
        rep = json.loads(a.vault.text(REPORT))
        self.assertEqual(len(rep["runs"]), 2)
        self.assertEqual(rep["runs"][1]["parts"]["diary"]["generated"], 0)     # 日記は既にある

    def test_no_errors_when_all_parts_work(self):
        a, out, rep = run_night({"write": True})
        self.assertEqual(rep["runs"][0]["errors"], {})
        self.assertEqual(out["errors"], {})

    def test_nothing_to_do_gives_zero_counts(self):
        a = make()
        a.llm = FakeLLM([])
        asyncio.run(night.run(a, NIGHT))
        c = json.loads(a.vault.text(REPORT))["runs"][0]["parts"]
        self.assertTrue(all(v == {"generated": 0, "saved": 0, "skipped": 0} for v in c.values()))


class FailureTest(unittest.TestCase):
    """部品の1つが例外を出しても、残りの部品と夜の報告は続く。"""
    ZERO = {"generated": 0, "saved": 0, "skipped": 0}

    def run_with_broken(self, target, name):
        a = build({"write": True})
        with patch.object(target, name, side_effect=RuntimeError("落ちた")), self.assertLogs("utsuwa", "ERROR"):
            out = asyncio.run(night.run(a, NIGHT))
        return a, out, json.loads(a.vault.text(REPORT))["runs"][0]

    def test_one_broken_part_does_not_stop_the_rest(self):
        for part, (target, name) in {"people": (people, "update"), "promises": (promises, "update"),
                                     "digest": (digest, "update"), "lessons": (night, "tidy"),
                                     "diary": (night, "diary_draft")}.items():
            a, out, run1 = self.run_with_broken(target, name)
            self.assertEqual(written(a), {**dict.fromkeys(ALL, True), part: False}, part)
            self.assertEqual(run1["errors"], {part: "RuntimeError: 落ちた"}, part)
            self.assertEqual(out["errors"], {part: "RuntimeError: 落ちた"}, part)
            self.assertEqual(run1["parts"][part], self.ZERO, part)
            for k in ALL:
                if k != part:
                    self.assertGreaterEqual(run1["parts"][k]["saved"], 1, (part, k))

    def test_all_broken_still_reports(self):
        a = build({"write": True})
        with patch.object(people, "update", side_effect=RuntimeError("a")), \
             patch.object(promises, "update", side_effect=ValueError("b")), \
             patch.object(digest, "update", side_effect=KeyError("c")), self.assertLogs("utsuwa", "ERROR"):
            asyncio.run(night.run(a, NIGHT))
        run1 = json.loads(a.vault.text(REPORT))["runs"][0]
        self.assertEqual(set(run1["errors"]), {"people", "promises", "digest"})
        self.assertEqual(run1["errors"]["promises"], "ValueError: b")
        self.assertEqual(run1["parts"]["diary"]["saved"], 1)


if __name__ == "__main__":
    unittest.main()
