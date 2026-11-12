"""約束・宣言の台帳：発言から足す、重複しない、節は最大3件で期限順、期限切れは流れた、write=false は変えない。"""
import asyncio
import json
import unittest
from datetime import date, datetime

import yaml

from harness import night, promises
from harness.agent import Agent
from harness.outbox import Intent
from harness.vault import Vault
from tests.fakes import FakeJev, FakeLLM, FakeSender, copy_template

T = datetime(2026, 11, 12, 20, 0)
DAY = date(2026, 11, 12)


def make(jev_rule=None, write=False):
    root = copy_template()
    p = f"{root}/Agents/config.yaml"
    text = open(p, encoding="utf-8").read().replace("persona: basic", "persona: nagi")
    open(p, "w", encoding="utf-8").write(text)
    a = Agent(Vault(root), FakeJev(jev_rule), FakeLLM([]), FakeSender(), clock=lambda: T, notice=lambda p, x: 0)
    a.config_write = write
    return a


def rec(i, text, who="", due="", state="未完了", made="2026-11-10", kind="約束"):
    return {"id": i, "内容": text, "相手": who, "種類": kind, "期限": due, "状態": state, "作られた日": made}


def seed(a, rows):
    st = promises.ledger(a)
    st.data[:] = rows
    return st


def target(author):
    return {"id": "9", "author": author, "author_id": f"u-{author}", "text": "やあ", "channel": "c"}


class AddTest(unittest.TestCase):
    def test_sent_promise_is_added_with_partner_and_kind(self):
        a = make()
        texts = ["了解", "今度ラーメン奢るわ"]
        ans = {"promise": 0.9, "declare": 0.1, "which": ("1", {}), "to": ("ミナ", {})}
        a.persona.on_sent(texts, ans)
        rows = promises.ledger(a).data
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["内容"], rows[0]["相手"], rows[0]["種類"], rows[0]["状態"]),
                         ("今度ラーメン奢るわ", "ミナ", "約束", "未完了"))
        self.assertEqual(rows[0]["作られた日"], "2026-11-12")

    def test_declaration_without_partner(self):
        a = make()
        a.persona.on_sent(["早めに寝る"], {"promise": 0.0, "declare": 0.9, "to": ("なし", {})})
        r = promises.ledger(a).data[0]
        self.assertEqual((r["種類"], r["相手"]), ("宣言", ""))

    def test_below_threshold_adds_nothing(self):
        a = make()
        a.persona.on_sent(["早めに寝る"], {"promise": 0.3, "declare": 0.5})
        self.assertEqual(promises.ledger(a).data, [])

    def test_same_content_same_partner_is_not_added_twice(self):
        a = make()
        ans = {"promise": 0.9, "to": ("ミナ", {})}
        a.persona.on_sent(["今度ラーメン奢るわ"], ans)
        a.persona.on_sent(["今度ラーメン奢るわ"], ans)
        self.assertEqual(len(promises.ledger(a).data), 1)
        a.persona.on_sent(["今度ラーメン奢るわ"], {"promise": 0.9, "to": ("トオル", {})})   # 相手が違えば足す
        self.assertEqual(len(promises.ledger(a).data), 2)

    def test_done_item_does_not_block_a_new_one(self):
        a = make()
        seed(a, [rec("x", "今度ラーメン奢るわ", "ミナ", state="済み")])
        a.persona.on_sent(["今度ラーメン奢るわ"], {"promise": 0.9, "to": ("ミナ", {})})
        self.assertEqual(len(promises.ledger(a).data), 2)

    def test_questions_go_through_the_real_jev_batch(self):
        a = make()
        qs = a.persona.sent_questions(["ラーメン奢る", "またね"])
        self.assertTrue({"promise", "declare", "which"} <= set(qs))

    def test_due_from_wording(self):
        self.assertEqual(promises.due_from("明日返すわ", DAY), "2026-11-13")
        self.assertEqual(promises.due_from("今度返すわ", DAY), "")

    def test_ledger_is_in_writable_state_and_persists(self):
        a = make()
        a.persona.on_sent(["早めに寝る"], {"declare": 0.9})
        promises.ledger(a).flush(T, force=True)
        self.assertEqual(json.loads(open(a.vault.path("02 State/約束.json"), encoding="utf-8").read())[0]["内容"], "早めに寝る")


class RecallTest(unittest.TestCase):
    def test_section_is_max_three_sorted_by_due_with_undated_last(self):
        a = make()
        seed(a, [rec("1", "なし期限", made="2026-11-01"), rec("2", "遠い", due="2026-12-01"), rec("3", "近い", due="2026-11-13"),
                 rec("4", "中", due="2026-11-20"), rec("5", "済んだ", due="2026-11-12", state="済み")])
        got = promises.recall(a, "ミナ").split("\n")
        self.assertEqual(len(got), 3)
        self.assertTrue(got[0].startswith("- 近い") and got[1].startswith("- 中") and got[2].startswith("- 遠い"))
        self.assertNotIn("なし期限", "".join(got))
        self.assertNotIn("済んだ", "".join(got))

    def test_undated_are_newest_first(self):
        """期限なし同士は新しい順。古い3件が枠を占め続けない。"""
        a = make()
        seed(a, [rec("1", "古い一", made="2026-11-01"), rec("2", "古い二", made="2026-11-02"), rec("3", "古い三", made="2026-11-03"),
                 rec("4", "きのうの宣言", made="2026-11-11"), rec("5", "今日の宣言", made="2026-11-12")])
        got = promises.recall(a, "ミナ").split("\n")
        self.assertEqual([x[2:].split("（")[0] for x in got], ["今日の宣言", "きのうの宣言", "古い三"])

    def test_dated_ties_keep_made_order(self):
        a = make()
        seed(a, [rec("1", "後", due="2026-11-20", made="2026-11-11"), rec("2", "先", due="2026-11-20", made="2026-11-05")])
        got = promises.recall(a, "ミナ").split("\n")
        self.assertTrue(got[0].startswith("- 先") and got[1].startswith("- 後"))

    def test_partner_match_comes_first(self):
        a = make()
        seed(a, [rec("1", "近い別人", "トオル", due="2026-11-13"), rec("2", "ミナへ", "ミナ"), rec("3", "ミナへ2", "ミナ", due="2026-12-01")])
        got = promises.recall(a, "ミナ").split("\n")
        self.assertTrue(got[0].startswith("- ミナへ2") and got[1].startswith("- ミナへ") and got[2].startswith("- 近い別人"))

    def test_none_when_no_open_item(self):
        a = make()
        seed(a, [rec("1", "済んだ", state="流れた")])
        self.assertIsNone(promises.recall(a, "ミナ"))

    def test_agent_sections_include_it_only_when_present(self):
        a = make()
        s, _ = a.sections(Intent("reply", "c", "normal", T, target=target("ミナ")), T)
        self.assertNotIn(promises.SECTION, s)
        seed(a, [rec("1", "コンビニに行く回数を減らす", "ミナ", kind="宣言")])
        s, _ = a.sections(Intent("reply", "c", "normal", T, target=target("ミナ")), T)
        self.assertIn("コンビニに行く回数を減らす", s[promises.SECTION])


class NightTest(unittest.TestCase):
    def rows(self):
        return [rec("1", "期限切れ", due="2026-11-11"), rec("2", "今日が期限", due="2026-11-12"),
                rec("3", "果たした"), rec("4", "まだ")]

    def run_update(self, write):
        a = make(lambda s, n, q: 0.95 if "1. 果たした" in s and n == "d1" else 0.1)
        seed(a, self.rows())
        # d番号は「期限切れ」を除いた残りの並び: 0=今日が期限, 1=果たした, 2=まだ
        got = asyncio.run(promises.update(a, DAY, write_=write))
        return a, got

    def test_overdue_becomes_lapsed_and_done_by_jev(self):
        a, got = self.run_update(True)
        st = {r["id"]: r["状態"] for r in promises.ledger(a).data}
        self.assertEqual(st, {"1": "流れた", "2": "未完了", "3": "済み", "4": "未完了"})
        self.assertIn(("1", "流れた", True), got)

    def test_undated_lapses_after_lifetime(self):
        """期限なしは、作られてから14日を過ぎたら流れた（14日目はまだ有効）。Jev には聞かない。"""
        a = make()
        seed(a, [rec("1", "古い宣言", made="2026-10-28", kind="宣言"), rec("2", "ぎりぎりの宣言", made="2026-10-29", kind="宣言"),
                 rec("3", "期限つき", due="2026-12-01", made="2026-10-01")])
        self.assertEqual([r["id"] for r in promises.lapse(promises.ledger(a).data, DAY)], ["1"])
        got = asyncio.run(promises.update(a, DAY, write_=True))
        self.assertEqual(got, [("1", "流れた", True)])
        self.assertEqual([r["状態"] for r in promises.ledger(a).data], ["流れた", "未完了", "未完了"])
        self.assertNotIn("古い宣言", a.jev.calls[0][0])
        self.assertNotIn("古い宣言", promises.recall(a, "ミナ"))

    def test_lapse_is_code_only_no_jev_question_about_it(self):
        a, _ = self.run_update(True)
        asked = a.jev.calls[0][0]
        self.assertNotIn("期限切れ", asked)

    def test_write_false_changes_nothing_but_reports(self):
        a, got = self.run_update(False)
        self.assertEqual({r["状態"] for r in promises.ledger(a).data}, {"未完了"})
        self.assertEqual({(i, s) for i, s, w in got}, {("1", "流れた"), ("3", "済み")})
        self.assertFalse(any(w for _, _, w in got))
        self.assertFalse(promises.ledger(a).dirty)

    def test_night_run_calls_it_and_respects_config(self):
        a = make(lambda s, n, q: 0.0)
        seed(a, [rec("1", "期限切れ", due="2026-11-01")])
        a.llm = FakeLLM(lambda m: json.dumps({"body": ""}))
        out = asyncio.run(night.run(a, datetime(2026, 11, 13, 4, 0)))
        self.assertEqual(out["promises"], [("1", "流れた", False)])      # 見本の night.write は false
        self.assertEqual(promises.ledger(a).data[0]["状態"], "未完了")


if __name__ == "__main__":
    unittest.main()
