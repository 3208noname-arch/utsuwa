"""記憶の階層（種類 → 表 → 行）、記録、保存、夜の整理。"""
import asyncio
import json
import unittest
from datetime import date, datetime

from harness import context, night, saver
from harness.agent import Agent
from harness.vault import Vault
from tests.fakes import FakeJev, FakeLLM, FakeSender, copy_template

T = datetime(2026, 11, 4, 20, 0)


def run(c):
    return asyncio.run(c)


def agent(jev, llm):
    return Agent(Vault(copy_template()), jev, llm, FakeSender(), clock=lambda: T, notice=lambda p, a: 0)


class ContextTest(unittest.TestCase):
    def test_narrow_kinds_then_rows(self):
        """コーヒーの話 → 種類はスマホのメモと思い出だけ → 表の全行からコーヒーの行だけ。"""
        def rule(state, name, q):
            text = q["instructions"]
            if name.startswith("k"):
                return 0.9 if ("スマホのメモ" in text or "「思い出」" in text) else 0.1
            i = int(name[1:])
            row = [ln for ln in state.split("\n") if ln.startswith(f"{i}. ")][0]
            return 0.95 if "コーヒー" in row else 0.2
        jev = FakeJev(rule)
        v = Vault(copy_template())
        sec, picked, stats = run(context.select(jev, v, "ミナ: 最近コーヒー淹れてる？", "あかり", date(2026, 11, 4)))
        self.assertEqual(sorted(r.kind for r in picked), ["スマホのメモ", "思い出"])
        self.assertEqual(stats["candidates"], 2)        # 絞った種類の行だけを表にした
        self.assertEqual(sec["スマホのメモ"], "- ミナに「水出しコーヒーの作り方」を教わった（メモがある）")
        self.assertNotIn("粉40g", "".join(sec.values()))    # メモの中身は渡さない
        self.assertIn("トオルとの出来事", sec["覚えていること"])
        table = jev.calls[1][0]
        self.assertNotIn("出どころ", table)                 # 会話用には「評価」という語を見せない
        self.assertEqual(len(jev.calls), 2)                 # Jev は2回だけ

    def test_templates_and_inactive_are_not_rows(self):
        v = Vault(copy_template())
        names = [r.name for r in context.lesson_rows(v)]
        self.assertFalse(any("Temp" in n for n in names))
        v.set_meta("01 Lessons/01 常識/文庫本の初版は奥付で分かる.md", "status", "inactive")
        self.assertNotIn("文庫本の初版は奥付で分かる", [r.name for r in context.lesson_rows(v)])

    def test_jev_failure_gives_no_memory(self):
        sec, picked, _ = run(context.select(FakeJev(), Vault(copy_template()), "x", "あかり", date(2026, 11, 4)))
        self.assertEqual((sec, picked), ({}, []))


def talk_item(text, author="ミナ"):
    m = {"id": "1", "at": T.isoformat(), "channel": "100000000000000001", "author": author, "author_id": "u1",
         "bot": False, "me": False, "text": text}
    return {"channel": m["channel"], "message": m, "before": [], "after": []}


class RecordTest(unittest.TestCase):
    def test_common_sense_with_properties(self):
        llm = FakeLLM([json.dumps({"name": "レジ袋は1枚3円", "body": "コンビニのレジ袋は1枚3円くらい。",
                                   "keywords": ["レジ袋", "袋代", "存在しない語"]})])
        a = agent(FakeJev(), llm)
        rel = run(a.recorder.from_talk(talk_item("レジ袋は3円だよ"), ("常識", {})))
        n = a.vault.note(rel)
        self.assertEqual(rel, "01 Lessons/01 常識/レジ袋は1枚3円.md")
        self.assertEqual(n.meta["教わった人"], ["ミナ"])
        self.assertEqual(n.meta["出どころ"], "会話")
        self.assertEqual(n.meta["思い出す言葉"], ["レジ袋"])     # 本文にも発言にも無い語は落とす
        self.assertEqual(str(n.meta["date"]), "2026-11-04")
        self.assertEqual(llm.calls[0][0]["content"], a.vault.text("Agents/RECORDER.md"))   # 記録用の指示だけを読む

    def test_memo_body_is_copied_by_code(self):
        said = "卵2個、ご飯200g、強火で2分"
        a = agent(FakeJev(), FakeLLM([json.dumps({"name": "チャーハンの作り方", "body": "LLMが書いた本文",
                                                   "keywords": ["チャーハン", "卵"]})]))
        rel = run(a.recorder.from_talk(talk_item(said), ("スマホのメモ", {})))
        self.assertEqual(a.vault.text(rel), said)
        self.assertNotIn("出どころ", a.vault.note(rel).meta)

    def test_nothing_from_own_lines_or_none(self):
        a = agent(FakeJev(), FakeLLM([]))
        it = talk_item("俺は昨日クレーンで千円使った")
        it["message"]["me"] = True
        self.assertIsNone(run(a.recorder.from_talk(it, ("常識", {}))))
        self.assertIsNone(run(a.recorder.from_talk(talk_item("へー"), ("しない", {}))))

    def test_same_regret_adds_recurrence(self):
        jev = FakeJev(lambda s, n, q: 0.9 if "同じ振る舞い" in q["instructions"] else None)
        a = agent(jev, FakeLLM([json.dumps({"name": "昔の発言は確かめる", "body": "前の発言を聞かれたら確かめる。",
                                            "keywords": ["前"], "scene": "突っ込まれた時"})]))
        rel = run(a.recorder.from_talk(talk_item("お前前にそう言ってなかった？"), ("反省", {})))
        self.assertEqual(rel, "01 Lessons/04 反省/昔の発言を突っ込まれたら先に確かめる.md")
        self.assertEqual(a.vault.note(rel).meta["再発"], ["2026-11-04"])

    def test_rating_becomes_talk_when_said_in_chat(self):
        def rule(s, n, q):
            return {"kind": "常識", "talk": 0.9, "who": "トオル"}.get(n)
        a = agent(FakeJev(rule), FakeLLM([json.dumps({"name": "ﾇﾙﾎﾟにはｶﾞｯ", "body": "ﾇﾙﾎﾟと言われたらｶﾞｯと返す。",
                                                       "keywords": ["ﾇﾙﾎﾟ"]})]))
        rec = {"message_id": "9002", "channel": "100000000000000001", "text": "え、知らなかった", "is_me": True,
               "comment": "ﾇﾙﾎﾟにはｶﾞｯ", "rater": "ミナ", "at": "2026-11-03T21:00:00", "msg_at": "2026-11-03T20:02:00"}
        rel = run(a.rate(rec))
        m = a.vault.note(rel).meta
        self.assertEqual((m["出どころ"], m["教わった人"]), ("会話", ["トオル"]))
        self.assertTrue(a.vault.exists("03 Logs/ratings/2026-11.jsonl"))

    def test_checks(self):
        a = agent(FakeJev(), FakeLLM([]))
        kinds = a.config["kinds"]
        self.assertIn("同じ本文", saver.check(a.vault, kinds, "常識", "x", "文庫本が初版かどうかは、巻末の奥付の刷数を見れば分かる。"))
        self.assertIn("長すぎる", saver.check(a.vault, kinds, "常識", "x", "あ" * 121))
        self.assertIn("知らない種類", saver.check(a.vault, kinds, "噂", "x", "y"))
        self.assertEqual(saver.safe_name('a/b:c#d'), "abcd")

    def test_month_base_created(self):
        a = agent(FakeJev(), FakeLLM([]))
        self.assertTrue(saver.ensure_month_base(a.vault, date(2026, 12, 1)))
        text = open(a.vault.path("01 Lessons/2026/12月.base"), encoding="utf-8").read()
        self.assertIn("date.month == 12", text)
        self.assertIn("name: 12月", text)


class NightTest(unittest.TestCase):
    def setUp(self):
        self.a = agent(FakeJev(lambda s, n, q: 0.9), FakeLLM([json.dumps({"body": "今日は静かな日だった。"})]))
        v = self.a.vault
        v.write_note("01 Lessons/01 常識/初版の見分け方.md",
                     {"date": date(2026, 11, 4), "教わった人": ["トオル"], "status": "active", "種類": "常識",
                      "出どころ": "評価", "思い出す言葉": ["初版", "奥付"]}, "初版かどうかは奥付で分かる。")

    def test_dry_run_changes_nothing(self):
        done = run(night.tidy(self.a, date(2026, 11, 4), write=False))
        self.assertEqual(len(done), 1)
        self.assertEqual(self.a.vault.note("01 Lessons/01 常識/初版の見分け方.md").meta["status"], "active")

    def test_talk_wins_over_rating_even_if_older(self):
        done = run(night.tidy(self.a, date(2026, 11, 4), write=True))
        self.assertEqual(done, [("01 Lessons/01 常識/文庫本の初版は奥付で分かる.md", "01 Lessons/01 常識/初版の見分け方.md")])
        self.assertEqual(self.a.vault.note("01 Lessons/01 常識/初版の見分け方.md").meta["status"], "inactive")

    def test_diary(self):
        rel = run(night.diary(self.a, date(2026, 11, 4), ["13:00 バイトに行った"], [], ["おつかれ"]))
        self.assertEqual(self.a.vault.text(rel), "今日は静かな日だった。")
        self.assertIn("13:00 バイトに行った", self.a.llm.calls[0][1]["content"])


if __name__ == "__main__":
    unittest.main()
