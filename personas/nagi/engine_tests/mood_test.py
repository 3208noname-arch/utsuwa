# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from personas.nagi.engine import mood as M

T = datetime(2026, 10, 11, 15, 0)
n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


# 基準線
ok(M.baseline(30, 30, 70) == 0, "体が整っていれば0")
ok(abs(M.baseline(85, 30, 70) - (-12)) < 0.01, "眠気65を超えた分×0.6だけ下がる")
ok(M.baseline(30, 30, 70, broke=True) == -5, "金欠で少し下がる")

# 戻り方
m = M.new(T); M.nudge(m, -20, T, "ユウにけなされた")
M.settle(m, T + timedelta(minutes=60), 0)
ok(abs(m["v"] - (-10)) < 0.01, "1時間で半分戻る")
M.settle(m, T + timedelta(minutes=120), -12)
ok(abs(m["v"] - (-11)) < 0.01, "戻る先は体で決まる基準線")

# 動かし方
m = M.new(T)
ok(M.nudge(m, 150, T, "x") == 100 and m["v"] == 100, "上限で切る")
m = M.new(T)
M.nudge(m, 0.2, T, "小さすぎる")
ok(m["events"] == [], "小さい動きは出来事に残さない")
ok(M.people_delta("attack", "me") < 0 < M.people_delta("support", "me"), "けなしで下がり、庇いで上がる")
ok(M.people_delta(None, "me") == 0, "何も起きていなければ0")
ok(M.people_delta("joke", "me") > 0, "冗談で少し上がる")
ok(M.people_delta("joke", "close") == 0, "第三者への冗談では動かない")
ok(M.people_reason("joke", "トオル") == "トオルの冗談で笑った", "理由の文")
ok(M.people_reason("attack", "ユウ", by="トオル") == "ユウがトオルにけなされた", "第三者への言動")
ok(M.chat_delta(60) == 3 and M.chat_delta(-40) == -2, "話しかけられた時の向きは好感度で決まる")

# 話題
topics = [{"name": "まかない", "valence": 0.8}, {"name": "虫", "valence": -0.8}, {"name": "政治", "valence": -0.1}]
ok(M.topic_hit({"まかない": 0.9, "虫": 0.95}, topics)[0] == "虫", "強く効く方を1つ")
ok(M.topic_hit({"まかない": 0.5}, topics) is None, "当てはまりが低ければ話題と読まない")
ok(M.topic_hit({}, topics) is None, "Jev失敗は何も起きない")

# 言葉
m = M.new(T); M.nudge(m, -20, T, "ユウにけなされた")
ok(M.as_words(m, 0) == "ちょっと機嫌が悪い（ユウにけなされた）", "理由を添える")
m = M.new(T); m["v"] = -20
ok(M.as_words(m, -15) == "ちょっと機嫌が悪い（体がしんどい）", "出来事が無ければ体のせい")
ok(M.as_words(M.new(T), 0) == "ふつう", "ふつうは理由を付けない")
# 推移
h = M.record([], T, 5)
h = M.record(h, T + timedelta(minutes=3), 9)
ok(h == [[T.isoformat(timespec="minutes"), 5]], "10分たつまで次の点を打たない")
h = M.record(h, T + timedelta(minutes=10), -3)
ok(len(h) == 2 and h[-1][1] == -3, "10分ごとに残す")
h = M.record(h, T + timedelta(hours=49), 0)
ok(len(h) == 1, "48時間より古い点は捨てる")
print(f"ok {n}")
