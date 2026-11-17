"""人名の移行（harness/migrate_names.py）：dry-run は書かない、名前を印に、曖昧は残す、人物ページを ID の名前に移す。
ID と名前はすべて架空の値。"""
import io
import json
import unittest

from harness import migrate_names, people
from harness.vault import Vault, join
from tests.fakes import copy_template

A, B, C = "900000000000000001", "900000000000000002", "900000000000000003"


def chat(v, day, rows):
    v.write_text(f"03 Logs/chat/{day}.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def m(author, uid, text="やあ", **kw):
    return {"id": "1", "at": "2026-11-05T10:00:00", "channel": "c", "author": author, "author_id": uid,
            "bot": False, "me": False, "text": text, **kw}


def setup():
    v = Vault(copy_template())
    chat(v, "2026-11-05", [m("ゆず胡椒", A), m("ミナ", B), m("ミナ", C), m("トオル", "900000000000000004"),
                           {**m("Akari", "akari", "うるせ"), "me": True, "bot": True}])
    v.write_text("00 Diary/2026-11-05.md", join({"date": "2026-11-05", "種類": "日記", "status": "active"},
                 "ゆず胡椒にからかわれた。鍋にゆず胡椒を入れた。トオルと話した。ミナにも会った。"))
    v.write_text("00 Diary/2026-11-06.md", join({"date": "2026-11-06", "種類": "日記", "status": "active"},
                 "ゆず胡椒に会った。"))
    v.write_text("01 Lessons/01 常識/x.md", join({"date": "2026-11-05", "教わった人": ["ゆず胡椒"], "status": "active",
                                                 "種類": "常識", "出どころ": "会話", "思い出す言葉": ["ゆず胡椒", "トオル", "レジ袋"],
                                                 "related": []}, "レジ袋は3円。"))
    return v


def run(v, write=False):
    out = io.StringIO()
    plan = migrate_names.run(v.root, ["ゆず胡椒"], write=write, diff=True, out=out)
    return plan, out.getvalue()


class MigrateTest(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        v = setup()
        before = v.text("00 Diary/2026-11-05.md")
        plan, text = run(v)
        self.assertEqual(v.text("00 Diary/2026-11-05.md"), before)
        self.assertIn("dry-run", text)
        self.assertIn("## 差分", text)

    def test_names_become_marks_and_ambiguous_are_left(self):
        v = setup()
        plan, _ = run(v, write=True)
        d = v.text("00 Diary/2026-11-05.md")
        self.assertEqual(d, f"[[{A}|ゆず胡椒]]にからかわれた。鍋にゆず胡椒を入れた。"
                            f"[[900000000000000004|トオル]]と話した。ミナにも会った。")
        why = {(r["file"], r["name"]): r["why"] for r in plan.ambiguous}
        self.assertIn("普通の語", why[("00 Diary/2026-11-05.md", "ゆず胡椒")])     # 鍋にゆず胡椒を… は調味料
        self.assertIn("同じ名前の ID が", why[("00 Diary/2026-11-05.md", "ミナ")])         # 同名が2人
        self.assertEqual(v.text("00 Diary/2026-11-06.md"), "ゆず胡椒に会った。")      # その日のログに居ない
        self.assertIn("結びつかない", why[("00 Diary/2026-11-06.md", "ゆず胡椒")])

    def test_person_flag_replaces_a_checked_spot(self):
        v = setup()
        out = io.StringIO()
        migrate_names.run(v.root, ["ゆず胡椒"], write=True, out=out, persons=["00 Diary/2026-11-06.md:1:ゆず胡椒"])
        self.assertEqual(v.text("00 Diary/2026-11-06.md"), f"[[{A}|ゆず胡椒]]に会った。")
        self.assertIn("鍋にゆず胡椒を入れた", v.text("00 Diary/2026-11-05.md"))      # 指定していない所はそのまま

    def test_frontmatter(self):
        v = setup()
        run(v, write=True)
        meta = v.note("01 Lessons/01 常識/x.md").meta
        self.assertEqual(meta["教わった人"], [f"[[{A}|ゆず胡椒]]"])
        self.assertEqual(meta["関係する人"], [A])
        self.assertEqual(meta["思い出す言葉"], ["レジ袋"])                         # 人名は落とす
        self.assertEqual(meta["出どころ"], "会話")                                  # ほかの項目は残る

    def test_second_run_changes_nothing(self):
        v = setup()
        run(v, write=True)
        plan, _ = run(v)
        self.assertEqual((plan.replaced, plan.meta, plan.moves), ([], [], []))

    def test_legacy_page_moves_to_id_and_promise_partner_is_marked(self):
        v = setup()
        v.write_text(people.INDEX, join({"date": "2026-11-05", "status": "active", "種類": "索引"},
                                        "| 表示名 | ID | 呼び名 | 一行要約 | 最終更新 |\n|---|---|---|---|---|\n"
                                        f"| 旧ゆず | {A} |  | 友人 | 2026-11-05 |"))
        v.write_text("05 人物/旧ゆず.md", join({"date": "2026-11-05", "種類": "人物", "status": "active"},
                                            "# 関係\nゆず胡椒とは同じ学部。"))
        v.write_text("02 State/約束.json", json.dumps([{"id": "1", "内容": "奢る", "相手": "トオル", "種類": "約束",
                                                       "期限": "", "状態": "未完了", "作られた日": "2026-11-05"}],
                                                     ensure_ascii=False))
        run(v, write=True)
        self.assertFalse(v.exists("05 人物/旧ゆず.md"))
        n = v.note(f"05 人物/{A}.md")
        self.assertEqual((n.meta["id"], n.meta["表示名"], n.meta["aliases"]), (A, "ゆず胡椒", ["旧ゆず"]))
        self.assertIn(f"[[{A}|ゆず胡椒]]とは同じ学部", n.body)
        self.assertEqual([(e.name, e.uid) for e in people.index(v)], [("ゆず胡椒", A)])
        self.assertEqual(json.loads(v.text("02 State/約束.json"))[0]["相手"], "[[900000000000000004|トオル]]")


if __name__ == "__main__":
    unittest.main()
