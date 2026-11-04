"""②文脈を組む。記憶（`01 Lessons/`、`00 Diary/`）から、今の会話に関係する分だけを選ぶ。

1. 種類を絞る（Jev）：種類ごとに「この種類の記憶が要るか」を Noul で、1回の呼び出しで聞く
2. 絞った種類の表を、全行そのまま並べる（コード）。キーワードで候補を集めない
3. 行ごとに Jev が選ぶ：行ごとの Noul を1回で聞く
4. 選ばれた行を、種類に合った形で渡す（コード）。上限で切る

`出どころ`（会話／評価）は行に入れない。会話用 LLM に「評価」という語を見せないため。
"""
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta

from harness.api import noul

log = logging.getLogger("utsuwa")

KIND_MIN = 0.5        # 種類を絞る閾値（広めに通し、行の段で絞る）
ROW_MIN = 0.7         # 行を選ぶ閾値（RustXNG の検証で、正解を落とさなかった値）
MAX_ROWS = 6
MAX_CHARS = 600
DIARY_DAYS = 7
DIARY = "日記"


@dataclass
class Row:
    rel: str
    kind: str
    name: str
    date: str
    people: list = field(default_factory=list)
    words: list = field(default_factory=list)
    scene: str = ""
    body: str = ""

    def line(self):
        s = f"{self.name}｜種類: {self.kind}"
        if self.words:
            s += "｜思い出す言葉: " + "、".join(self.words)
        if self.people:
            s += "｜教わった人: " + "、".join(self.people)
        if self.scene:
            s += "｜場面: " + self.scene
        return s + f"｜{self.date}"


def _list(v):
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v]) if str(x).strip()]


def lesson_rows(vault):
    """現役の記憶の行。date が空のノートは雛形なので入れない。"""
    rows = []
    for rel, n in vault.notes("01 Lessons"):
        m = n.meta
        if not m.get("date") or str(m.get("status", "active")) != "active" or not m.get("種類"):
            continue
        rows.append(Row(rel, str(m["種類"]), os.path.splitext(os.path.basename(rel))[0], str(m["date"]),
                        _list(m.get("教わった人")), _list(m.get("思い出す言葉")), str(m.get("場面") or ""),
                        n.body.strip()))
    return rows


def diary_rows(vault, today):
    rows = []
    for i in range(1, DIARY_DAYS + 1):
        d = today - timedelta(days=i)
        n = vault.note(f"00 Diary/{d.isoformat()}.md")
        if n and n.body.strip():
            first = n.body.strip().split("\n")[0]
            rows.append(Row(f"00 Diary/{d.isoformat()}.md", DIARY, f"{d.isoformat()}の日記（{first[:40]}）",
                            d.isoformat(), body=n.body.strip()))
    return rows


def render(rows):
    """選ばれた行を節にする。{節名: 文}。"""
    memo = [r for r in rows if r.kind == "スマホのメモ"]
    regret = [r for r in rows if r.kind == "反省"]
    diary = [r for r in rows if r.kind == DIARY]
    known = [r for r in rows if r not in memo + regret + diary]
    out = {}
    if known:
        out["覚えていること"] = "\n".join(
            f"- {r.body}" + (f"（{'、'.join(r.people)}に教わった）" if r.people and r.kind != "思い出"
                             else f"（{'、'.join(r.people)}との出来事）" if r.people else "")
            for r in known)
    if memo:
        # メモは貼らない。誰に何を教わったかだけを、話題に混ざるきっかけとして渡す（2026-11-04 ユーザー）
        out["スマホのメモ"] = "\n".join(f"- {'、'.join(r.people) or '誰か'}に「{r.name}」を教わった（メモがある）"
                                    for r in memo)
    if regret:
        out["前に反省したこと"] = "\n".join(f"- {r.body}" for r in regret)
    if diary:
        out["最近の日記"] = "\n".join(f"- {r.date}: {r.body}" for r in diary)
    return out


async def select(jev, vault, convo_text, about, today: date, kinds_about=None):
    """今の会話（convo_text）に関係する記憶を選び、(節, 選んだ行, ログ用の数) を返す。"""
    rows = lesson_rows(vault) + diary_rows(vault, today)
    if not rows:
        return {}, [], {"rows": 0}
    kinds = sorted({r.kind for r in rows})
    kinds_about = kinds_about or {}
    q1 = {f"k{i}": noul(f"{about}が、この会話の最後の発言に返事を書く時、「{k}」の記憶"
                        f"{'（' + kinds_about[k] + '）' if k in kinds_about else ''}を思い出す必要があるか。")
          for i, k in enumerate(kinds)}
    a1 = await jev.ask(convo_text, q1)
    wanted = {k for i, k in enumerate(kinds) if a1.get(f"k{i}", 0) >= KIND_MIN}
    cand = [r for r in rows if r.kind in wanted]
    if not cand:
        return {}, [], {"rows": len(rows), "kinds": sorted(wanted)}
    table = "\n".join(f"{i}. {r.line()}" for i, r in enumerate(cand))
    state = f"【会話】\n{convo_text}\n\n【記憶の表】\n{table}"
    q2 = {f"r{i}": noul(f"記憶の表の {i} 番は、{about}がこの会話の最後の発言に返事を書く時に関係するか。")
          for i in range(len(cand))}
    a2 = await jev.ask(state, q2)
    scored = sorted(((a2[f"r{i}"], r) for i, r in enumerate(cand) if a2.get(f"r{i}", 0) >= ROW_MIN),
                    key=lambda x: -x[0])
    picked, chars = [], 0
    for _, r in scored:
        if len(picked) >= MAX_ROWS or chars + len(r.body) > MAX_CHARS:
            break
        picked.append(r)
        chars += len(r.body)
    log.info("文脈: 記憶%d件 → 種類%s → 候補%d件 → %d件（%d字）", len(rows), sorted(wanted), len(cand), len(picked), chars)
    return render(picked), picked, {"rows": len(rows), "kinds": sorted(wanted), "candidates": len(cand),
                                    "picked": len(picked), "chars": chars}
