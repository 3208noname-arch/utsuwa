# -*- coding: utf-8 -*-
from datetime import datetime

from personas.nagi.engine import names as N

n = 0


def ok(cond, msg):
    global n
    assert cond, msg
    n += 1


T = datetime(2026, 10, 9, 20, 0)
ok(N.clean("ミナさん") == "ミナ", "敬称は外す")
ok(N.clean("「なぎ」") == "なぎ", "括弧は外す")
ok(N.clean("みっちゃん") == "みっちゃん", "ちゃんはあだ名の一部")
ok(N.valid("みなっち", "みなっちさぁ、それ違う"), "本文にあれば受け付ける")
ok(not N.valid("ミナ", "みなさぁ"), "本文に無い言い換えは受け付けない")
ok(not N.valid("@N4Gi", "@N4Gi 起きてる？"), "メンションは呼び名ではない")
ok(not N.valid("おめえ", "ほんと使えねえなおめえ") and not N.valid("あなた", "あなたは？"), "代名詞は呼び名ではない")
t = "ちゃんなぎ、確か茄子苦手だよね"
ok(N.clean(N.around(t, 3, 5)) == "ちゃんなぎ", "前の「ちゃん」も含める")
t = "なぎちゃんは栗苦手なん？"
ok(N.clean(N.around(t, 0, 2)) == "なぎちゃん", "後ろの「ちゃん」も含める")
t = "なぎさん起きてる？"
ok(N.clean(N.around(t, 0, 2)) == "なぎ", "「さん」は含めてから外す")
t = "なぎ、おはよう"
ok(N.around(t, 0, 2) == "なぎ", "何も付いていなければそのまま")

s = {}
N.record(s, 1, "ミナ", "みなっち", 10, T)
N.record(s, 1, "ミナ", "みなっち", 10, T)
N.record(s, 1, "ミナ", "みなっち", 10, T)
ok(N.adopted(s, 1) == [], "1人の癖だけでは採用しない")
N.record(s, 1, "ミナ", "みなっち", 11, T)
ok(N.adopted(s, 1) == ["みなっち"], "2人以上・3回以上で採用")
N.record(s, 99, "N4Gi", "なぎ", 10, T)
ok(N.format_summary(s, 99).startswith("ナギ: なぎ 1回/1人"), "ナギへの呼び名を先に出す")

print(f"ok {n}")
