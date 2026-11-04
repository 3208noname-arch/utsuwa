"""記録④ 夜の整理。エージェントが寝ている時間に1日1回動かす。

1. 似た記憶の組を集める（コード）：同じ種類で、思い出す言葉か本文の語が重なる現役の組
2. Jev が比べる：同じ事柄なら、新しい方を正にし、古い方を `status: inactive` にする（消さない）。
   片方が会話、片方が評価で教わったものなら、日付に関係なく会話の方を正にする
3. 「その他」を数える：溜まったら知らせる（新しい種類を作るかは人が決める）
4. 日記を書く：その日の出来事、覚えたこと、自分が言ったことだけを材料に、記録用 LLM が書く

config.yaml の `night.write` が false の間は、2で何を古いにするかをログに出すだけで書き換えない。
"""
import logging
from datetime import datetime, timedelta
from itertools import combinations

from harness.api import loads, noul
from harness.context import lesson_rows
from harness.saver import norm

log = logging.getLogger("utsuwa")

SAME_MIN = 0.8
WINDOW_DAYS = 365
OTHER_ALERT = 10
INACTIVE = "inactive"
PAIRS_MAX = 40       # 1晩に比べる組の上限（1回の Jev の呼び出しに収める）


def _grams(s):
    s = norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similar_pairs(rows, today):
    """同じ種類で、思い出す言葉が重なるか、本文の2文字の重なりが3割を超える組。"""
    since = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    rows = [r for r in rows if r.date >= since]
    out = []
    for a, b in combinations(rows, 2):
        if a.kind != b.kind:
            continue
        ga, gb = _grams(a.body), _grams(b.body)
        overlap = len(ga & gb) / max(1, min(len(ga), len(gb)))
        if set(a.words) & set(b.words) or overlap > 0.3:
            out.append((a, b))
    return out[:PAIRS_MAX]


def newer_wins(vault, a, b):
    """(残す方, 古くする方)。会話で教わった方を、評価で教わった方より優先する。"""
    sa, sb = vault.note(a.rel).meta.get("出どころ"), vault.note(b.rel).meta.get("出どころ")
    if sa == "会話" and sb == "評価":
        return a, b
    if sb == "会話" and sa == "評価":
        return b, a
    return (a, b) if a.date >= b.date else (b, a)


async def tidy(agent, today, write=False):
    """似た記憶を整理する。[(残した rel, 古くした rel)] を返す。write が False なら書き換えない。"""
    rows = lesson_rows(agent.vault)
    pairs = similar_pairs(rows, today)
    if not pairs:
        return []
    state = "\n".join(f"{i}. A「{a.body}」／B「{b.body}」" for i, (a, b) in enumerate(pairs))
    ans = await agent.jev.ask(state, {f"p{i}": noul(f"{i} 番の A と B は、同じ事柄について述べているか。")
                                      for i in range(len(pairs))})
    done, retired = [], set()
    for i, (a, b) in enumerate(pairs):
        if ans.get(f"p{i}", 0) < SAME_MIN or a.rel in retired or b.rel in retired:
            continue
        keep, old = newer_wins(agent.vault, a, b)
        done.append((keep.rel, old.rel))
        retired.add(old.rel)
        if write:
            agent.vault.set_meta(old.rel, "status", INACTIVE)
        log.info("夜の整理%s: %s を正にし、%s を %s にする", "" if write else "（ログだけ）", keep.rel, old.rel, INACTIVE)
    return done


def count_other(agent):
    return sum(1 for r in lesson_rows(agent.vault) if r.kind == "その他")


async def diary(agent, day, events, learned, mine):
    """その日の日記。材料にあることだけを書かせる。書けたら rel。"""
    rel = f"00 Diary/{day.isoformat()}.md"
    if agent.vault.exists(rel):
        return None
    user = ("【種類】日記\n【その日の出来事】\n" + ("\n".join(f"- {e}" for e in events) or "（無し）") +
            "\n【その日に覚えたこと】\n" + ("\n".join(f"- {x}" for x in learned) or "（無し）") +
            "\n【自分が言ったこと】\n" + ("\n".join(f"- {x}" for x in mine) or "（無し）"))
    text = await agent.llm.chat([{"role": "system", "content": agent.vault.text("Agents/RECORDER.md")},
                                 {"role": "user", "content": user}], temperature=0.7)
    body = str(loads(text).get("body") or "").strip()
    if not body:
        return None
    agent.vault.write_note(rel, {"date": day, "種類": "日記", "status": "active"}, body)
    log.info("日記を書いた: %s", rel)
    return rel


async def run(agent, t: datetime):
    """夜の整理を1回。"""
    cfg = agent.config.get("night") or {}
    day = (t - timedelta(days=1)).date() if t.hour < 12 else t.date()
    done = await tidy(agent, day, write=bool(cfg.get("write")))
    n_other = count_other(agent)
    if n_other > OTHER_ALERT:
        agent.notify(f"「その他」の記憶が{n_other}件になった。新しい種類を作るか見てほしい")
    events, learned, mine = agent.day_material(day)
    await diary(agent, day, events, learned, mine)
    return {"tidied": done, "other": n_other}
