# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from pathlib import Path

from harness.schedule import Schedule
from personas.nagi.engine import decide as D

# 予定は SCHEDULE.md から読む。テストは minato の時間割を写した見本で動かす
D.PLAN = Schedule.parse((Path(__file__).parent.parent / "SCHEDULE.example.md").read_text(encoding="utf-8"))

MON = datetime(2026, 10, 5)   # 月曜（1限あり）
TUE = datetime(2026, 10, 6)   # 火曜（1限なし・バイト）
SUN = datetime(2026, 10, 11)
n = 0


def eq(got, want, what):
    global n
    assert got == want, f"{what}: {got!r} != {want!r}"
    n += 1


def st(t):
    return D.activity(t)["state"]


# 今していること
eq(st(MON.replace(hour=5)), "寝ている", "朝5時は寝ている")
eq(st(MON.replace(hour=7, minute=59)), "寝ている", "1限の日は8時まで寝ている")
eq(st(MON.replace(hour=8, minute=30)), "起きたて", "1限の日は8時起き")
eq(st(MON.replace(hour=9, minute=30)), "講義中", "月1限は語学")
eq(st(TUE.replace(hour=9, minute=30)), "寝ている", "1限が無い日は10時半まで寝ている")
eq(st(TUE.replace(hour=11)), "講義中", "火2限")
eq(st(TUE.replace(hour=20)), "バイト中", "火曜20時はバイト")
eq(st(TUE.replace(hour=23, minute=59)), "バイト中", "ラストまでバイト")
eq(st(MON.replace(hour=20)), "ふつう", "月曜20時はバイトなし")
eq(st(MON.replace(hour=1)), "深夜", "1時は深夜")
eq(st(SUN.replace(hour=11)), "起きたて", "日曜11時は起きたて")
eq(D.activity(MON.replace(hour=1))["device"], "PC", "深夜はPC")
eq(D.activity(TUE.replace(hour=11))["device"], "スマホ", "講義中はスマホ")
eq(D.activity(TUE.replace(hour=20))["until"], TUE.replace(hour=23, minute=59) + timedelta(minutes=1), "バイトは24時まで")

# 上限
c = {}
t = MON.replace(hour=20)
eq(D.budget_ok(c, 1, t), True, "初回はOK")
for _ in range(D.USER_DAILY_MAX):
    D.count(c, 1, t)
eq(D.budget_ok(c, 1, t), False, "1人の上限で止まる")
eq(D.budget_ok(c, 2, t), True, "別の人はまだOK")
eq(D.budget_ok(c, 1, t + timedelta(days=1)), True, "翌日は戻る")
eq(D.day_key(TUE.replace(hour=2)), "2026-10-05", "深夜2時は前日に数える")
c2 = {"day": D.day_key(t), "total": D.TOTAL_DAILY_MAX, "users": {}}
eq(D.budget_ok(c2, 9, t), False, "全体の上限で止まる")

# 発言シェア
me = "湊"
recent = [{"author": "A"}, {"author": me}, {"author": "B"}, {"author": me}]
eq(D.my_share(recent, me), (0.5, 2), "シェアと人数")
eq(D.my_share([{"author": "A"}, {"author": me}, {"author": me}, {"author": "B"}], me)[0], 0.25, "連投は1回と数える")
eq(D.my_share([{"author": "A"}, {"author": "X", "bot": True}], me)[1], 1, "Botは人数に数えない")

# 型の選択
eq(D.choose(True, None, 0.9, 3, "寝ている", 0.0), "defer", "寝ている間に呼ばれたら後で返す")
eq(D.choose(False, 0.99, 0.0, 3, "寝ている", 0.0), "none", "寝ている間の雑談は拾わない")
eq(D.choose(True, None, 0.0, 3, "バイト中", 0.9), "short", "バイト中に呼ばれたら、遅れて一言")
eq(D.choose(False, 0.99, 0.0, 3, "バイト中", 0.0), "none", "バイト中の雑談は拾わない")
eq(600 <= D.notice_sec("バイト中", 0.5) <= 2400, True, "バイト中は10〜40分後に見る")
eq(D.choose(True, None, 0.0, 3, "講義中", 0.9), "short", "講義中は一言")
eq(D.choose(False, 0.99, 0.0, 3, "講義中", 0.5), "none", "講義中の雑談はほぼ拾わない")
eq(D.choose(True, None, 0.9, 3, "ふつう", 0.9), "normal", "話しかけられたらシェアに関係なく返す")
eq(D.choose(False, 0.99, 0.6, 2, "ふつう", 0.0), "none", "喋りすぎなら黙る（2人なら1/2超え）")
eq(D.choose(False, 0.99, 0.25, 4, "ふつう", 0.0), "normal", "5人なら3割までは喋る")
eq(D.choose(False, 0.99, 0.1, 2, "ふつう", 0.1), "normal", "食いつく話題は返す")
eq(D.choose(False, 0.99, 0.1, 2, "ふつう", 0.6), "reaction", "食いつく話題でもリアクションだけの時がある")
eq(D.choose(False, None, 0.1, 2, "ふつう", 0.1), "reaction", "棄権は食いつかない扱い")
eq(D.choose(False, 0.5, 0.1, 2, "ふつう", 0.5), "none", "関心が薄ければ大半は黙る")

# 気付くまで・打つ速さ
eq(D.notice_sec("ふつう", 0.0), 5.0, "最短5秒")
eq(round(D.notice_sec("ふつう", 0.69)), 40, "7割は40秒以内")
eq(D.notice_sec("ふつう", 0.80) > 60, True, "たまに数分")
eq(D.notice_sec("講義中", 0.0), 60.0, "講義中は1分以上")
eq(D.notice_sec("講義中", 1.0), 360.0, "講義中でも6分以内")
eq(D.typing_sec("あ" * 20, "スマホ") > D.typing_sec("あ" * 20, "PC"), True, "スマホは遅い")
eq(D.typing_sec("あ" * 100), 6.0, "PCの待ちは6秒まで")

# 後で返す分
d = [{"at": TUE.replace(hour=19).isoformat(), "id": 1}, {"at": TUE.replace(hour=20).isoformat(), "id": 2}]
eq(D.defer_due(d, TUE.replace(hour=21), "バイト中"), (d, None), "バイト中はまだ返さない")
eq(D.defer_due(d, TUE.replace(hour=23, minute=59) + timedelta(minutes=5), "深夜"), ([], d[1]), "終わったら最新の1件に返す")
eq(D.defer_due(d, TUE + timedelta(days=1, hours=10), "ふつう"), ([], None), "12時間過ぎたら捨てる")

# 体の変数がある時
b = {"asleep": True}
eq(st_b := D.activity(TUE.replace(hour=14), b)["state"], "寝ている", "体が寝ていれば昼でも寝ている（講義を寝過ごす）")
b = {"asleep": False, "away_until": TUE.replace(hour=20, minute=10).isoformat(), "away_why": "コンビニ"}
eq(D.activity(MON.replace(hour=20), {**b, "away_until": MON.replace(hour=20, minute=10).isoformat()})["state"], "離席中", "コンビニ中は離席")
eq(D.activity(MON.replace(hour=3), {"asleep": False})["state"], "深夜", "体が起きていれば3時でも深夜")
eq(D.activity(MON.replace(hour=13, minute=30), {"asleep": False, "woke_at": MON.replace(hour=13).isoformat()})["state"], "講義中", "講義は起きたてより先")
eq(D.activity(SUN.replace(hour=13, minute=30), {"asleep": False, "woke_at": SUN.replace(hour=13).isoformat()})["state"], "起きたて", "起きて90分は起きたて")
eq(D.choose(True, None, 0.0, 3, "離席中", 0.0), "defer", "離席中に呼ばれたら後で返す")
eq(D.choose(True, None, 0.0, 3, "ふつう", 0.9, energy=20), "short", "気力が低いと呼ばれても一言")
eq(D.choose(False, 0.99, 0.0, 3, "ふつう", 0.1, energy=20), "reaction", "気力が低いと雑談はリアクションだけ")
eq(D.choose(False, 0.99, 0.0, 3, "ふつう", 0.5, energy=20), "none", "気力が低いと大半は黙る")
eq(D.schedule(TUE.replace(hour=20)), "バイト中", "予定だけの判定")
eq(D.alarm(MON)[0], True, "月曜は目覚まし")
eq(D.alarm(TUE)[0], False, "火曜は目覚ましなし")

# 遅れて返す時の補足
eq("触れず" in D.late_note(5, "離席中", "コンビニ"), True, "短い遅れは断らない")
eq("コンビニ行ってた" in D.late_note(25, "離席中", "コンビニ"), True, "離席は理由を言う")
eq("バイト中だった" in D.late_note(120, "バイト中"), True, "バイトは状態から")
eq("ちょっと離れてた" in D.late_note(30, "離席中"), True, "理由が無ければぼかす")

# 放置された発言を拾う
P0 = TUE.replace(hour=15)
lone = [{"author": "A", "at": (P0 - timedelta(minutes=5)).isoformat()}]
eq(D.pickup(lone, me, P0, "ふつう", 70), lone[0], "5分放置された発言は拾う")
eq(D.pickup(lone, me, P0 - timedelta(minutes=4), "ふつう", 70), None, "1分ではまだ拾わない")
eq(D.pickup(lone, me, P0 + timedelta(hours=1), "ふつう", 70), None, "古すぎる発言は拾わない")
eq(D.pickup(lone, me, P0, "講義中", 70), None, "講義中は拾いに行かない")
eq(D.pickup(lone, me, P0, "ふつう", 10), None, "気力が無いと拾わない")
eq(D.pickup(lone + [{"author": me, "at": P0.isoformat()}], me, P0, "ふつう", 70), None, "自分が最後なら拾わない")

# 自分から話題を出す
S0 = TUE.replace(hour=15)
quiet, alive = S0 - timedelta(hours=2), S0 - timedelta(hours=2)
eq(D.start_due(S0, quiet, alive, "ふつう", 70, 0, 0.0), True, "2時間静かなら出しうる")
eq(D.start_due(S0, S0 - timedelta(minutes=30), alive, "ふつう", 70, 0, 0.0), False, "30分前に誰か話していたら出さない")
eq(D.start_due(S0, quiet, S0 - timedelta(days=2), "ふつう", 70, 0, 0.0), False, "人が来ていない部屋には出さない")
eq(D.start_due(S0, quiet, alive, "ふつう", 70, 2, 0.0), False, "1日2回まで")
eq(D.start_due(S0, quiet, alive, "講義中", 70, 0, 0.0), False, "講義中は出さない")
eq(D.start_due(S0, quiet, alive, "ふつう", 20, 0, 0.0), False, "気力が無いと出さない")
eq(D.start_due(TUE.replace(hour=5), quiet, alive, "深夜", 70, 0, 0.0), False, "明け方は出さない")
eq(D.start_due(S0, quiet, alive, "ふつう", 70, 0, 0.9), False, "毎分の抽選に外れたら出さない")

# 経過時間
eq(D.ago(30), "今", "1分未満は今")
eq(D.ago(5 * 60 + 59), "5分前", "分は切り捨て")
eq(D.ago(3 * 3600 + 120), "3時間前", "1時間以上は時間")
eq(D.ago(50 * 3600), "2日前", "1日以上は日")
eq([D.rough_ago(s * 60) for s in (5, 44, 70, 200, 1500)], ["さっき", "30分くらい前", "1時間くらい前", "3時間くらい前", "昨日"],
   "出来事の経過はざっくり")

# 誰かに向けた発言は拾わない
PK = MON.replace(hour=15)
msg = {"author": 2, "bot": False, "at": (PK - timedelta(minutes=5)).isoformat()}
eq(D.pickup([msg], 1, PK, "ふつう", 70) is not None, True, "放置された発言は拾う")
eq(D.pickup([{**msg, "to_other": True}], 1, PK, "ふつう", 70), None, "誰かへの返信・メンションは拾わない")

# 居心地の予想で選んでよいか
eq(D.free_choice(False, 0.1, 3, "ふつう", 70), True, "話しかけられていない・空いている時だけ")
eq(D.free_choice(True, 0.1, 3, "ふつう", 70), False, "話しかけられたら決まりごと")
eq(D.free_choice(False, 0.1, 3, "講義中", 70), False, "講義中は決まりごと")
eq(D.free_choice(False, 0.1, 3, "ふつう", 20), False, "気力切れは決まりごと")
eq(D.free_choice(False, 0.5, 3, "ふつう", 70), False, "喋りすぎは黙る")

# 今と1週間（LLMに渡す）
eq(D.now_text(datetime(2026, 11, 1, 12, 58)), "11月1日（日）の昼 12:58", "時間帯の言葉つき")
eq(D.now_text(datetime(2026, 11, 3, 1, 20)), "11月3日（火）の深夜 01:20（月曜の夜の続き）", "4時までは前日の夜の続き")
eq(D.week_text(datetime(2026, 11, 1, 12, 0)).split("／")[:2],
   ["日: 授業もバイトも無い", "月: 09:00〜10:40 語学（再履修）、13:00〜14:40 講義"], "今日から7日分")
eq(D.items(datetime(2026, 11, 3)), [(datetime(2026, 11, 3, 10, 50), datetime(2026, 11, 3, 12, 30), "講義"),
   (datetime(2026, 11, 3, 18, 0), datetime(2026, 11, 4), "居酒屋のバイト")], "24:00 は翌日の0時")

# 日付と予定（LLMに渡す）
eq(D.day_text(datetime(2026, 10, 10, 11, 35)),
   "10月10日（土）。今日の予定: 18:00〜24:00 居酒屋のバイト／明日（日）: 授業もバイトも無い", "土曜は授業なし")
eq(D.day_text(datetime(2026, 10, 11, 1, 22)),
   "10月10日（土）の深夜。今日の予定: 18:00〜24:00 居酒屋のバイト／明日（日）: 授業もバイトも無い", "4時までは前日の夜")
eq(D.day_text(MON.replace(hour=14)).split("／")[0],
   "10月5日（月）。今日の予定: 09:00〜10:40 語学（再履修）、13:00〜14:40 講義", "月曜は1限あり")

print(f"ok {n}")
