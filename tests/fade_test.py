"""薄れ層（harness/fade.py）：強さ・3段・うろ覚えの提示・部品スイッチ・数字チェック・exact の完全一致・replay の human 場面。"""
import asyncio
import json
import math
import os
import unittest
from datetime import date, datetime

import yaml

from harness import context, fade, replay, writer
from harness.agent import Agent
from harness.context import Row
from harness.outbox import Intent
from harness.vault import Vault
from tests.fakes import FakeJev, FakeLLM, FakeSender, TEMPLATE, copy_template

TODAY = date(2026, 11, 13)
HUMAN = {"recall": {"mode": "human"}}


def row(kind="常識", name="n", day="2026-11-13", p=0.9, body="本文", **kw):
    return Row(f"01 Lessons/{kind}/{name}.md", kind, name, day, body=body, p=p, **kw)


def cfg(parts=None, tau=None, mode="human"):
    r = {"mode": mode}
    if parts is not None:
        r["parts"] = parts
    if tau is not None:
        r["tau"] = tau
    return {"recall": r}


def ago(n):
    return date.fromordinal(TODAY.toordinal() - n).isoformat()


class StrengthTest(unittest.TestCase):
    def test_decay_formula(self):
        r = row("反省", day=ago(180), p=0.9)
        self.assertAlmostEqual(fade.strength(r, cfg(), TODAY), 0.9 * math.exp(-1), places=6)

    def test_decay_part_off_ignores_age(self):
        r = row("反省", day=ago(180), p=0.9)
        self.assertAlmostEqual(fade.strength(r, cfg({"decay": False}), TODAY), 0.9)

    def test_tau_by_kind_and_config_override(self):
        age = ago(60)
        want = {"常識": 365, "反省": 180, "思い出": 60, "スマホのメモ": 30, "その他": 30, "日記": 30, "未知": 30}
        for k, t in want.items():
            self.assertAlmostEqual(fade.strength(row(k, day=age, p=1.0), cfg(), TODAY), math.exp(-60 / t), places=6, msg=k)
        c = cfg(tau={"思い出": 600})
        self.assertAlmostEqual(fade.strength(row("思い出", day=age, p=1.0), c, TODAY), math.exp(-0.1), places=6)

    def test_bad_date_is_not_faded(self):
        self.assertAlmostEqual(fade.strength(row(day="不明", p=0.7), cfg(), TODAY), 0.7)


class TierTest(unittest.TestCase):
    def apply(self, rows, config=None):
        return fade.apply(context.render(rows), rows, config or cfg(), TODAY)

    def test_boundaries(self):
        """0.55 ちょうどは鮮明、0.30 ちょうどは曖昧、わずかに下は一段下。"""
        for p, where in ((0.55, "vivid"), (0.5499, "blur"), (0.30, "blur"), (0.2999, "dropped")):
            _, _, info = self.apply([row(p=p)], cfg({"decay": False}))
            self.assertEqual(info[where], ["n"], p)

    def test_vivid_passes_body_as_today(self):
        r = row(p=0.9, body="水は安い")
        sec, faded, info = self.apply([r])
        self.assertEqual(sec, context.render([r]))
        self.assertEqual(faded, [])

    def test_blur_gives_heading_and_tag_only_max_two(self):
        rows = [row(name=f"店の話{i}", day=ago(10), p=0.5 + i * 0.01, body=f"秘密の本文{i}", people=["ミナ"],
                    scene="ランチの相談") for i in range(3)]
        sec, faded, info = self.apply(rows, cfg({"decay": False}))
        text = sec[fade.SECTION]
        self.assertEqual(len(text.split("\n")), 2)               # 最大2行
        self.assertIn("[先週] 店の話2（ミナ）｜ランチの相談", text)   # 強い順
        self.assertNotIn("秘密の本文", "".join(sec.values()))      # 本文は渡さない
        self.assertEqual(len(faded), 3)                           # 3行目も本文は渡していない
        self.assertNotIn("覚えていること", sec)

    def test_diary_heading_hides_body(self):
        d = Row("00 Diary/x.md", context.DIARY, "2026-11-09の日記（財布に4800円あった）", "2026-11-09", body="財布に4800円あった", p=0.4)
        sec, _, _ = self.apply([d], cfg({"decay": False}))
        self.assertNotIn("4800", sec[fade.SECTION])

    def test_tags(self):
        want = {0: "今日", 1: "数日前", 6: "数日前", 7: "先週", 13: "先週", 14: "先月ごろ", 59: "先月ごろ", 60: "だいぶ前"}
        for n, t in want.items():
            self.assertEqual(fade.tag(n), t, n)

    def test_blur_part_off_gives_body(self):
        r = row(p=0.4, body="水は安い")
        sec, faded, _ = self.apply([r], cfg({"decay": False, "blur": False}))
        self.assertEqual(sec, context.render([r]))
        self.assertEqual(faded, [])
        sec, _, _ = self.apply([r], cfg({"decay": False}))
        self.assertNotIn("水は安い", "".join(sec.values()))

    def test_tip_one_line_only_for_high_probability(self):
        rows = [row(name="a", day=ago(900), p=0.9), row(name="b", day=ago(900), p=0.95), row(name="c", day=ago(900), p=0.8)]
        sec, _, info = self.apply(rows)
        self.assertEqual(sec[fade.SECTION], f"- {fade.TIP_LINE}")   # 2件が条件を満たしても1行
        self.assertEqual(sorted(info["dropped"]), ["a", "b", "c"])
        sec, _, info = self.apply([rows[2]])
        self.assertEqual(sec, {})                                   # 0.85 未満は何も残さない
        sec, _, _ = self.apply([row(day=ago(900), p=0.85)])
        self.assertIn(fade.TIP_LINE, sec[fade.SECTION])             # 0.85 ちょうどは残す

    def test_tip_part_off(self):
        sec, _, info = self.apply([row(day=ago(900), p=0.95)], cfg({"tip": False}))
        self.assertEqual(sec, {})
        self.assertEqual(info["dropped"], ["n"])

    def test_tip_shares_section_with_blur(self):
        rows = [row("その他", name="a", day=ago(20), p=0.9), row("その他", name="b", day=ago(900), p=0.95)]   # 0.46 と 0
        sec, _, _ = self.apply(rows)
        self.assertEqual(sec[fade.SECTION].split("\n"), ["- [先月ごろ] a", f"- {fade.TIP_LINE}"])

    def test_exact_returns_input_untouched(self):
        rows = [row(day=ago(900), p=0.9)]
        mem = context.render(rows)
        for c in ({}, {"recall": {}}, cfg(mode="exact"), cfg({"decay": True}, mode="exact")):
            out, faded, info = fade.apply(mem, rows, c, TODAY)
            self.assertIs(out, mem)
            self.assertEqual((faded, info), ([], {}))

    def test_deterministic(self):
        rows = [row(name=str(i), day=ago(i * 20), p=0.6 + i * 0.05) for i in range(5)]
        a = fade.apply(context.render(rows), rows, cfg(), TODAY)
        b = fade.apply(context.render(rows), rows, cfg(), TODAY)
        self.assertEqual(a[0], b[0])


class CheckerTest(unittest.TestCase):
    def test_leaked_number_and_date(self):
        hidden = [row(body="ラーメンは820円だった", day="2026-04-01")]
        self.assertEqual(writer.leaked(["820円だったよね"], hidden, "【今】2026-11-13"), ["820"])
        self.assertEqual(writer.leaked(["4月1日だったよね"], hidden, "x"), ["4/1"])
        self.assertEqual(writer.leaked(["04-01 だった"], hidden, "x"), ["4/1"])
        self.assertEqual(writer.leaked(["いい店だった"], hidden, "x"), [])
        self.assertEqual(writer.leaked(["820円だったよね"], hidden, "会話: 820円だった"), [])   # 渡した物にもあれば言える
        self.assertEqual(writer.leaked(["820円"], [], "x"), [])

    def test_write_drops_leak_even_when_number_check_is_log(self):
        faded = [row(body="ラーメンは820円だった")]
        llm = FakeLLM([json.dumps({"candidates": [{"messages": ["820円だったよね"]}, {"messages": ["安かった気がする"]}]})])
        jev = FakeJev(lambda s, n, q: 0.9 if n.startswith("c") else None)
        out = asyncio.run(writer.write(llm, jev, Vault(copy_template()), "あかり", {"今": "x"}, "normal", [],
                                       faded=faded))
        self.assertEqual(out, ["安かった気がする"])

    def test_write_keeps_numbers_in_passed_context(self):
        """渡した文脈（鮮明な行）にある数字は言える。"""
        llm = FakeLLM([json.dumps({"candidates": [{"messages": ["820円だったよね"]}]})])
        jev = FakeJev(lambda s, n, q: 0.9 if n.startswith("c") else None)
        out = asyncio.run(writer.write(llm, jev, Vault(copy_template()), "あかり",
                                       {"覚えていること": "- ラーメンは820円だった"}, "normal", [], faded=[row(body="820円")]))
        self.assertEqual(out, ["820円だったよね"])


T = datetime(2026, 11, 13, 20, 0)


def note(v, kind, name, day, body, words=("語",)):
    d = os.path.join(v.root, "01 Lessons", {"常識": "01 常識", "その他": "03 その他"}[kind])
    w = "".join(f"- {x}\n" for x in words)
    with open(os.path.join(d, name + ".md"), "w", encoding="utf-8") as f:
        f.write(f"---\ndate: {day}\nstatus: active\n種類: {kind}\n思い出す言葉:\n{w}---\n{body}\n")


def jev_rule(state, name, q):
    if name.startswith("k"):
        return 0.9 if "「常識」" in q["instructions"] else 0.1
    if name.startswith("c"):
        return 0.9
    ln = [x for x in state.split("【記憶の表】\n")[1].split("\n") if x.startswith(f"{int(name[1:])}. ")][0]
    return 0.9 if ("古い値段" in ln or "新しい値段" in ln) else 0.0


def build(config, reply="うん"):
    llm = FakeLLM(lambda m: json.dumps({"candidates": [{"messages": [reply]}]}))
    a = Agent(Vault(copy_template()), FakeJev(jev_rule), llm, FakeSender(), clock=lambda: T, notice=lambda p, x: 0)
    a.config.update(config)
    note(a.vault, "常識", "古い値段", "2026-04-01", "あの店のラーメンは820円だった。")
    note(a.vault, "常識", "新しい値段", "2026-11-12", "あの店のうどんは640円だ。")
    return a, llm


def compose(a):
    target = {"id": "9", "author": "ミナ", "author_id": "u1", "text": "やあ", "channel": "c", "at": T.isoformat()}
    out = asyncio.run(a.compose(Intent("reply", "c", "normal", T, target=target)))
    return out, a.llm.calls[0][-1]["content"]


class ComposeTest(unittest.TestCase):
    def test_exact_is_identical_to_no_recall_key(self):
        """recall が無い・exact の時、writer に渡る文脈が現行（薄れ層なし）と1字まで同じ。"""
        a0, _ = build({})
        a0.config.pop("recall", None)
        a1, _ = build(cfg(mode="exact"))
        a2, _ = build(cfg({"decay": True, "blur": True, "tip": True}, mode="exact"))
        _, p0 = compose(a0)
        self.assertEqual(compose(a1)[1], p0)
        self.assertEqual(compose(a2)[1], p0)
        self.assertIn("あの店のラーメンは820円だった。（04-01）", p0)       # 現行の渡し方（本文＋日付）
        self.assertIn("あの店のうどんは640円だ。（11-12）", p0)

    def test_human_fades_old_row_but_keeps_new_one(self):
        a, _ = build(HUMAN)
        _, prompt = compose(a)
        self.assertIn("640円", prompt)                      # 新しい（強さ 0.9×e^(-1/365)）は鮮明
        self.assertNotIn("820円", prompt)                   # 古い（0.9×e^(-226/365)=0.48）は曖昧：本文なし
        self.assertIn(fade.SECTION, prompt)
        self.assertIn("[だいぶ前] 古い値段", prompt)

    def test_human_blocks_fabricated_number_from_faded_row(self):
        a, _ = build(HUMAN, reply="820円だったよね")
        out, _ = compose(a)
        self.assertIsNone(out)                              # 唯一の案が落ちて、書き直しも落ちて黙る

    def test_fixed_frames_are_not_faded(self):
        """約束・人物・近況の固定枠は、human でも exact と同じ。"""
        def frames(config):
            a, _ = build(config)
            target = {"id": "9", "author": "ミナ", "author_id": "u1", "text": "やあ", "channel": "c", "at": T.isoformat()}
            s, _ = a.sections(Intent("reply", "c", "normal", T, target=target), T)
            return {k: v for k, v in s.items() if k not in ("今", "返信先")}
        self.assertEqual(frames(cfg(mode="exact")), frames(HUMAN))


def lesson(root, kind, name, day, words, body):
    d = os.path.join(root, "01 Lessons", kind)
    os.makedirs(d, exist_ok=True)
    w = "".join(f"- {x}\n" for x in words)
    with open(os.path.join(d, name + ".md"), "w", encoding="utf-8") as f:
        f.write(f"---\ndate: {day}\nstatus: active\n種類: {kind}\n思い出す言葉:\n{w}---\n{body}\n")


def rt(at, ctx, comment="x"):
    return {"at": at, "msg_at": at, "text": ctx[-1], "comment": comment, "label": "ズレてる", "context": list(ctx)}


class ReplayHumanTest(unittest.TestCase):
    """replay の human 場面。Jev の行確率は、行の見出しに含む「p九五」のような印で決める（FakeJev）。"""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp(prefix="fade-")
        self.v = Vault(self.root)

    def run_replay(self, ratings, config, p_of):
        def rule(state, name, q):
            if name.startswith("k"):
                return 0.9
            ln = [x for x in state.split("【記憶の表】\n")[1].split("\n") if x.startswith(f"{int(name[1:])}. ")][0]
            return p_of(ln)
        return asyncio.run(replay.replay(FakeJev(rule), self.v, ratings, "ナギ", config=config))

    def scene(self, kind, name, day, p, body="秘密の本文820円"):
        lesson(self.root, kind, name, day, ["語"], body)
        res = self.run_replay([rt("2026-11-13T20:00:00", ["A: こんにちは"])], HUMAN, lambda ln: p if name in ln else 0.0)
        return res[0]

    def test_scene_old_unimportant_is_blur_or_dropped(self):
        x = self.scene("その他", "どうでもいい昔の話", "2026-10-24", 0.6)      # 20日前、その他 τ30、s=0.31
        self.assertEqual(x["picked"], [("どうでもいい昔の話", "その他")])
        self.assertNotIn("どうでもいい昔の話", x["fade"]["vivid"])
        self.assertIn(x["fade"]["blur"] or x["fade"]["dropped"], (["どうでもいい昔の話"],))
        self.assertNotIn("820", "".join(x["mem"].values()))

    def test_scene_new_important_is_vivid(self):
        x = self.scene("反省", "昨日の大事な指摘", "2026-11-12", 0.9)
        self.assertEqual(x["fade"]["vivid"], ["昨日の大事な指摘"])
        self.assertIn("秘密の本文820円", "".join(x["mem"].values()))

    def test_scene_named_old_memory_leaves_one_trace_line(self):
        x = self.scene("思い出", "名指しされた昔の出来事", "2025-10-01", 0.95)    # 408日前、τ60 → s≒0
        self.assertEqual(x["fade"]["dropped"], ["名指しされた昔の出来事"])
        self.assertTrue(x["fade"]["tip"])
        self.assertEqual(x["mem"], {fade.SECTION: f"- {fade.TIP_LINE}"})

    def test_exact_picked_and_hits_equal_current_and_human(self):
        """exact（と recall なし）で選ばれる行と、ヒット 4/4 が現行と同じ。human でも選ぶ集合は変わらない。"""
        names = ["水の話", "金の話", "猫の話", "駅の話"]
        words = ["水", "金", "猫", "駅"]
        for n, w in zip(names, words):
            lesson(self.root, "常識", n, "2026-11-13", [w], f"{n}の本文")
        ratings = [rt("2026-11-13T09:00:00", [f"A: {w}の話"], comment=w) for w in words]
        def run(config):
            def r(state, name, q):
                if name.startswith("k"):
                    return 0.9
                convo = state.split("【記憶の表】")[0]
                ln = [x for x in state.split("【記憶の表】\n")[1].split("\n") if x.startswith(f"{int(name[1:])}. ")][0]
                return 0.9 if any(f"思い出す言葉: {w}" in ln and f"{w}の話" in convo for w in words) else 0.0
            return asyncio.run(replay.replay(FakeJev(r), self.v, ratings, "ナギ", config=config))
        base, ex, hu = run(None), run(cfg(mode="exact")), run(HUMAN)
        self.assertEqual(replay.total(base), (4, 4))
        self.assertEqual(replay.total(ex), (4, 4))
        self.assertEqual([x["picked"] for x in ex], [x["picked"] for x in base])
        self.assertEqual([x["picked"] for x in ex], [[(n, "常識")] for n in names])
        self.assertEqual([x["picked"] for x in hu], [x["picked"] for x in base])
        self.assertEqual([x["mem"] for x in ex], [x["mem"] for x in base])
        self.assertEqual([x["fade"] for x in ex], [{}] * 4)


class TemplateConfigTest(unittest.TestCase):
    def test_sample_config_has_recall_exact(self):
        for p in (os.path.join(TEMPLATE, "Agents", "config.yaml"),
                  os.path.join(os.path.dirname(TEMPLATE), "..", "personas", "nagi", "config.example.yaml")):
            c = yaml.safe_load(open(p, encoding="utf-8"))
            self.assertEqual(c["recall"]["mode"], "exact", p)


if __name__ == "__main__":
    unittest.main()
