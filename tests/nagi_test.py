"""ナギの部品を本体に差し込んで動くか（見本のフォルダの設定を persona: nagi に替えて試す）。"""
import asyncio
import unittest
from datetime import datetime, timedelta

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


def read_item(a, text, author="ミナ", **kw):
    m = {"id": "r1", "at": a.now().isoformat(timespec="seconds"), "channel": "100000000000000001", "author": author,
         "author_id": f"u-{author}", "bot": False, "me": False, "text": text, **kw}
    return {"channel": m["channel"], "message": m, "before": [], "after": []}


def ask(a, item, answers):
    """後で読む時の問いを作らせ、偽の答えを返して on_read に渡す。"""
    qs = a.persona.read_questions(item)
    a.persona.on_read(item, {k: v for k, v in answers.items() if k in qs})
    return qs


class NagiFeelingsTest(unittest.TestCase):
    def setUp(self):
        self.a, self.clock = make(datetime(2026, 11, 10, 15, 0))
        self.p = self.a.persona

    def test_attack_angers_lowers_like_and_mood_then_apology(self):
        it = read_item(self.a, "ほんと使えねえなおめえ", to_me=True)
        qs = ask(self.a, it, {"attack": 0.95, "tease": 0.1, "support": 0.0, "joke": 0.1, "belittle": 0.2})
        self.assertNotIn("apology", qs)
        self.assertEqual(self.p.anger_of("u-ミナ"), 40)
        self.assertEqual(self.p.likes.data["u-ミナ"]["v"], 12)        # 初期20から本気のけなし −8
        self.assertLess(self.p.m["v"], 0)
        inner = self.p.inner(self.a.now(), it["message"])
        self.assertIn("返信先への怒り", inner)
        self.assertIn("気分", inner)
        self.assertFalse(any(g.pattern == self.p.guards(it["message"])[-1].pattern and g.search("うるせえ")
                             for g in self.p.guards(it["message"])))   # 怒っている相手には「うるせえ」も許す
        it2 = read_item(self.a, "さっきは言いすぎた、ごめん", to_me=True)
        qs2 = ask(self.a, it2, {"apology": 0.95, "light": 0.3})
        self.assertIn("apology", qs2)
        self.assertLess(self.p.anger_of("u-ミナ"), 40)

    def test_third_party_read_and_nickname(self):
        it = read_item(self.a, "みなっちさぁ、それ違うって", author="トオル", target_author="ミナ", target_id="u-ミナ")
        self.assertTrue(self.p.wants_read(it["message"], []))
        qs = self.p.read_questions(it)
        self.assertIn("みなっち", qs["name"]["criteria"])
        self.assertIn("【相手】ミナ", self.p.read_context(it))
        self.p.on_read(it, {"name": ("みなっち", {}), "attack": 0.1})
        self.assertEqual(self.p.names_state.data["u-ミナ"]["forms"]["みなっち"]["n"], 1)

    def test_own_name_counted_by_regex(self):
        it = read_item(self.a, "あかりちゃん起きてる？", mentions_me=True)
        ask(self.a, it, {"attack": 0.0})
        self.assertIn("あかりちゃん", self.p.names_state.data["me"]["forms"])

    def test_topic_moves_mood_and_comfort_learns(self):
        self.p.topic_list.append({"name": "コーヒー", "valence": 0.8, "arousal": 0.9})
        scene = {"addressed": False, "share": 0.0, "humans": 3, "target": {"author_id": "u-ミナ", "channel": "c"},
                 "presence": self.p.presence(self.a.now()), "convo": "ミナ: コーヒー淹れた"}
        qs = self.p.jev_questions(scene)
        self.assertIn("topic:コーヒー", qs)
        scene["answers"] = {"topic:コーヒー": 0.95}
        kind = self.p.choose(scene)
        self.assertGreater(self.p.m["v"], 0)                            # 好きな話題で機嫌が上がる
        self.assertEqual(self.p.mem.data["episodes"][0]["name"], "コーヒー")
        from harness.outbox import Intent
        self.p.on_delivered(Intent("reply", "c", kind, self.a.now()), ["いいね"])
        self.assertEqual(len(self.p.policy_state.data["pending"]), 1)
        self.clock["t"] += timedelta(minutes=25)
        self.p.tick(self.a.now())
        self.assertEqual(self.p.policy_state.data["pending"], [])       # 反応の枠が閉じて予想を直した
        self.assertEqual(self.p.mem.data["episodes"], [])               # 話題の経験も閉じた

    def test_treat_and_taste_from_sent_reply(self):
        self.a.conv("100000000000000001").add({"id": "x", "at": self.a.now().isoformat(), "author": "トオル",
                                              "author_id": "u-トオル", "bot": False, "me": False, "text": "ほら唐揚げ"})
        before = self.p.b["money"]
        qs = self.p.sent_questions(["トオルの唐揚げうまい", "唐揚げは好き"])
        self.assertIn("by", qs)
        self.p.on_sent(["トオルの唐揚げうまい"], {"did": 0.9, "ate": 0.9, "treated": 0.9, "by": ("トオル", {}),
                                                 "like": 0.9, "dislike": 0.0, "topic": ("唐揚げ", {})})
        self.assertEqual(self.p.b["money"], before)                     # 奢りなので減らない
        self.assertTrue(any("トオルに" in e for e in self.a.h.data["events"]["2026-11-10"]))
        self.assertEqual(next(t for t in self.p.topic_list if t["name"] == "唐揚げ")["valence"] > 0, True)


class NagiThroughAgentTest(unittest.TestCase):
    def test_read_goes_through_one_jev_call(self):
        """本体の「後で読む」から、記録するかと好感度・怒りが、Jev の同じ1回で読まれる。"""
        a, clock = make(datetime(2026, 11, 10, 15, 0))
        a.jev.rule = lambda s, n, q: {"attack": 0.95, "record": "しない"}.get(n, 0.0 if q["type"] == "noul" else None)
        m = {"id": "1", "at": clock["t"].isoformat(timespec="seconds"), "channel": "100000000000000001",
             "author": "ミナ", "author_id": "u-ミナ", "bot": False, "me": False, "text": "あかりほんと使えねえな", "to_me": True}
        async def go():
            a.quiet_sec = 0.05
            a.persona.choose = lambda scene: "none"
            await a.on_message(m)
            await asyncio.sleep(0.2)
            clock["t"] += timedelta(minutes=11)
            await a.tick()
        asyncio.run(go())
        reads = [c for c in a.jev.calls if "record" in c[1]]
        self.assertEqual(len(reads), 1)
        self.assertIn("attack", reads[0][1])
        self.assertIn("【相手】", reads[0][0])
        self.assertEqual(a.persona.anger_of("u-ミナ"), 40)
