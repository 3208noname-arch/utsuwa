# -*- coding: utf-8 -*-
"""居心地（快・不快の気分。純関数）。体の上に1層足す（Obsidian のナギのノート「居心地と好き嫌い」）。

- 値は −100〜+100。何も起きなければ、体で決まる基準線へ半減期 HALF_LIFE で戻る
- 基準線は体の外れ具合で下がる（眠い・腹が減った・気力が無い・金欠）。体が整っていれば 0
- 出来事が値を一時的に動かす: 人の言動（けなし・庇い・話しかけ）と、話題の好き嫌い（快）
- 話しかけられた時の上がり下がりは、その人への好感度で決まる（好きな人だと上がり、苦手な人だと下がる）
- LLMには数値でなく言葉で渡す。数値は評価との突き合わせ（snapshot の mood）に残す
"""
from datetime import datetime, timedelta

HALF_LIFE = timedelta(minutes=60)
KEEP = timedelta(hours=6)
KEEP_N = 8

# 体 → 基準線（しきい値を超えた分 × 係数）
SLEEPY_FROM, HUNGER_FROM, ENERGY_UNDER = 65, 60, 40
BODY_GAIN = 0.6
BROKE_PENALTY = 5

# 人 → 一時的な上下
PEOPLE = {("attack", "me"): -15, ("support", "me"): 8, ("joke", "me"): 3, ("belittle", "me"): -6, ("treat", "me"): 5,
          ("belittle", "close"): -4, ("belittle", "friend"): -2,
          ("attack", "close"): -8, ("support", "close"): 3,
          ("attack", "friend"): -4, ("support", "friend"): 2}
CHAT_PER_LIKE = 1 / 20          # 話しかけられた時: 好感度20で+1、60で+3、−40で−2
TOPIC_GAIN = 10                 # 話題: 快(−1〜+1) × 当てはまる確率 × これ
TOPIC_TRUE = 0.70               # これ未満の当てはまりは話題と読まない

BANDS = [(40, "機嫌がいい"), (15, "ちょっと機嫌いい"), (-14, "ふつう"), (-39, "ちょっと機嫌が悪い"), (-100, "かなり機嫌が悪い")]


def new(t):
    return {"v": 0.0, "at": t.isoformat(), "events": []}


def baseline(felt_sleepy, hunger, energy, broke=False):
    """体が整っていれば 0。外れた分だけ下がる。"""
    pen = (max(0, felt_sleepy - SLEEPY_FROM) + max(0, hunger - HUNGER_FROM) + max(0, ENERGY_UNDER - energy)) * BODY_GAIN
    return -(pen + (BROKE_PENALTY if broke else 0))


def settle(m, t, base):
    """経った時間の分だけ基準線へ戻す。古い出来事を捨てる。"""
    dt = max(0.0, (t - datetime.fromisoformat(m["at"])).total_seconds())
    m["v"] = base + (m["v"] - base) * 0.5 ** (dt / HALF_LIFE.total_seconds())
    m["at"] = t.isoformat()
    m["events"] = [e for e in m["events"] if t - datetime.fromisoformat(e["at"]) <= KEEP][-KEEP_N:]
    return m["v"]


def nudge(m, delta, t, why):
    """出来事で動かす。動いた量を返す。"""
    old = m["v"]
    m["v"] = max(-100.0, min(100.0, old + delta))
    d = m["v"] - old
    if abs(d) >= 0.5:
        m["events"].append({"at": t.isoformat(timespec="seconds"), "why": why, "delta": round(d, 1)})
        m["events"] = m["events"][-KEEP_N:]
    return d


def people_delta(kind, rel):
    """けなし・庇いの増減（affinity.judge と affinity.relation の値をそのまま受ける）。"""
    return PEOPLE.get((kind, rel), 0)


REASON = {"attack": "{who}にけなされた", "support": "{who}に庇われた", "joke": "{who}の冗談で笑った",
          "belittle": "{who}に見下された", "treat": "{who}に奢ってもらった"}


def people_reason(kind, who, by=None):
    """気分を動かした理由の文。by があれば第三者への言動（「ユウがトオルにけなされた」）。"""
    return (f"{who}が" + REASON[kind].format(who=by)) if by else REASON[kind].format(who=who)


def chat_delta(like):
    """普通に話しかけられた時。好感度で向きと大きさが決まる。"""
    return like * CHAT_PER_LIKE


def topic_hit(probs, topics):
    """当てはまる話題のうち、一番強く効くものを1つ。(名前, 増減) か None。
    probs は {話題名: 確率}、topics は topics.json の並び（name・valence）。"""
    best = None
    for tp in topics:
        p = probs.get(tp["name"])
        if p is None or p < TOPIC_TRUE:
            continue
        d = tp["valence"] * p * TOPIC_GAIN
        if best is None or abs(d) > abs(best[1]):
            best = (tp["name"], d)
    return best


HIST_EVERY_MIN = 10
HIST_KEEP = timedelta(hours=48)


def record(hist, t, v):
    """気分の推移を HIST_EVERY_MIN 分ごとに1点残す（Web UI のグラフ用）。"""
    if hist and t - datetime.fromisoformat(hist[-1][0]) < timedelta(minutes=HIST_EVERY_MIN):
        return hist
    hist.append([t.isoformat(timespec="minutes"), round(v, 1)])
    return [h for h in hist if t - datetime.fromisoformat(h[0]) <= HIST_KEEP]


def band(v):
    return next(name for floor, name in BANDS if v >= floor)


def as_words(m, base):
    """LLMに渡す気分。一番大きく動かした最近の出来事を理由に添える（体のせいなら体と書く）。"""
    v = m["v"]
    word = band(v)
    if word == "ふつう":
        return word
    same = [e for e in m["events"] if (e["delta"] > 0) == (v > 0)]
    if same:
        why = max(same, key=lambda e: abs(e["delta"]))["why"]
    elif v < 0 and base < -5:
        why = "体がしんどい"
    else:
        return word
    return f"{word}（{why}）"
