"""人名の印 [[id|表示名]]：コードが付け、記録用 LLM には今の名前で印のまま、会話用 LLM には印を外して渡す。
知らない ID の印は外す。思い出す言葉に人名を入れず、人で思い出す時は関係する人（ID）で引く。
ID と名前はすべて架空の値。"""
import asyncio
import json
import unittest
from datetime import date, datetime

from harness import context, digest, night, people, promises, who
from harness.agent import Agent
from harness.outbox import Intent
from harness.vault import Vault, join
from tests.fakes import FakeJev, FakeLLM, FakeSender, copy_template, msg

T = datetime(2026, 11, 12, 20, 0)
DAY = date(2026, 11, 12)
A = "900000000000000001"       # 架空の ID
B = "900000000000000002"


def agent(llm=None, jev=None):
    return Agent(Vault(copy_template()), jev or FakeJev(), llm or FakeLLM([]), FakeSender(), clock=lambda: T,
                 notice=lambda p, a: 0)


class WhoTest(unittest.TestCase):
    def test_link_and_back(self):
        self.assertEqual(who.link(A, "ミナ"), f"[[{A}|ミナ]]")
        self.assertEqual(who.link("", "ミナ"), "ミナ")                   # ID の分からない人に印は付けない
        self.assertEqual(who.link(None, "ミナ"), "ミナ")
        self.assertEqual(who.link(A, "ミナ|眠い[1]"), f"[[{A}|ミナ｜眠い［1］]]")   # 印を壊す文字は全角に
        self.assertEqual(who.uid_of(f"[[{A}|ミナ]]"), A)
        self.assertEqual(who.uid_of(f"[[{A}]]"), A)
        self.assertEqual(who.uid_of("ミナ"), "")

    def test_rename_keeps_mark_and_plain_drops_it(self):
        text = f"[[{A}|旧名]]に借りた。[[{B}|トオル]]もいた"
        self.assertEqual(who.rename(text, {A: "新名"}), f"[[{A}|新名]]に借りた。[[{B}|トオル]]もいた")
        self.assertEqual(who.plain(text, {A: "新名"}), "新名に借りた。トオルもいた")

    def test_check_removes_unknown_ids(self):
        text = f"[[{A}|ミナ]]と[[123|ポン酢]]の話"
        out, dropped = who.check(text, {A})
        self.assertEqual(out, f"[[{A}|ミナ]]とポン酢の話")
        self.assertEqual(dropped, ["[[123|ポン酢]]"])
        v, d = who.check_value({"a": [text], "b": 1}, {A})
        self.assertEqual(v, {"a": [f"[[{A}|ミナ]]とポン酢の話"], "b": 1})

    def test_names_registry_prefers_latest_seen(self):
        a = agent()
        a.vault.write_text(f"05 人物/{A}.md", join({"id": A, "表示名": "旧名", "ユーザー名": "mina_user",
                                                   "aliases": ["もっと旧名"]}, "# 関係\n友人"))
        self.assertEqual(a.whois.current(), {A: "旧名"})
        self.assertEqual(a.whois.all_names(), {"旧名", "mina_user", "もっと旧名"})
        self.assertEqual(a.whois.uid_for("もっと旧名"), A)
        asyncio.run(a.on_message(msg("1", "100000000000000001", "新名", "やあ", T, author_id=A)))
        self.assertEqual(a.whois.current(), {A: "新名"})
        self.assertEqual(a.whois.uid_for("新名"), A)


def talk_item(text, author="ミナ", uid=A, before=()):
    m = {"id": "1", "at": T.isoformat(), "channel": "100000000000000001", "author": author, "author_id": uid,
         "bot": False, "me": False, "text": text}
    return {"channel": m["channel"], "message": m, "before": list(before), "after": []}


class RecorderTest(unittest.TestCase):
    def test_material_is_marked_and_output_checked(self):
        answer = {"name": f"[[{A}|ミナ]]にからかわれた", "body": f"[[{A}|ミナ]]にからかわれた。[[999|ポン酢]]をかけた。",
                  "keywords": ["ミナ", "からかわれた", "ポン酢"]}
        llm = FakeLLM([json.dumps(answer)])
        a = agent(llm)
        rel = asyncio.run(a.recorder.from_talk(talk_item("やーい ポン酢かけとけ"), ("思い出", {})))
        sent = llm.calls[0][1]["content"]
        self.assertIn(f"[[{A}|ミナ]]: やーい ポン酢かけとけ", sent)           # 発言者は印、発言の本文には付けない
        self.assertNotIn("[[999", sent)
        n = a.vault.note(rel)
        self.assertEqual(rel, "01 Lessons/00 感情に基づいた思い出/ミナにからかわれた.md")   # ノート名に印を残さない
        self.assertEqual(n.body.strip(), f"[[{A}|ミナ]]にからかわれた。ポン酢をかけた。")  # 知らない ID の印は外す
        self.assertEqual(n.meta["教わった人"], [f"[[{A}|ミナ]]"])
        self.assertEqual(n.meta["関係する人"], [A])
        self.assertEqual(n.meta["思い出す言葉"], ["からかわれた", "ポン酢"])   # 人名は入れない（印の無いポン酢は普通の語）

    def test_old_name_in_material_becomes_current(self):
        llm = FakeLLM([json.dumps({"name": "x", "body": "y", "keywords": []})])
        a = agent(llm)
        a.whois.saw(A, "新名")
        before = [{"id": "0", "at": T.isoformat(), "author": "旧名", "author_id": A, "text": "前の発言"}]
        asyncio.run(a.recorder.from_talk(talk_item("やあ", author="新名", before=before), ("常識", {})))
        sent = llm.calls[0][1]["content"]
        self.assertIn(f"[[{A}|新名]]: 前の発言", sent)
        self.assertNotIn("旧名", sent)


def lesson(a, body, people_=(), uids=(), words=(), kind="思い出", folder="00 感情に基づいた思い出", name="x"):
    meta = {"date": DAY, "教わった人": list(people_), "関係する人": list(uids), "status": "active", "種類": kind,
            "思い出す言葉": list(words), "related": []}
    a.vault.write_text(f"01 Lessons/{folder}/{name}.md", join(meta, body))


class ConversationTest(unittest.TestCase):
    def test_conversation_llm_sees_current_name_without_mark(self):
        jev = FakeJev(lambda state, name, q: 0.9)
        llm = FakeLLM([json.dumps({"candidates": [{"messages": ["うるせ"]}]})])
        a = agent(llm, jev)
        lesson(a, f"[[{A}|旧名]]にからかわれた。", [f"[[{A}|旧名]]"], [A], name="からかわれた")
        a.whois.saw(A, "新名")
        target = {"id": "9", "author": "新名", "author_id": A, "text": "やあ", "channel": "c", "at": T.isoformat()}
        asyncio.run(a.compose(Intent("reply", "c", "normal", T, target=target)))
        user = llm.calls[0][1]["content"]
        self.assertIn("新名にからかわれた", user)
        self.assertNotIn("[[", user)
        self.assertNotIn("旧名", user)
        table = [s for s, q in jev.calls if "【記憶の表】" in s][0]
        self.assertNotIn("[[", table)                                        # Jev にも印を見せない

    def test_rows_of_the_target_person_are_candidates_without_kind(self):
        def rule(state, name, q):
            if name.startswith("k"):
                return 0.9 if "「常識」" in q["instructions"] else 0.05
            return 0.9
        a = agent()
        lesson(a, f"[[{A}|ミナ]]にからかわれた。", [f"[[{A}|ミナ]]"], [A], name="ミナ")
        lesson(a, f"[[{B}|トオル]]に奢られた。", [f"[[{B}|トオル]]"], [B], name="トオル")
        sec, picked, stats = asyncio.run(context.select(FakeJev(rule), a.vault, "ミナ: よう", "あかり", DAY,
                                                       uid=A, names={A: "ミナ"}))
        rels = [r.rel for r in picked]
        self.assertIn("01 Lessons/00 感情に基づいた思い出/ミナ.md", rels)      # 種類の段で落ちても人で引く
        self.assertNotIn("01 Lessons/00 感情に基づいた思い出/トオル.md", rels)
        self.assertEqual(stats["by_person"], 1)

    def test_name_string_does_not_recall(self):
        """返信先が別人なら、名前の語が会話に出ても、その人の行を人で引かない。"""
        a = agent()
        lesson(a, f"[[{A}|ポン酢]]にからかわれた。", [f"[[{A}|ポン酢]]"], [A], name="p")
        jev = FakeJev(lambda state, name, q: 0.05 if name.startswith("k") else 0.9)
        sec, picked, _ = asyncio.run(context.select(jev, a.vault, "トオル: 鍋にポン酢かけた", "あかり", DAY, uid=B))
        self.assertEqual(picked, [])

    def test_old_note_without_related_uses_taught_mark(self):
        self.assertEqual(context.related_ids({"教わった人": [f"[[{A}|ミナ]]", "トオル"]}), [A])
        self.assertEqual(context.related_ids({"関係する人": [B], "教わった人": [f"[[{A}|ミナ]]"]}), [B, A])


class PromiseTest(unittest.TestCase):
    def test_partner_is_marked_and_matched_by_id(self):
        a = agent()
        st = promises.ledger(a)
        r = promises.add(st, "今度ラーメンを奢る", "ミナ", "約束", DAY, uid=A)
        self.assertEqual(r["相手"], f"[[{A}|ミナ]]")
        self.assertIsNone(promises.add(st, "今度ラーメンを奢る", "みーな", "約束", DAY, uid=A))   # 名前が変わっても同じ相手
        promises.add(st, "宿題を見せる", "トオル", "約束", DAY, uid=B)
        self.assertEqual(promises.pick(st.data, "改名後", uid=A)[0]["内容"], "今度ラーメンを奢る")
        st.data.append({"id": "old", "内容": "本を返す", "相手": "ミナ", "種類": "約束", "期限": "", "状態": "未完了",
                        "作られた日": "2026-11-01"})                        # 印の無い古い件は表示名で引く
        self.assertEqual(promises.pick(st.data, "ミナ", uid="other")[0]["内容"], "本を返す")

    def test_section_shows_current_name(self):
        a = agent()
        promises.add(promises.ledger(a), "今度ラーメンを奢る", "旧名", "約束", DAY, uid=A)
        a.whois.saw(A, "新名")
        target = {"id": "9", "author": "新名", "author_id": A, "text": "やあ", "channel": "c"}
        s, _ = a.sections(Intent("reply", "c", "normal", T, target=target), T)
        self.assertIn(f"[[{A}|旧名]]", s[promises.SECTION])                # sections はまだ印のまま（compose で外す）
        self.assertEqual(who.plain(s[promises.SECTION], a.whois.current()), "- 今度ラーメンを奢る（約束・新名に）")


class NightTest(unittest.TestCase):
    def test_diary_material_marks_reply_targets_and_checks_output(self):
        body = f"[[{A}|ミナ]]に時給を聞いた。[[777|謎]]も出てきた。"
        llm = FakeLLM([json.dumps({"body": body})])
        a = agent(llm)
        a.logs.chat(msg("m1", "c", "ミナ", "バイト行く", datetime(2026, 11, 12, 12, 0), author_id=A))
        a.logs.chat(msg("x", "c", "Akari", "時給出るの？", datetime(2026, 11, 12, 12, 1), me=True, bot=True,
                        target_author="ミナ", target_id=A))
        a.logs.chat(msg("y", "c", "Akari", "ねむ", datetime(2026, 11, 12, 12, 2), me=True, bot=True))
        events, learned, mine = a.day_material(DAY)
        self.assertEqual(mine, [f"（[[{A}|ミナ]]への返事）時給出るの？", "ねむ"])
        out = asyncio.run(night.diary_draft(a, DAY, events, learned, mine))
        self.assertEqual(out, f"[[{A}|ミナ]]に時給を聞いた。謎も出てきた。")

    def test_digest_material_marks_people_and_checks_output(self):
        llm = FakeLLM([json.dumps({"体と生活": "眠い", "進行中のこと": "", "関係の変化": f"[[{A}|ミナ]]と仲直り。[[5|x]]",
                                   "最近の流れ": ""})])
        a = agent(llm)
        a.vault.write_text(people.INDEX, join({"date": DAY, "status": "active", "種類": "索引"},
                                              people.render_index([people.Entry("ミナ", [], "友人", DAY.isoformat(), A)])))
        p = asyncio.run(digest.propose(a, DAY))
        self.assertIn(f"[[{A}|ミナ]]（友人）", llm.calls[0][1]["content"])
        self.assertIn(f"[[{A}|ミナ]]と仲直り。x", p["body"])

    def test_people_page_has_id_frontmatter_and_drops_unknown_marks(self):
        ans = {"要約": f"[[{A}|ミナ]]は友人", "関係": f"[[{A}|ミナ]]とは同じ学部。[[42|誰か]]の話もする。",
               "最近の出来事": [], "約束・宣言": [], "呼び名": [f"[[{A}|ミナ]]"]}
        a = agent(FakeLLM(lambda m: json.dumps(ans)))
        for i in range(2):
            a.logs.chat({**msg(f"m{i}", "c", "ミナ", "ミナです", datetime(2026, 11, 12, 12, i), author_id=A),
                         "username": "mina_user"})
        asyncio.run(people.update(a, DAY, write_=True))
        n = a.vault.note(f"05 人物/{A}.md")
        self.assertEqual((n.meta["id"], n.meta["表示名"], n.meta["ユーザー名"]), (A, "ミナ", "mina_user"))
        sec = people.parse_page(n.body)
        self.assertEqual(sec["関係"], f"[[{A}|ミナ]]とは同じ学部。誰かの話もする。")
        self.assertEqual(sec["呼び名・呼び方"], "- ミナ")                      # 呼び名は印を外した語
        self.assertIn(f"| [[{A}]] |", a.vault.text(people.INDEX))          # INDEX の ID 列はページへのリンク
        self.assertEqual(people.index(a.vault)[0].uid, A)


if __name__ == "__main__":
    unittest.main()
