# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from personas.nagi.engine import body as B

T = datetime(2026, 10, 6, 20, 0)   # 火曜20時
n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


def at(b, t, state="ふつう", alarm=False):
    return B.tick(b, t, state, alarm, t.replace(hour=8, minute=0))


b = B.new(T); b["sleepy"] = 30
at(b, T + timedelta(hours=2))
ok(abs(b["sleepy"] - 39) < 0.01, "起きている間は毎時+4.5")
b = B.new(T); b["sleepy"] = 30
at(b, T + timedelta(hours=2), "バイト中")
ok(abs(b["sleepy"] - 40) < 0.01, "バイト中は毎時+5")

b = B.new(T.replace(hour=23, minute=29)); b["sleepy"] = 85
ok("寝た" not in at(b, T.replace(hour=23, minute=30)), "23時台は92まで粘る")
b = B.new(T); b["sleepy"] = 79; b["at"] = T.replace(hour=1).isoformat()
ev = at(b, T.replace(hour=1, minute=30))
ok("寝た" in ev and b["asleep"], "1時以降は80を超えたら寝る")
b = B.new(T); b["sleepy"] = 85
ok("寝た" not in at(b, T + timedelta(minutes=1), "バイト中"), "バイト中は寝ない")
b = B.new(T); b["sleepy"] = 85
ok("寝た" not in at(b, T.replace(hour=15)), "昼は100未満なら寝ない")

N = T.replace(hour=1); b = B.new(N); b["sleepy"] = 85; b["fun_until"] = (N + timedelta(minutes=10)).isoformat()
ok("寝た" not in at(b, N.replace(minute=5)), "楽しい間は夜でも寝ない")
ok("寝た" in at(b, N.replace(minute=20)), "楽しいのが終わるとまとめて来る")

# 起きる
b = B.new(T); b["asleep"] = True; b["sleepy"] = 60; b["at"] = T.replace(hour=3).isoformat()
ev = B.tick(b, T.replace(hour=8, minute=0), "ふつう", True, T.replace(hour=8, minute=0))
ok("起きた" in ev, "1限の日は目覚ましで起きる（眠いまま）")
b = B.new(T); b["asleep"] = True; b["sleepy"] = 90; b["at"] = T.replace(hour=3).isoformat()
ev = B.tick(b, T.replace(hour=9), "ふつう", False, None)
ok("起きた" not in ev, "目覚ましが無い日は眠気が抜けるまで寝る")
ev = B.tick(b, T.replace(hour=12), "ふつう", False, None)
ok("起きた" in ev, "それでも12時には起きる")

# 食べる
b = B.new(T, money=30000); b["hunger"] = 50
at(b, T.replace(hour=12, minute=30))
ok(b["hunger"] == 0 and b["money"] == 29500, "予算内なら昼を食べる")
b = B.new(T); b["hunger"] = 50
at(b, T.replace(hour=12, minute=30))
ok(b["hunger"] == 0 and b["money"] == 6700, "予算を超えるなら昼もカップ麺（6,800円÷19日）")
b = B.new(T.replace(hour=12)); b["hunger"] = 50; b["money"] = 50
at(b, T.replace(hour=12, minute=30))
ok(b["hunger"] > 50, "カップ麺も買えなければ昼を抜く")
b = B.new(T, money=50000); b["hunger"] = 75; b["energy"] = 50
ev = at(b, T + timedelta(minutes=1))
ok("コンビニに行く" in ev and b["away_why"] == "コンビニ", "腹が減ったらコンビニ")
ok(B.felt_sleepy(b, T + timedelta(minutes=2)) >= 30 + B.POST_MEAL_SLEEPY - 0.01 and b["sleepy"] < 31, "食後は1時間だけ眠い（溜まる眠気には足さない）")
b = B.new(T); b["hunger"] = 75; b["energy"] = 10
ev = at(b, T + timedelta(minutes=1))
ok("コンビニに行く" not in ev and "落ちる" not in ev, "気力が低いと食べに行くのも面倒。まだ落ちはしない")
b = B.new(T); b["energy"] = 5
ev = at(b, T + timedelta(minutes=1))
ok("落ちる" in ev, "気力が尽きたら落ちる")
b["away_until"] = None; b["energy"] = 5; b["at"] = (T + timedelta(minutes=59)).isoformat()
ev = at(b, T + timedelta(hours=1))
ok("落ちる" not in ev, "落ちてから3時間は、また落ちない")
b["energy"] = 5; b["at"] = (T + timedelta(hours=3)).isoformat()
ev = at(b, T + timedelta(hours=3, minutes=1))
ok("落ちる" in ev, "3時間過ぎたらまた落ちうる")
b2 = B.new(T)
ok(B.after_baito(b2, T) and b2["hunger"] == 0, "まかないで空腹が戻る")

# 相互作用
b = B.new(T); b["hunger"] = 80; b["energy"] = 50
at(b, T + timedelta(hours=1))
ok(abs(b["energy"] - (50 + B.ENERGY_IDLE / 2)) < 0.01, "腹が減っていると気力の戻りが半分")
b = B.new(T); b["sleepy"] = 70
b["at"] = T.replace(hour=1).isoformat(); at(b, T.replace(hour=1) + timedelta(hours=1))
ok(abs(b["hunger"] - 42) < 0.01, "深夜に眠いと腹が減る")
b = B.new(T); e0 = b["energy"]; B.spend_talk(b, 30, T)
b2 = B.new(T); b2["sleepy"] = 80; B.spend_talk(b2, 30, T)
ok(e0 - b2["energy"] == 2 * (e0 - b["energy"]), "眠いと話す気力の減りが倍")

# お金
b = B.new(T.replace(day=14), money=1000)
ev = at(b, T.replace(day=15, hour=20))
ok(b["money"] == 1000 + B.SEND and "仕送りが届いた" in ev, "仕送り")
at(b, T.replace(day=25, hour=20, minute=1))
ok(b["money"] == 1000 + B.SEND + B.PAY, "給料日")
at(b, T.replace(day=27, hour=20))
ok(b["money"] == 1000 + B.SEND + B.PAY - B.BILLS, "引き落とし")
at(b, T.replace(day=28, hour=20))
ok(b["money"] == 1000 + B.SEND + B.PAY - B.BILLS, "月に1回だけ")

# 予算
D17 = T.replace(day=17)
b = B.new(D17); b["money"] = 4000; b["paid"] = b["sent"] = b["billed"] = D17.strftime("%Y-%m")
ok(B.allowance(b, D17) == 500, "給料日まで8日なら、残高を8で割った額が1日の予算")
b2 = B.new(D17.replace(day=25)); b2["money"] = 100000; b2["paid"] = D17.strftime("%Y-%m")
ok(B.allowance(b2, D17.replace(day=25)) < 100000 / 30, "給料日の後は、まだ払っていない家賃を除いて割る")
b["hunger"] = 75; b["energy"] = 50; b["at"] = D17.replace(hour=15).isoformat()
ev = at(b, D17.replace(hour=15, minute=1))
ok("コンビニに行く" in ev and b["money"] == 3600 and B.allowance(b, D17) == 500, "予算内ならコンビニ。使っても今日の予算は減らない")
b["away_until"] = None; b["hunger"] = 75; b["at"] = D17.replace(hour=20).isoformat()
ev = at(b, D17.replace(hour=20, minute=1))
ok("節約して家でカップ麺を食べた" in ev and b["money"] == 3500 and b["hunger"] == 0, "予算を超えそうならカップ麺")
b = B.new(D17); b["money"] = 50; b["hunger"] = 75; b["energy"] = 50; b["paid"] = b["sent"] = D17.strftime("%Y-%m")
b["at"] = D17.replace(hour=15).isoformat()
ev = at(b, D17.replace(hour=15, minute=1))
ok(not ev and b["hunger"] > 75, "カップ麺も買えなければ食べられない")

# 財布
b = B.new(D17); b["money"] = 52700; b["paid"] = b["sent"] = b["billed"] = D17.strftime("%Y-%m")
ok(B.wallet(b, D17) == "残高 約53,000円（今日あと6,600円くらい使ってよい）", "残高はいつも丸めて渡す")
b["money"] = 1500
ok(B.wallet(b, D17) == "金欠（残り1,500円）", "金欠ならそう言う")

# 奢ってもらう
b = B.new(T); b["money"] = 300; b["hunger"] = 80
ok(B.did(b, T, "唐揚げ", 900, ate=True, treat=True) == "唐揚げ" and b["money"] == 300 and b["hunger"] == 0, "奢りは0円で食べる")
B.did(b, T, "ジュース", treat=True)
ok(B.did(b, T, "ケーキ", ate=True, treat=True) is None, "奢ってもらえるのは1日2回まで")

# 渡し方
b = B.new(T); b["sleepy"] = 90; b["hunger"] = 85
ok(B.as_feelings(b, T) == "まぶたが落ちそう、腹が減って力が出ない", "強い感覚を2つ")
ok(B.as_feelings(B.new(T), T) == "特に無し（ふつう）", "何も無ければふつう")
ok("眠気90/100" in B.as_numbers(b, T), "数値で渡す形")

b = B.new(T); b["asleep"] = True; b["sleepy"] = 99
ev = B.tick(b, T.replace(hour=22, minute=1), "ふつう", True, T.replace(hour=8))
ok("起きた" not in ev, "夜は目覚ましが効かない（その日の8時を過ぎていても）")

# 会話で言った行動
b = B.new(T); b["money"] = 4100
ok(B.did(b, T, "クレーンゲーム", 1000) == "クレーンゲーム（1,000円）" and b["money"] == 3100, "言った額が残高から減る")
b = B.new(T); b["money"] = 500; b["hunger"] = 70
ok(B.did(b, T, "ラーメン", 900, ate=True) == "ラーメン（500円）" and b["money"] == 0 and b["hunger"] == 0,
   "食べたら空腹が戻る。残高より多くは使えない")
b = B.new(T); b["money"] = 9000
B.did(b, T, "服", 8000)
ok(b["money"] == 6000, "1回の出費は上限で切る")
b = B.new(T); b["asleep"] = True
ok(B.did(b, T, "散歩") is None, "寝ている間は何もしない")
print(f"ok {n}")
