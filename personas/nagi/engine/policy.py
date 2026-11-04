# -*- coding: utf-8 -*-
"""話しかけられていない時に混ざるかを、居心地の予想で選ぶ（純関数。強化学習の一番小さい形）。

- 候補は none（黙る）／reaction／short／normal。それぞれの「選びやすさ」（ロジット）を足し合わせ、ソフトマックスで選ぶ
  （毎回いちばん良いものを選ぶと機械的なので、ばらつかせる）
- 足すもの:
  1. BASE: 何も無い時の選びやすさ。それまでの手書きの確率表（黙る75%・リアクション15%・一言10%）に合わせた
  2. 話題の熱（覚醒）: 心が動く話題ほど混ざる。熱が最大で、それまでの「食いつく話題」の確率表（普通30%・一言25%・リアクション20%）
  3. 話題の快: 好きな話題ほど長く話し、嫌いな話題は一言・リアクションに寄る
  4. 今の気分: 機嫌が良いと混ざりやすく、悪いと黙りやすい
  5. 予想（Q）: その型で混ざった後10分の、人の反応による気分の動きの平均。混ざって笑われたり話しかけられたりすれば上がる
- Q は混ざるたびに、実際の気分の動きへ LR だけ寄せる
"""
import math

KINDS = ("none", "reaction", "short", "normal")
BASE = {"none": 0.0, "reaction": -1.6, "short": -2.0, "normal": -4.0}
HEAT = {"none": 0.0, "reaction": 1.4, "short": 2.0, "normal": 4.2}
SIZE = {"none": 0.0, "reaction": 0.3, "short": 0.6, "normal": 1.0}   # 口を開く量（快・気分の効き方）
HEAT_FROM, HEAT_FULL = 0.4, 0.8      # 覚醒がこの間で、熱が 0 → 1
VALENCE_W = 1.5
MOOD_W = 1 / 15                       # 気分15で、普通に返すロジットが+1
Q_W = 0.3                             # 予想1（気分+1）あたりのロジット
LR = 0.1
WINDOW_MIN = 10                       # 混ざった後、反応を待つ時間


def heat_of(arousal):
    return max(0.0, min(1.0, (arousal - HEAT_FROM) / (HEAT_FULL - HEAT_FROM)))


def logits(heat, valence, mood_v, q, grudge=0.0):
    """heat 0〜1（話題の覚醒から）、valence −1〜1（話題の快。話題が無ければ0）、mood_v 気分、q 型ごとの予想。
    grudge は発言者への怒りから来る罰（混ざる側のロジットから引く。engine/anger.POLICY_PER）。"""
    return {k: BASE[k] + HEAT[k] * heat + SIZE[k] * (VALENCE_W * valence + MOOD_W * mood_v) + Q_W * q.get(k, 0.0)
            - (grudge if k != "none" else 0.0) for k in KINDS}


def probs(lg):
    mx = max(lg.values())
    e = {k: math.exp(v - mx) for k, v in lg.items()}
    z = sum(e.values())
    return {k: v / z for k, v in e.items()}


def pick(lg, r):
    """r は 0〜1 の乱数（テストで固定する）。"""
    acc = 0.0
    p = probs(lg)
    for k in KINDS:
        acc += p[k]
        if r < acc:
            return k
    return KINDS[-1]


def update(q, kind, reward):
    """混ざった後の気分の動き（reward）へ、その型の予想を寄せる。"""
    old = q.get(kind, 0.0)
    q[kind] = round(old + LR * (reward - old), 3)
    return q[kind]
