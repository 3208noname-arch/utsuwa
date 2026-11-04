# -*- coding: utf-8 -*-
"""話題の好き嫌い（快・覚醒の座標）を、ナギが会話で口にした好き嫌いから育てる（純関数）。

- 新しい話題は「自分で覚えた」印（source: learned）を付けて足す。快は言った向き、覚醒は 0.5 から
- 既にある覚えた話題は、言うたびに快を少し寄せる（1回で全部は動かさない）
- **人が置いた話題（source が無い）は書き換えない**
- **覚えた話題は消さない**（10-12 ユーザー「消すわけにもいかない。参照しないだけで増えるだけ増えて、話題が出たら引っ張り出せばいい」）。
  使うのは active で選んだ分だけ: 人が置いた話題すべて＋会話に名前が出た覚えた話題＋最近言った覚えた話題 RECENT_N 件。
  残りは眠らせておき、Jevにも LLM にも渡さない（話題が増えるほどJevへの問いが増えるため）
- **経験で育てる**（2段目・10-13）: 話題が出てから EPISODE_MIN 分の間に、ナギが混ざったら覚醒を上げ、素通りしたら下げる。
  その間の人の反応で気分が上がれば快を上げ、下がれば下げる。
  人が置いた話題は、置いた場所（anchor）から ±ANCHOR_RANGE までしか動かさない（錨。ユーザーが ±0.2 を選んだ）。
  覚えた話題は −1〜1・0〜1 の中で自由に動く
"""

RECENT_N = 10
PULL = 0.3            # 既にある覚えた話題を、言った向きへ寄せる割合
NEW_AROUSAL = 0.5
SHOW_FROM = 0.2       # 快がこれ以上離れている話題だけ、LLMに「好き・嫌い」として渡す
EPISODE_MIN = 10
JOINED_AROUSAL, IGNORED_AROUSAL = 0.03, -0.01
FELT_FROM = 2.0       # 人の反応による気分の動きが、これ以上なら快を動かす
FELT_VALENCE = 0.03
ANCHOR_RANGE = 0.2


def clean(name):
    name = str(name or "").strip().strip("「」『』\"'")
    return name if 1 <= len(name) <= 20 else None


def learn(topics, name, like, at):
    """topics（topics.json の並び）を直して、何をしたか（"added"／"moved"／"kept"／None）を返す。"""
    name = clean(name)
    try:
        like = max(-1.0, min(1.0, float(like)))
    except (TypeError, ValueError):
        return None
    if not name:
        return None
    same = next((t for t in topics if t["name"].casefold() == name.casefold()), None)
    if same is not None:
        if same.get("source") != "learned":
            return "kept"
        same["valence"] = round(same["valence"] + (like - same["valence"]) * PULL, 2)
        same["at"] = at
        return "moved"
    topics.append({"name": name, "valence": round(like, 2), "arousal": NEW_AROUSAL,
                   "note": "会話で言った", "source": "learned", "at": at})
    return "added"


def mentioned(t, text):
    """話題の名前（「ピーマン・ナス」なら各部分）が会話に出ているか。"""
    text = text.casefold()
    return any(p and p.casefold() in text for p in t["name"].replace("／", "・").split("・"))


def active(topics, text):
    """今使う話題。人が置いた話題すべて＋会話に名前が出た覚えた話題＋最近言った覚えた話題。"""
    learned = [t for t in topics if t.get("source") == "learned"]
    recent = {id(t) for t in sorted(learned, key=lambda t: t.get("at", ""), reverse=True)[:RECENT_N]}
    return [t for t in topics if t.get("source") != "learned" or id(t) in recent or mentioned(t, text)]


def as_words(topics):
    """LLMに渡す自分の好き嫌い（言ったことと食い違わないように）。"""
    likes = [t["name"] for t in sorted(topics, key=lambda t: -t["valence"]) if t["valence"] >= SHOW_FROM]
    hates = [t["name"] for t in sorted(topics, key=lambda t: t["valence"]) if t["valence"] <= -SHOW_FROM]
    return f"好き: {'、'.join(likes) or '特に無し'}／嫌い: {'、'.join(hates) or '特に無し'}"


def experience(t, joined, felt):
    """話題が出た後の経験で、その話題の座標を動かす。joined はナギが混ざったか、felt はその間の人の反応による気分の動き。
    動いた量 (快, 覚醒) を返す。"""
    dv = FELT_VALENCE if felt >= FELT_FROM else -FELT_VALENCE if felt <= -FELT_FROM else 0.0
    da = JOINED_AROUSAL if joined else IGNORED_AROUSAL
    if t.get("source") == "learned":
        lo_v, hi_v, lo_a, hi_a = -1.0, 1.0, 0.0, 1.0
    else:
        a = t.setdefault("anchor", {"valence": t["valence"], "arousal": t["arousal"]})
        lo_v, hi_v = max(-1.0, a["valence"] - ANCHOR_RANGE), min(1.0, a["valence"] + ANCHOR_RANGE)
        lo_a, hi_a = max(0.0, a["arousal"] - ANCHOR_RANGE), min(1.0, a["arousal"] + ANCHOR_RANGE)
    v0, a0 = t["valence"], t["arousal"]
    t["valence"] = round(max(lo_v, min(hi_v, v0 + dv)), 3)
    t["arousal"] = round(max(lo_a, min(hi_a, a0 + da)), 3)
    return round(t["valence"] - v0, 3), round(t["arousal"] - a0, 3)
