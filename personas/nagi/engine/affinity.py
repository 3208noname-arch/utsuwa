# -*- coding: utf-8 -*-
"""人ごとの好感度（純関数）。**今は数えるだけで、態度にはまだ効かせない。**

- 好感度は −100〜+100。最初は全員 +20（友人のサーバーなので）
- 何が起きたかは Jev が読み、増減はここの規則が決める。本気のけなしだけ下げ、いじりでは下げない
- ナギが好きな相手をけなした人も下げる（庇った人は上げる）
- 1人1日 ±15 まで。1日ごとに 0 との差を1割縮める（根に持ちすぎない）
- 好感度が動いた出来事を、時刻つきで人ごとに残す（根に持つ話を「さっき」「昨日」で区別して出すため）
- **判定は後回しにする**（10-11）。発言の後に AFTER_N 件続くか AFTER_WAIT 経ってから、前後の会話ごと読む。
  トオルの「いいバイトがあるんだけど紹介しよっか？」は、直後の1件だけ見て「褒めた」+6 になった。
  闇バイトの誘いの冗談だと分かるのは、その後の流れだった
- 冗談・ボケ（罠の前振りを含む）は +2（一緒にふざけられる相手。ユーザーが +2 を選んだ）
- 見下し・子ども扱いは −1（10-12 ユーザー「ガキ扱いはいじりというより貶し」。最初 −3 にしたが、仲間内のいじりで嫌いにはならないので抑えた。気分は −6 のまま）。ふざけた調子でも下げる。
  本気のけなし（−8）よりは軽い。トオルの「かわいい、おこちゃまだ」は いじり0.76・けなし0.31 で ±0 になっていた
"""
from datetime import datetime, timedelta

INITIAL = 20.0
DAILY_CAP = 15.0
DECAY = 0.9                # 1日ごとに 0 との差に掛ける
TRUE = 0.70                # Jev をこれ以上で真と読む
TEASE_MAX = 0.50           # いじり・冗談がこれ以上なら、けなしとも本心の褒めとも読まない
AFTER_N = 6                # 判定する発言の後、これだけ続いたら読む
AFTER_WAIT = timedelta(minutes=10)   # 続かなくても、これだけ経ったら読む
BEFORE_GAP = timedelta(minutes=30)   # 判定する発言よりこれ以上前の発言は、別の会話として文脈に入れない
CHAT_PLUS, CHAT_PER_DAY = 1.0, 3
KEEP = timedelta(days=14)
KEEP_N = 10
BANDS = [(50, "仲いい"), (15, "好き"), (-14, "ふつう"), (-49, "気まずい"), (-100, "苦手")]

# 起きたこと → 増減。「me」はナギ自身への言動、「friend」「close」はナギが好き／仲いい相手への言動
DELTA = {("attack", "me"): -8, ("support", "me"): 6, ("joke", "me"): 2, ("belittle", "me"): -1, ("treat", "me"): 3,
         ("belittle", "close"): -1,
         ("attack", "close"): -8, ("support", "close"): 4,
         ("attack", "friend"): -4, ("support", "friend"): 2}


def day_key(t):
    return (t - timedelta(hours=4)).strftime("%Y-%m-%d")   # 朝4時区切り（decide.day_key と同じ）


def get(store, uid, display, t):
    """その人の記録。無ければ初期値で作る。日が変わっていれば減衰を進め、その日の増減を戻す。"""
    p = store.setdefault(str(uid), {"v": INITIAL, "display": display, "day": day_key(t),
                                    "plus": 0.0, "minus": 0.0, "chats": 0, "events": []})
    p["display"] = display
    if p["day"] != day_key(t):
        days = (datetime.strptime(day_key(t), "%Y-%m-%d") - datetime.strptime(p["day"], "%Y-%m-%d")).days
        p["v"] = round(p["v"] * DECAY ** max(days, 0), 2)
        p.update({"day": day_key(t), "plus": 0.0, "minus": 0.0, "chats": 0})
    p["events"] = [e for e in p["events"] if t - datetime.fromisoformat(e["at"]) <= KEEP][-KEEP_N:]
    return p


def judge(attack, tease, support, joke=None, belittle=None):
    """Jev の確率から、起きたことを1つに決める。どれも当たらなければ None。失敗（None）は当たらない扱い。
    冗談なら、褒め言葉の形でも本心の褒めとは読まない（「偉すぎ。いいバイトがあるんだけど」は前振り）。
    見下し・子ども扱いは、いじりの調子でも見下しと読む（冗談・ボケと読めるなら冗談を採る）。"""
    playful = max(tease or 0, joke or 0) >= TEASE_MAX
    if joke is not None and joke >= TRUE:
        return "joke"
    if attack is not None and attack >= TRUE and not playful:
        return "attack"
    if belittle is not None and belittle >= TRUE:
        return "belittle"
    if support is not None and support >= TRUE and not playful:
        return "support"
    return None


def ripe(after_n, waited):
    """後回しにした発言を、もう読んでよいか。after_n はその後に続いた発言数、waited は経った時間。"""
    return after_n >= AFTER_N or waited >= AFTER_WAIT


def near(before, said):
    """直前の発言のうち、判定する発言と同じ会話と言えるものだけを残す（Jev には時刻が見えないので、ここで切る）。"""
    return [x for x in before if x.get("at") and said - datetime.fromisoformat(x["at"]) < BEFORE_GAP]


def relation(v):
    """ナギから見た相手の段階（第三者への言動を効かせるか）。"""
    return "close" if v >= 50 else "friend" if v >= 15 else None


def apply(p, delta, t, what, quote=""):
    """増減を1日の上限で切って足す。実際に動いた量を返し、動いたら出来事として残す。"""
    if delta > 0:
        delta = min(delta, DAILY_CAP - p["plus"])
        p["plus"] += max(delta, 0)
    else:
        delta = max(delta, -DAILY_CAP - p["minus"])
        p["minus"] += min(delta, 0)
    if delta:
        p["v"] = max(-100.0, min(100.0, p["v"] + delta))
        p["events"].append({"at": t.isoformat(timespec="seconds"), "what": what,
                            "delta": delta, "quote": quote[:40]})
        p["events"] = p["events"][-KEEP_N:]
        return delta
    return 0


def chat(p, t):
    """普通に話しかけてきた。1日 CHAT_PER_DAY 回まで少し上がる（出来事には残さない）。"""
    if p["chats"] >= CHAT_PER_DAY or p["plus"] >= DAILY_CAP:
        return 0
    p["chats"] += 1
    d = min(CHAT_PLUS, DAILY_CAP - p["plus"])
    p["plus"] += d
    p["v"] = min(100.0, p["v"] + d)
    return d


def band(v):
    return next(name for floor, name in BANDS if v >= floor)


def when(at, t):
    """出来事の時刻を、会話で使う言葉にする（「さっき」と「昨日」を区別する）。"""
    if t - at <= timedelta(minutes=30):
        return "さっき"
    days = (datetime.strptime(day_key(t), "%Y-%m-%d") - datetime.strptime(day_key(at), "%Y-%m-%d")).days
    h = at.hour
    part = "夜中" if h < 4 else "朝" if h < 11 else "昼" if h < 17 else "夜"
    if days == 0:
        return f"今日の{part}"
    if days == 1:
        return f"昨日の{part}"
    return f"{days}日前"


def format_summary(store):
    if not store:
        return "好感度: まだ無し"
    rows = sorted(store.values(), key=lambda p: -p["v"])
    return "\n".join(f"{p['display']} {p['v']:+.0f}（{band(p['v'])}）" for p in rows)
