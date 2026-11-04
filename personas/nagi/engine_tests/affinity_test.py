# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from personas.nagi.engine import affinity as A

n = 0


def ok(cond, msg):
    global n
    assert cond, msg
    n += 1


T = datetime(2026, 10, 9, 20, 0)

# 判定
ok(A.judge(0.9, 0.2, 0.1) == "attack", "本気のけなし")
ok(A.judge(0.9, 0.8, 0.1) is None, "いじりなら下げない")
ok(A.judge(0.1, 0.1, 0.9) == "support", "庇う・褒める")
ok(A.judge(None, None, None) is None, "Jevの失敗は何も起きない扱い")
ok(A.judge(0.1, 0.3, 0.9, 0.9) == "joke", "褒め言葉の形でも、冗談の前振りなら冗談")
ok(A.judge(0.1, 0.6, 0.9, 0.2) is None, "いじり混じりの褒めは本心と読まない")
ok(A.judge(0.1, 0.1, 0.9, 0.2) == "support", "冗談でなければ褒め")
ok(A.DELTA[("joke", "me")] == 2, "冗談は+2")
ok(A.judge(0.31, 0.76, 0.1, 0.32, 0.93) == "belittle" and A.DELTA[("belittle", "me")] == -1,
   "いじりの調子でも、子ども扱いは見下し（-1）")
ok(A.judge(0.03, 0.9, 0.39, 0.83, 0.37) == "joke", "冗談と読めるなら冗談")
ok(A.judge(0.9, 0.2, 0.1, 0.1, 0.9) == "attack", "本気のけなしは見下しより重い方")
ok(not A.ripe(3, timedelta(minutes=2)) and A.ripe(6, timedelta(0)) and A.ripe(0, timedelta(minutes=10)),
   "6件続くか10分経ったら読む")

# 増減
s = {}
p = A.get(s, 1, "ミナ", T)
ok(p["v"] == A.INITIAL, "最初は+20")
ok(A.apply(p, A.DELTA[("attack", "me")], T, "けなされた", "使えねえな") == -8 and p["v"] == 12, "けなされたら-8")
ok(p["events"][-1]["quote"] == "使えねえな", "出来事を残す")
A.apply(p, -8, T, "けなされた")
ok(p["v"] == 5 and p["minus"] == -15, "1日の減りは-15まで")
ok(A.apply(p, -8, T, "けなされた") == 0 and len(p["events"]) == 2, "上限に達したら動かず、出来事も残さない")
ok(A.chat(p, T) == 1 and A.chat(p, T) == 1 and A.chat(p, T) == 1 and A.chat(p, T) == 0, "話しかけは1日3回まで")

# 日をまたぐ
p = A.get(s, 1, "ミナ", T + timedelta(days=1))
ok(abs(p["v"] - 8 * 0.9) < 0.01 and p["minus"] == 0 and p["chats"] == 0, "翌日は1割戻り、上限も戻る")
p = A.get(s, 1, "ミナ", T + timedelta(days=20))
ok(p["events"] == [], "14日より古い出来事は消える")

# 第三者
ok(A.relation(60) == "close" and A.relation(20) == "friend" and A.relation(0) is None, "段階")
ok(A.band(20) == "好き" and A.band(0) == "ふつう" and A.band(-60) == "苦手", "段階の名前")

# 時刻の言葉
ok(A.when(T - timedelta(minutes=10), T) == "さっき", "30分以内はさっき")
ok(A.when(T.replace(hour=13), T) == "今日の昼", "同じ日")
ok(A.when(T - timedelta(days=1), T) == "昨日の夜", "前日")
ok(A.when(T.replace(hour=2) + timedelta(days=1), T + timedelta(days=1)) == "昨日の夜中", "夜中2時は前の日に数える")
ok(A.when(T - timedelta(days=3), T) == "3日前", "それより前は日数")

print(f"ok {n}")
