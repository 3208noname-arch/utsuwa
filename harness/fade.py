"""薄れ層：想起で選ばれた行を、writer に渡す前に「鮮明／曖昧／痕跡」に分ける。保存データは書き換えない。

- config.yaml の recall.mode が exact（既定）なら何もしない（現行と同じ文脈）。human の時だけ働く
- 強さ s = Jev の行確率（Row.p）× exp(-経過日数 / τ)。乱数は使わない（同じ入力なら同じ出力）
- 鮮明（s >= VIVID）: 本文を渡す／曖昧（BLUR <= s < VIVID）: 見出しと相対時間タグだけ（最大 MAX_BLUR 行）
- 痕跡（s < BLUR）: 落とす。ただし Jev の行確率が TIP_P 以上なら TIP_LINE を1行だけ残す
- 部品 recall.parts = {decay, blur, tip}。書いた部品はその値、書かなければ（human の間は）オン
  - decay を切ると経過日数を見ない（s = Jev の行確率）
  - blur を切ると、曖昧の行も鮮明と同じに本文で渡す
  - tip を切ると、痕跡の1行を残さない
- 対象外: 固定枠（約束・人物・近況）と体の状態。ここに来るのは select が選んだ記憶の行だけ。想起回数による強化はしない
"""
import math
from datetime import date

from harness import context

VIVID = 0.55
BLUR = 0.30
TIP_P = 0.85
MAX_BLUR = 2
TIP_LINE = "（何かあった気がする）"
SECTION = "うろ覚え（中身は思い出せない）"
PARTS = ("decay", "blur", "tip")
# 種類ごとの τ（日）。常識は長く残り、感情の思い出・メモ・その他は早く薄れる
TAU = {"常識": 365, "反省": 180, "思い出": 60, "スマホのメモ": 30, "その他": 30}
TAU_DEFAULT = 30


def settings(config):
    return (config or {}).get("recall") or {}


def enabled(config):
    return str(settings(config).get("mode", "exact")) == "human"


def part_on(config, name):
    return bool((settings(config).get("parts") or {}).get(name, True))


def tau(config, kind):
    mine = settings(config).get("tau") or {}
    return float(mine.get(kind, TAU.get(kind, TAU_DEFAULT)))


def age_days(row, today):
    try:
        return max(0, (today - date.fromisoformat(str(row.date)[:10])).days)
    except ValueError:
        return 0        # 日付が読めない行は新しい扱い（薄れさせない側に倒す）


def strength(row, config, today):
    s = row.p
    if part_on(config, "decay"):
        s *= math.exp(-age_days(row, today) / tau(config, row.kind))
    return s


def tag(days):
    """相対時間タグ。"""
    if days <= 0:
        return "今日"
    if days < 7:
        return "数日前"
    if days < 14:
        return "先週"
    if days < 60:
        return "先月ごろ"
    return "だいぶ前"


def heading(row):
    """本文を含まない見出し（相手・場面が分かる部分）。日記は名前に本文の頭が入っているので使わない。"""
    if row.kind == context.DIARY:
        return "日記"
    s = row.name
    if row.people:
        s += "（" + "、".join(row.people) + "）"
    if row.scene:
        s += "｜" + row.scene
    return s


def apply(mem, picked, config, today):
    """(節, 薄れた行, 内訳) を返す。mem は select が render した節。exact ならそのまま返す。
    薄れた行は本文を渡さなかった行（曖昧と痕跡）で、writer の数字チェックが使う。"""
    if not enabled(config):
        return mem, [], {}
    blur_on, tip_on = part_on(config, "blur"), part_on(config, "tip")
    vivid, blurred, dropped = [], [], []
    for r in picked:
        s = strength(r, config, today)
        if s >= VIVID or (s >= BLUR and not blur_on):
            vivid.append(r)
        elif s >= BLUR:
            blurred.append((s, r))
        else:
            dropped.append(r)
    blurred.sort(key=lambda x: -x[0])       # 同点は picked の順のまま（sort は安定）
    over = [r for _, r in blurred[MAX_BLUR:]]
    blurred = [r for _, r in blurred[:MAX_BLUR]]
    out = context.render(vivid)
    lines = [f"- [{tag(age_days(r, today))}] {heading(r)}" for r in blurred]
    tip = tip_on and any(r.p >= TIP_P for r in dropped)
    if tip:
        lines.append(f"- {TIP_LINE}")
    if lines:
        out[SECTION] = "\n".join(lines)
    info = {"vivid": [r.name for r in vivid], "blur": [r.name for r in blurred], "tip": tip,
            "dropped": [r.name for r in dropped + over]}
    return out, blurred + dropped + over, info
