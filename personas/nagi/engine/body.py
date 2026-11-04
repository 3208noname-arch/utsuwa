# -*- coding: utf-8 -*-
"""体の変数（純関数）。**状態は時刻で切り替わるのではなく、少しずつ動いて振る舞いに効き続ける。**

参照: Man & Damasio (2019)「感情は、体を生きられる範囲に保つ恒常性の心的な現れ」。
眠気は睡眠の2プロセスモデル（起きている間に溜まり、寝ると抜ける）に寄せた。

変数（0〜100）: sleepy 眠気／hunger 空腹／energy 気力。money は円。
戻す行動（寝る・食べる・落ちる）は、ここの規則でナギ自身が選ぶ。
"""
from datetime import datetime, timedelta

# --- 増減の速さ（1時間あたり） ---------------------------------------------------
SLEEPY_AWAKE = 4.5         # 10時ごろ起きて、夜中2時ごろに80に届く
SLEEPY_BAITO = 5.0         # 体を使うと速く溜まる（バイトの日は1時半ごろ寝る）
SLEEPY_ASLEEP = -9.0       # 80で寝ると、8時間弱で抜ける
HUNGER_AWAKE = 8.0
HUNGER_NIGHT = 12.0        # 深夜に眠いと夜食が欲しくなる
HUNGER_ASLEEP = 3.0
ENERGY_IDLE = 8.0          # 一人でいると戻る（4だと夕方に尽きて抜けてばかりだった）
ENERGY_ASLEEP = 10.0
ENERGY_LECTURE = -10.0
ENERGY_BAITO = -6.0
TALK_BASE, TALK_PER_CHARS = 1.0, 40   # 1回送るごとの気力（2＋文字数÷30だと、盛り上がった夕方に尽きた）

# --- しきい値 ---------------------------------------------------------------
SLEEP_AT = 80              # 夜（1〜5時）にこれを超えたら寝る
SLEEP_AT_EARLY = 92        # 23〜1時はラジオやスマホで粘るので、ここまで寝ない（夜型）
CRASH_AT = 100             # どこでも寝落ちる（講義・バイト中は除く）
WAKE_BY_SELF = 10          # 目覚ましの無い日は、ここまで抜けたら起きる
WAKE_EARLIEST = 9.5        # ただし目覚ましが無ければ9時半までは寝ている（寝坊気質）
WAKE_LATEST = 12           # それでも12時には起きる
HANGRY = 70                # 空腹がこれ以上だと、気力が戻りにくく、とげが出やすい
SNACK_AT = 70              # これ以上なら（金と気力があれば）コンビニに行く
LUNCH_FROM = 40
LOW_ENERGY = 8             # これを割ったら「ちょっと落ちる」
LAZY_ENERGY = 15           # これを割ると、コンビニに行くのも面倒
DROP_GAP = timedelta(hours=3)   # 落ちたら、次に落ちるまでこれだけ空ける（低い間は口数を減らすだけ）
POST_MEAL_SLEEPY = 10      # 食後の眠気（1時間だけ。溜まる眠気には足さない）
POST_MEAL = timedelta(hours=1)
FUN_HOLD = timedelta(minutes=40)   # 楽しい話題が続くと、その間は眠気を感じにくい

LUNCH_YEN, SNACK_YEN = 500, 400
CHEAP_YEN = 100            # 家のカップ麺。予算を超えそうな日はこれで済ませる
BROKE = 2000               # これを割ると「金欠」（体の感じ・気分に出る）
# お金の入り方（10-17 見直し）。最初は給料85,000円・仕送り無しで、自由に使えるのが月15,000円しか無く、
# 毎日コンビニに2回行って半月で100円になった（友人「相当ブラック」）。
# 火木金土18〜24時・時給1,100円で月約11万円＋仕送り3万円。家賃などを引いて月約7万円（1日約2,300円）
PAYDAY, PAY = 25, 110000   # 給料日
SENDDAY, SEND = 15, 30000  # 仕送り
BILLDAY, BILLS = 27, 70000 # 家賃・光熱費・スマホ代
TREATS_PER_DAY = 2         # 奢ってもらえるのは1日これまで（ずるにならないように）


def new(t, money=6800):
    return {"sleepy": 30.0, "hunger": 30.0, "energy": 70.0, "money": money,
            "asleep": False, "away_until": None, "away_why": None,
            "fun_until": None, "paid": None, "sent": None, "billed": None, "at": t.isoformat()}


def _clip(b):
    for k in ("sleepy", "hunger", "energy"):
        b[k] = max(0.0, min(100.0, b[k]))


def _day(t):
    return (t - timedelta(hours=4)).date().isoformat()   # 1日の区切りは朝4時（decide.day_key と同じ）


def _spend(b, yen, t):
    b["money"] -= yen
    sp = b.get("spent") or {}
    if sp.get("day") != _day(t):
        sp = {"day": _day(t), "yen": 0}
    sp["yen"] += yen
    b["spent"] = sp


def spent_today(b, t):
    sp = b.get("spent") or {}
    return sp.get("yen", 0) if sp.get("day") == _day(t) else 0


def allowance(b, t):
    """今日使っていい額。朝の残高（今の残高＋今日使った分）を、次の給料日までの日数で割る
    （給料日の後は、まだ払っていない家賃などを除く）。"""
    if t.day >= PAYDAY:
        nxt = (t.replace(day=1) + timedelta(days=32)).replace(day=PAYDAY)
    else:
        nxt = t.replace(day=PAYDAY)
    days = max(1, (nxt.date() - t.date()).days)
    reserve = BILLS if t.day >= PAYDAY and b.get("billed") != t.strftime("%Y-%m") else 0
    return max(0, b["money"] + spent_today(b, t) - reserve) / days


def can_spend(b, yen, t):
    return b["money"] >= yen and spent_today(b, t) + yen <= allowance(b, t)


def _eat(b, yen, t):
    b["hunger"] = 0.0
    _spend(b, yen, t)
    b["ate_at"] = t.isoformat()


def felt_sleepy(b, t):
    """感じている眠気。**楽しい話題の最中は抑えられ、終わるとまとめて来る**（値そのものは溜まり続ける）。"""
    s = b["sleepy"]
    if b.get("ate_at") and t - datetime.fromisoformat(b["ate_at"]) < POST_MEAL:
        s += POST_MEAL_SLEEPY
    if b.get("fun_until") and t < datetime.fromisoformat(b["fun_until"]):
        s *= 0.6
    return min(100.0, s)


def tick(b, t, state, has_alarm, alarm):
    """時間を進めて、戻す行動を選ぶ。起きた出来事のリストを返す（["寝た", "起きた", ...]）。

    state: 講義中／バイト中／その他（decide.activity の予定だけの判定）
    has_alarm: 今日は1限があって目覚ましをかけている。alarm はその時刻
    """
    ev = []
    last = datetime.fromisoformat(b["at"])
    h = max(0.0, (t - last).total_seconds() / 3600)
    b["at"] = t.isoformat()

    # お金（月の決まった日）
    ym = t.strftime("%Y-%m")
    if t.day >= SENDDAY and b.get("sent") != ym:
        b["money"] += SEND
        b["sent"] = ym
        ev.append("仕送りが届いた")
    if t.day >= PAYDAY and b.get("paid") != ym:
        b["money"] += PAY
        b["paid"] = ym
        ev.append("給料が入った")
    if t.day >= BILLDAY and b.get("billed") != ym:
        b["money"] -= BILLS
        b["billed"] = ym
        ev.append("家賃が引き落とされた")

    if b["asleep"]:
        b["sleepy"] += SLEEPY_ASLEEP * h
        b["hunger"] += HUNGER_ASLEEP * h
        b["energy"] += ENERGY_ASLEEP * h
        _clip(b)
        # 目覚ましは鳴った時刻から3時間だけ効く（夜に「今日の8時」を過ぎているからと起こさない）
        hm = t.hour + t.minute / 60
        self_wake = b["sleepy"] <= WAKE_BY_SELF and WAKE_EARLIEST <= hm < 18
        if (has_alarm and alarm <= t < alarm + timedelta(hours=3)) or self_wake or WAKE_LATEST <= t.hour < 18:
            b["asleep"] = False
            b["woke_at"] = t.isoformat()
            ev.append("起きた")
        return ev

    night = t.hour >= 23 or t.hour < 5
    b["sleepy"] += (SLEEPY_BAITO if state == "バイト中" else SLEEPY_AWAKE) * h
    b["hunger"] += (HUNGER_NIGHT if night and b["sleepy"] > 60 else HUNGER_AWAKE) * h
    rate = {"講義中": ENERGY_LECTURE, "バイト中": ENERGY_BAITO}.get(state, ENERGY_IDLE)
    if rate > 0 and b["hunger"] >= HANGRY:
        rate /= 2                       # 腹が減っていると気力が戻りにくい
    b["energy"] += rate * h
    _clip(b)

    # 離席中なら、戻るまで何もしない
    if b.get("away_until"):
        if t < datetime.fromisoformat(b["away_until"]):
            return ev
        b["away_until"], b["away_why"] = None, None
        ev.append("戻った")

    busy = state in ("講義中", "バイト中")
    # 寝る
    s = felt_sleepy(b, t)
    limit = SLEEP_AT_EARLY if t.hour >= 23 or t.hour < 1 else SLEEP_AT
    if not busy and ((night and s >= limit) or s >= CRASH_AT):
        b["asleep"] = True
        ev.append("寝た")
        return ev
    if busy:
        return ev
    # 食べる（気力が低いと面倒で放っておく）。今日の予算を超えそうなら、家のカップ麺で済ませる
    lunch = 12 <= t.hour < 14 and b["hunger"] >= LUNCH_FROM
    if (lunch or b["hunger"] >= SNACK_AT) and b["energy"] >= LAZY_ENERGY or lunch:
        if lunch and can_spend(b, LUNCH_YEN, t):
            _eat(b, LUNCH_YEN, t)
            ev.append("昼を食べた")
        elif not lunch and can_spend(b, SNACK_YEN, t):
            _eat(b, SNACK_YEN, t)
            b["away_until"], b["away_why"] = (t + timedelta(minutes=15)).isoformat(), "コンビニ"
            ev.append("コンビニに行く")
            return ev
        elif b["money"] >= CHEAP_YEN:
            _eat(b, CHEAP_YEN, t)
            ev.append("節約して家でカップ麺を食べた")
    # 気力が尽きたら落ちる
    dropped = b.get("dropped_at") and t - datetime.fromisoformat(b["dropped_at"]) < DROP_GAP
    if b["energy"] < LOW_ENERGY and not dropped:
        b["away_until"], b["away_why"] = (t + timedelta(minutes=45)).isoformat(), "一人になりたい"
        b["dropped_at"] = t.isoformat()
        b["energy"] += 15
        ev.append("落ちる")
    return ev


def after_baito(b, t):
    """バイト上がり。まかないで空腹が戻る。"""
    _eat(b, 0, t)
    return ["まかないを食べた"]


DID_MAX_YEN = 3000   # 会話の中で言った出費は、1回これまで


def did(b, t, what, yen=0, ate=False, treat=False):
    """会話の中で「今やった」と言った行動を、本当にやったことにする（10-11 ユーザー「話題を出すにも行動は要る」）。
    寝ている間は何もしない。出費は残高と DID_MAX_YEN で切る。出来事の文を返す（やらなかったら None）。
    treat は人に奢ってもらった（0円。1日 TREATS_PER_DAY 回まで。友人の案「奢られるシステムが無い」10-14）。"""
    if b["asleep"] or not what:
        return None
    if treat:
        tr = b.get("treats") or {}
        if tr.get("day") != _day(t):
            tr = {"day": _day(t), "n": 0}
        if tr["n"] >= TREATS_PER_DAY:
            return None
        tr["n"] += 1
        b["treats"] = tr
        yen = 0
    yen = max(0, min(int(yen or 0), DID_MAX_YEN, b["money"]))
    if ate:
        _eat(b, yen, t)
    else:
        _spend(b, yen, t)
    return f"{what}（{yen:,}円）" if yen else what


def spend_talk(b, text_len, t, cold=False, hot=False, praised=False):
    """1回送ると気力が減る。眠いと減りが速い。冷たくされると減り、楽しい話題と褒めで少し戻る。"""
    cost = TALK_BASE + text_len / TALK_PER_CHARS
    if felt_sleepy(b, t) > 70:
        cost *= 2
    b["energy"] -= cost
    if cold:
        b["energy"] -= 8
    if hot:
        b["energy"] += 2
        b["fun_until"] = (t + FUN_HOLD).isoformat()
    if praised:
        b["energy"] += 3
    _clip(b)


def edgy(b):
    """とげが出やすい（腹が減っている／眠い）。返事の型・LLMへの渡し方に使う。"""
    return b["hunger"] >= HANGRY or b["sleepy"] >= 75


# --- LLMへの渡し方（比較用に2通り） --------------------------------------------
def as_numbers(b, t):
    return (f"眠気{round(felt_sleepy(b, t))}/100 空腹{round(b['hunger'])}/100 "
            f"気力{round(b['energy'])}/100 残高{b['money']:,}円")


def wallet(b, t):
    """LLMに渡す財布の中身。**金欠の時だけ渡していたら、10万円あっても「4桁しかねぇ」と言った**（10-27）。"""
    if b["money"] < BROKE:
        return f"金欠（残り{b['money']:,}円）"
    left = max(0, int(allowance(b, t) - spent_today(b, t)))
    return f"残高 約{round(b['money'], -3):,}円（今日あと{round(left, -2):,}円くらい使ってよい）"


def as_feelings(b, t):
    """一番強い感覚を2つまで、体の感じとして。"""
    s = felt_sleepy(b, t)
    cands = []
    if s >= 85:
        cands.append((s, "まぶたが落ちそう"))
    elif s >= 65:
        cands.append((s, "けっこう眠い"))
    if b["hunger"] >= 80:
        cands.append((b["hunger"], "腹が減って力が出ない"))
    elif b["hunger"] >= 60:
        cands.append((b["hunger"], "小腹が空いてる"))
    if b["energy"] <= 25:
        cands.append((100 - b["energy"], "人と話すのがちょっとしんどい"))
    if b["money"] < BROKE:
        cands.append((60, "金欠でちょっと焦ってる"))
    cands.sort(reverse=True)
    return "、".join(c[1] for c in cands[:2]) or "特に無し（ふつう）"


NUMBERS_GUIDE = """
# 体の状態の読み方（数値で渡す）
眠気・空腹・気力は0〜100、残高は円。**数値そのものや「眠気」「空腹」という言葉を口に出さない。**語調・長さ・話題の選び方ににじませるだけにする。
- 眠気 0〜40 ふつう／40〜65 少し眠い／65〜85 眠い（返事が短くなる）／85〜 落ちる寸前（誤字や単語だけの返事）
- 空腹 0〜40 ふつう／60〜 何か食べたい（食べ物の話に食いつきやすい）／80〜 腹が減って機嫌が悪い
- 気力 70〜 元気／40〜70 ふつう／25〜40 面倒くさい（短く返す）／〜25 しんどい（ほぼ一言）
- 残高 2,000円未満 金欠（奢りや安い店の話に敏感）
"""
