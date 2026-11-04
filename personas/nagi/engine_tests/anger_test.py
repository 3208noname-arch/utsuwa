# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from personas.nagi.engine import anger as A

T = datetime(2026, 10, 24, 15, 0)
n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


s = {}
ok(A.hit(s, 1, "ミナ", "attack", "ほんと使えねえなおめえ", T) == 40 and A.level(s, 1, T) == 40, "本気のけなしで40")
ok(A.hit(s, 1, "ミナ", "joke", "x", T) == 0, "冗談では怒らない")
A.hit(s, 1, "ミナ", "belittle", "やーいお子様", T)
ok(A.level(s, 1, T) == 55 and s["1"]["why"] == "やーいお子様", "見下しは15。理由は最新に")
ok(abs(A.level(s, 1, T + timedelta(hours=2)) - 27.5) < 0.01, "2時間で半分に冷める")
s2 = {}
A.hit(s2, 2, "トオル", "attack", "x", T, close=True)
ok(abs(A.level(s2, 2, T + timedelta(hours=1), close=True) - 20) < 0.01, "仲のいい相手は1時間で半分")
ok(A.level({}, 9, T) == 0, "怒っていなければ0")

# 謝られる
s = {}
A.hit(s, 1, "ミナ", "attack", "x", T)
A.hit(s, 1, "ミナ", "attack", "x", T)
ok(A.apologize(s, 1, 0.9, 0.0, T) == 64.0 and abs(A.level(s, 1, T) - 16) < 0.01, "本気で謝られたら8割減る")
s = {}
A.hit(s, 1, "ミナ", "attack", "x", T)
A.hit(s, 1, "ミナ", "attack", "x", T)
ok(A.apologize(s, 1, 0.9, 1.0, T) == 16.0 and abs(A.level(s, 1, T) - 64) < 0.01, "「ごめんw」程度なら2割だけ")
ok(A.apologize(s, 1, 0.3, 0.0, T) == 0, "謝っていなければ減らない")
s = {}
A.hit(s, 1, "ミナ", "attack", "x", T)
ok(abs(A.apologize(s, 1, 0.97, 0.56, T) - 29.1) < 0.1, "軽さ0.56（実測の本気の謝罪）はほぼ本気として7割減る")
s = {}
A.hit(s, 1, "ミナ", "belittle", "x", T)
A.apologize(s, 1, 0.95, 0.0, T)
ok("1" not in s, "ほぼ消えたら忘れる")

# 言葉
s = {}
A.hit(s, 1, "ミナ", "attack", "ほんと使えねえな", T)
A.hit(s, 1, "ミナ", "belittle", "やーいお子様", T)
w = A.as_words(s["1"], A.level(s, 1, T))
ok(w.startswith("ミナにかなり怒っている（理由: 「やーいお子様」"), "怒りの強さと理由を渡す")
ok(A.as_words({"display": "x", "why": "y"}, 1) is None, "冷めていれば渡さない")
print(f"ok {n}")
