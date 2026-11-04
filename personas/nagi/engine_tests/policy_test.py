# -*- coding: utf-8 -*-
from personas.nagi.engine import policy as P

n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


def near(a, b, eps=0.02):
    return abs(a - b) < eps


p = P.probs(P.logits(0, 0, 0, {}))
ok(near(p["none"], 0.75) and near(p["reaction"], 0.15) and near(p["short"], 0.10), "何も無ければ、それまでの確率表")
p = P.probs(P.logits(1, 0, 0, {}))
ok(near(p["normal"], 0.30, 0.03) and near(p["short"], 0.25, 0.03) and near(p["none"], 0.25, 0.03), "熱が最大なら、食いつく話題の確率表")
ok(P.heat_of(0.4) == 0 and P.heat_of(0.8) == 1 and near(P.heat_of(0.6), 0.5), "覚醒0.4〜0.8で熱が0→1")

hot_like = P.probs(P.logits(1, 0.8, 0, {}))
hot_hate = P.probs(P.logits(1, -0.8, 0, {}))
ok(hot_like["normal"] > hot_hate["normal"] and hot_hate["reaction"] > hot_like["reaction"] * 0.9,
   "嫌いな話題は普通に返すより一言・リアクションに寄る")
good = P.probs(P.logits(0, 0, 30, {}))
bad = P.probs(P.logits(0, 0, -30, {}))
ok(good["none"] < 0.75 < bad["none"], "機嫌が良いと混ざりやすく、悪いと黙りやすい")
ok(P.probs(P.logits(0, 0, 0, {"short": 10}))["short"] > 0.10, "混ざって良かった型は選ばれやすくなる")

ok(P.pick(P.logits(0, 0, 0, {}), 0.0) == "none" and P.pick(P.logits(0, 0, 0, {}), 0.999) in P.KINDS, "乱数で選ぶ")
ok(P.probs(P.logits(1, 0, 0, {}, grudge=2))["none"] > P.probs(P.logits(1, 0, 0, {}))["none"],
   "怒っている相手の発言には混ざりにくい")
q = {}
P.update(q, "short", 5)
ok(q["short"] == 0.5, "予想は気分の動きへ1割寄せる")
P.update(q, "short", 5)
ok(q["short"] == 0.95, "続けば近づく")
print(f"ok {n}")
