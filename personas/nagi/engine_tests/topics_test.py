# -*- coding: utf-8 -*-
from personas.nagi.engine import topics as P

n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


ts = [{"name": "まかない", "valence": 0.8, "arousal": 0.7, "note": ""},
      {"name": "虫", "valence": -0.8, "arousal": 0.9, "note": ""}]

ok(P.learn(ts, "カラオケ", 0.6, "2026-10-12T10:00:00") == "added", "新しい話題を足す")
k = ts[-1]
ok(k["source"] == "learned" and k["valence"] == 0.6 and k["arousal"] == 0.5, "覚えた印・言った向き・覚醒0.5")
ok(P.learn(ts, "カラオケ", -1.0, "2026-10-12T11:00:00") == "moved" and k["valence"] == 0.12, "言うたびに3割寄せる")
ok(P.learn(ts, "虫", 1.0, "2026-10-12T11:00:00") == "kept" and ts[1]["valence"] == -0.8, "人が置いた話題は書き換えない")
ok(P.learn(ts, "「ピーマン」", 5, "x") == "added" and ts[-1]["name"] == "ピーマン" and ts[-1]["valence"] == 1.0,
   "かぎ括弧を外し、快は-1〜1で切る")
ok(P.learn(ts, "", 0.5, "x") is None and P.learn(ts, "a" * 21, 0.5, "x") is None, "名前は1〜20文字")
ok(P.learn(ts, "銭湯", "好き", "x") is None, "快が数でなければ何もしない")

ts = [{"name": "虫", "valence": -0.8, "arousal": 0.9}]
for i in range(P.RECENT_N + 5):
    P.learn(ts, f"話題{i}", 0.5, f"2026-10-12T10:{i:02d}:00")
ok(len(ts) == P.RECENT_N + 6, "覚えた話題は消さない")
names = [t["name"] for t in P.active(ts, "")]
ok(len(names) == P.RECENT_N + 1 and "虫" in names and "話題0" not in names, "人が置いた話題と、最近言った分だけ使う")
ok("話題0" in [t["name"] for t in P.active(ts, "そういえば話題0ってさ")], "会話に名前が出たら引っ張り出す")
ok(P.mentioned({"name": "ピーマン・ナス"}, "ナス嫌い"), "「・」で区切った一部でも出たとみなす")

ts = [{"name": "まかない", "valence": 0.8, "arousal": 0.7}, {"name": "政治", "valence": -0.1, "arousal": 0.1},
      {"name": "虫", "valence": -0.8, "arousal": 0.9}]
ok(P.as_words(ts) == "好き: まかない／嫌い: 虫", "どうでもいい話題は渡さない")
# 経験で育てる
t = {"name": "まかない", "valence": 0.8, "arousal": 0.7}
P.experience(t, True, 5)
ok(t["valence"] == 0.83 and t["arousal"] == 0.73 and t["anchor"] == {"valence": 0.8, "arousal": 0.7},
   "混ざって良い反応なら快も覚醒も上がる。人が置いた場所を錨として覚える")
for _ in range(20):
    P.experience(t, True, 5)
ok(t["valence"] == 1.0 and t["arousal"] == 0.9, "人が置いた話題は錨から±0.2まで（快は1で頭打ち）")
for _ in range(100):
    P.experience(t, False, -5)
ok(t["valence"] == 0.6 and t["arousal"] == 0.5, "下げる時も錨から-0.2まで")
k = {"name": "カラオケ", "valence": 0.1, "arousal": 0.5, "source": "learned"}
for _ in range(20):
    P.experience(k, False, -5)
ok(k["valence"] == -0.5 and k["arousal"] == 0.3 and "anchor" not in k, "覚えた話題は錨なしで動く")
ok(P.experience({"name": "x", "valence": 0, "arousal": 0.5, "source": "learned"}, True, 1) == (0, 0.03),
   "気分の動きが小さければ快は動かさない")
print(f"ok {n}")
