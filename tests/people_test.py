"""人物ページ：フロントマターの一覧、返信先への固定枠、夜の更新（write が false の間は書かない）。"""
import asyncio
import json
import unittest
from datetime import date, datetime

from harness import people
from harness.agent import Agent
from harness.outbox import Intent
from harness.vault import Vault
from tests.fakes import FakeJev, FakeLLM, FakeSender, copy_template, msg

T = datetime(2026, 11, 12, 20, 0)
DAY = date(2026, 11, 12)

OLD_INDEX = """---
date: 2026-11-12
status: active
種類: 索引
---
| 表示名 | 呼び名 | 一行要約 | 最終更新 |
|---|---|---|---|
| ミナ | みなみな、ミナさん | 古本屋の話をよくする友人 | 2026-11-11 |
"""
PAGE = """---
date: 2026-11-10
種類: 人物
status: active
表示名: ミナ
呼び名: [みなみな, ミナさん]
一行要約: 古本屋の話をよくする友人
最終更新: 2026-11-11
related: []
---
# 関係
同じ学部の友人。古本を貸し借りする。

# 最近の出来事
- 11-08 古本屋に行く話をした
- 11-09 文庫を借りた
- 11-10 返す約束をした
- 11-11 返した

# 約束・宣言
- 今度ラーメンを奢る

# 呼び名・呼び方
- みなみな
"""


def agent(llm=None):
    return Agent(Vault(copy_template()), FakeJev(), llm or FakeLLM([]), FakeSender(), clock=lambda: T,
                 notice=lambda p, a: 0)


def with_minna(a):
    """ID の無いページ（手で書いた `<表示名>.md`）。前の作りの INDEX.md も残っている（もう読まない）。"""
    a.vault.write_text("05 人物/INDEX.md", OLD_INDEX)
    a.vault.write_text("05 人物/ミナ.md", PAGE)
    return a


def sections(a, author):
    target = {"id": "9", "author": author, "author_id": f"u-{author}", "text": "こんにちは", "channel": "c"}
    s, _ = a.sections(Intent("reply", "c", "normal", T, target=target), T)
    return s


class IndexTest(unittest.TestCase):
    def test_index_reads_frontmatter_and_skips_other_kinds(self):
        a = with_minna(agent())
        got = people.index(a.vault)
        self.assertEqual([(e.name, e.forms, e.summary, e.updated, e.uid) for e in got],
                         [("ミナ", ["みなみな", "ミナさん"], "古本屋の話をよくする友人", "2026-11-11", "")])   # INDEX.md（索引）は読まない

    def test_template_has_no_pages_and_a_base(self):
        v = Vault(copy_template())
        self.assertEqual(people.index(v), [])
        self.assertFalse(v.exists("05 人物/INDEX.md"))
        base = v.text("05 人物/人物.base")
        for col in ("表示名", "id", "ユーザー名", "呼び名", "一行要約", "最終更新"):
            self.assertIn(col, base)

    def test_find_by_display_or_alias(self):
        a = with_minna(agent())
        self.assertEqual(people.find(a.vault, "ミナ").name, "ミナ")
        self.assertEqual(people.find(a.vault, "みなみな").name, "ミナ")
        self.assertIsNone(people.find(a.vault, "ケイ"))

    def test_page_without_frontmatter_uses_file_name(self):
        a = agent()
        a.vault.write_text("05 人物/ケイ.md", "# 関係\n先輩。")
        self.assertEqual(people.find(a.vault, "ケイ").at, "05 人物/ケイ.md")


class RecallTest(unittest.TestCase):
    def test_section_given_for_speaker_with_page(self):
        s = sections(with_minna(agent()), "ミナ")
        t = s[people.SECTION]
        self.assertIn("古本屋の話をよくする友人", t)
        self.assertIn("古本を貸し借りする", t)
        self.assertIn("ラーメンを奢る", t)
        self.assertNotIn("11-08", t)                      # 最近の出来事は新しい3件だけ
        self.assertIn("11-11 返した", t)
        self.assertLess(len(t), 500)

    def test_found_by_alias_too(self):
        self.assertIn(people.SECTION, sections(with_minna(agent()), "みなみな"))

    def test_no_section_without_page(self):
        a = with_minna(agent())
        self.assertNotIn(people.SECTION, sections(a, "ケイ"))
        self.assertNotIn(people.SECTION, sections(a, "トオル"))

    def test_no_section_without_target_or_index(self):
        a = with_minna(agent())
        s, _ = a.sections(Intent("start", "c", "normal", T), T)
        self.assertNotIn(people.SECTION, s)
        self.assertNotIn(people.SECTION, sections(agent(), "ミナ"))

    def test_inactive_page_not_given(self):
        a = with_minna(agent())
        a.vault.write_text("05 人物/ミナ.md", PAGE.replace("status: active", "status: inactive"))
        self.assertNotIn(people.SECTION, sections(a, "ミナ"))

    def test_no_jev_call_for_it(self):
        a = with_minna(agent())
        sections(a, "ミナ")
        self.assertEqual(a.jev.calls, [])


ANSWER = {"要約": "古本を貸し借りする友人", "関係": "同じ学部の友人。古本の話で盛り上がる。",
          "最近の出来事": ["古本屋の話をした"], "約束・宣言": ["今度ラーメンを奢る"], "呼び名": ["みなみな", "でっち上げ"]}


def with_chat(a, n=3):
    for i in range(n):
        a.logs.chat(msg(f"m{i}", "c", "ミナ", f"古本屋に行った みなみなです {i}", datetime(2026, 11, 12, 12, i)))
    a.logs.chat(msg("x", "c", "Akari", "俺の発言は材料にしない", datetime(2026, 11, 12, 13, 0), me=True, bot=True))
    a.logs.chat(msg("y", "c", "ケイ", "一言だけ", datetime(2026, 11, 12, 13, 1)))


class NightTest(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLM(lambda m: json.dumps(ANSWER))
        self.a = with_minna(agent(self.llm))
        with_chat(self.a)

    def test_dry_run_writes_nothing(self):
        before = self.a.vault.text("05 人物/ミナ.md")
        done = asyncio.run(people.update(self.a, DAY, write_=False))
        self.assertEqual(done, [("05 人物/u-ミナ.md", False)])
        self.assertEqual(self.a.vault.text("05 人物/ミナ.md"), before)
        self.assertFalse(self.a.vault.exists("05 人物/u-ミナ.md"))
        self.assertEqual(people.find(self.a.vault, "ミナ").updated, "2026-11-11")

    def test_write_updates_page_and_frontmatter(self):
        index_before = self.a.vault.text("05 人物/INDEX.md")
        done = asyncio.run(people.update(self.a, DAY, write_=True))
        self.assertEqual(done, [("05 人物/u-ミナ.md", True)])         # ページ名は ID
        self.assertFalse(self.a.vault.exists("05 人物/ミナ.md"))        # 前の作りのページは移した
        n = self.a.vault.note("05 人物/u-ミナ.md")
        self.assertEqual(n.meta["種類"], "人物")
        self.assertEqual((n.meta["id"], n.meta["表示名"], n.meta["aliases"]), ("u-ミナ", "ミナ", []))
        self.assertEqual((n.meta["一行要約"], str(n.meta["最終更新"])), ("古本を貸し借りする友人", "2026-11-12"))
        self.assertEqual(self.a.vault.text("05 人物/INDEX.md"), index_before)   # INDEX.md は書かない
        self.assertEqual(str(n.meta["date"]), "2026-11-10")          # 作った日は保つ
        sec = people.parse_page(n.body)
        self.assertEqual(sec["約束・宣言"], "- 今度ラーメンを奢る")
        e = people.find(self.a.vault, "ミナ")
        self.assertEqual((e.summary, e.updated), ("古本を貸し借りする友人", "2026-11-12"))
        self.assertIn("みなみな", e.forms)
        self.assertNotIn("でっち上げ", e.forms)                       # 材料に無い呼び名は入れない

    def test_material_excludes_own_lines_and_one_liners(self):
        asyncio.run(people.update(self.a, DAY, write_=False))
        self.assertEqual(len(self.llm.calls), 1)                      # ケイは発言が1つなので更新しない
        sent = self.llm.calls[0][1]["content"]
        self.assertNotIn("俺の発言は材料にしない", sent)
        self.assertIn("【相手】[[u-ミナ|ミナ]]", sent)                  # 相手は印で渡す
        self.assertIn("同じ学部の友人", sent)                          # 今のページを渡す

    def test_bad_answers_are_not_saved(self):
        for bad in [{**ANSWER, "要約": "あ" * 80}, {**ANSWER, "関係": ""}, {**ANSWER, "最近の出来事": ["a"] * 9},
                    {**ANSWER, "約束・宣言": ["b" * 90]}]:
            a = with_minna(agent(FakeLLM(lambda m, bad=bad: json.dumps(bad))))
            with_chat(a)
            self.assertEqual(asyncio.run(people.update(a, DAY, write_=True)), [])
        a = with_minna(agent(FakeLLM(["壊れた応答"])))
        with_chat(a)
        self.assertEqual(asyncio.run(people.update(a, DAY, write_=True)), [])

    def test_night_run_respects_write_false(self):
        from harness import night
        before = self.a.vault.text("05 人物/ミナ.md")
        out = asyncio.run(night.run(self.a, datetime(2026, 11, 13, 4, 0)))       # 4時は前の日の整理（config は write: false）
        self.assertEqual(out["people"], [("05 人物/u-ミナ.md", False)])
        self.assertEqual(self.a.vault.text("05 人物/ミナ.md"), before)

    def test_new_person_creates_page(self):
        a = agent(FakeLLM(lambda m: json.dumps(ANSWER)))
        with_chat(a)
        asyncio.run(people.update(a, DAY, write_=True))
        self.assertTrue(a.vault.exists("05 人物/u-ミナ.md"))
        self.assertEqual([e.name for e in people.index(a.vault)], ["ミナ"])
        self.assertIn(people.SECTION, sections(a, "ミナ"))             # 書いた翌日から想起される


def with_ids(a):
    a.vault.write_text("05 人物/1001.md", PAGE.replace("表示名: ミナ", "id: '1001'\n表示名: ミナ\nユーザー名: mina\naliases: []")
                       .replace("呼び名: [みなみな, ミナさん]", "呼び名: [みなみな]"))
    return a


def says(a, author, uid, n=3):
    for i in range(n):
        a.logs.chat(msg(f"{uid}-{i}", "c", author, f"古本屋に行った みなみなです {i}", datetime(2026, 11, 12, 12, i),
                        author_id=uid))


def pages(a):
    return sorted(a.vault.names("05 人物", ".md"))


class AuthorIdTest(unittest.TestCase):
    """author_id で `<id>.md` を引く。表示名が変わっても同じ人、同じ表示名でも ID が違えば別人。"""

    def test_index_reads_id(self):
        a = with_ids(agent())
        self.assertEqual([(e.name, e.uid, e.forms, e.at) for e in people.index(a.vault)],
                         [("ミナ", "1001", ["みなみな"], "05 人物/1001.md")])

    def test_found_by_id_after_display_name_changed(self):
        a = with_ids(agent())
        self.assertEqual(people.find(a.vault, "みーな＠試験前", "1001").name, "ミナ")
        target = {"id": "9", "author": "みーな＠試験前", "author_id": "1001", "text": "やあ", "channel": "c"}
        s, _ = a.sections(Intent("reply", "c", "normal", T, target=target), T)
        self.assertTrue(s[people.SECTION].startswith("みーな＠試験前（古本屋の話をよくする友人）"))   # 見出しは今の表示名
        self.assertIn("古本を貸し借りする", s[people.SECTION])

    def test_same_display_name_with_other_id_is_another_person(self):
        a = with_ids(agent())
        self.assertIsNone(people.find(a.vault, "ミナ", "2002"))
        self.assertIsNone(people.find(a.vault, "みなみな", "2002"))
        self.assertNotIn(people.SECTION, sections(a, "ミナ"))           # sections の author_id は u-ミナ（1001 ではない）

    def test_row_without_id_is_found_by_name_and_id_is_filled_at_night(self):
        a = with_minna(agent(FakeLLM(lambda m: json.dumps(ANSWER))))    # ID 列の無い古い表
        self.assertEqual(people.find(a.vault, "ミナ", "1001").name, "ミナ")
        says(a, "ミナ", "1001")
        asyncio.run(people.update(a, DAY, write_=True))
        self.assertEqual({e.name: e.uid for e in people.index(a.vault)}, {"ミナ": "1001"})
        self.assertEqual(pages(a), ["05 人物/1001.md", "05 人物/INDEX.md"])        # <表示名>.md は <id>.md に移した

    def test_night_update_after_rename_keeps_one_page(self):
        llm = FakeLLM(lambda m: json.dumps(ANSWER))
        a = with_ids(agent(llm))
        says(a, "みーな", "1001")
        done = asyncio.run(people.update(a, DAY, write_=True))
        self.assertEqual(done, [("05 人物/1001.md", True)])              # ページ名は ID
        self.assertEqual(pages(a), ["05 人物/1001.md"])                   # 1人1ページのまま
        rows = [e for e in people.index(a.vault) if e.uid == "1001"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].name, rows[0].updated), ("みーな", "2026-11-12"))   # 表示名は今の名前
        self.assertIn("同じ学部の友人", llm.calls[0][1]["content"])       # 前のページを材料に渡している
        meta = a.vault.note("05 人物/1001.md").meta
        self.assertEqual((meta["表示名"], meta["aliases"]), ("みーな", ["ミナ"]))     # 古い名前は aliases に

    def test_same_display_name_two_ids_get_two_pages(self):
        a = with_ids(agent(FakeLLM(lambda m: json.dumps(ANSWER))))
        before = a.vault.text("05 人物/1001.md")
        says(a, "ミナ", "2002")
        done = asyncio.run(people.update(a, DAY, write_=True))
        self.assertEqual(done, [("05 人物/2002.md", True)])
        self.assertEqual(a.vault.text("05 人物/1001.md"), before)        # 別人のページを上書きしない
        self.assertEqual({e.uid: e.name for e in people.index(a.vault) if e.uid}, {"1001": "ミナ", "2002": "ミナ"})

    def test_pipe_in_display_name_is_kept(self):
        a = agent(FakeLLM(lambda m: json.dumps(ANSWER)))
        says(a, "ミナ|眠い", "3003")
        asyncio.run(people.update(a, DAY, write_=True))
        asyncio.run(people.update(a, DAY, write_=True))                  # 2晩目も同じ行・同じページ
        ents = people.index(a.vault)
        self.assertEqual([(e.name, e.uid, e.summary, e.updated) for e in ents],
                         [("ミナ|眠い", "3003", "古本を貸し借りする友人", "2026-11-12")])
        self.assertEqual(pages(a), ["05 人物/3003.md"])
        target = {"id": "9", "author": "ミナ|眠い", "author_id": "3003", "text": "やあ", "channel": "c"}
        s, _ = a.sections(Intent("reply", "c", "normal", T, target=target), T)
        self.assertIn(people.SECTION, s)


if __name__ == "__main__":
    unittest.main()
