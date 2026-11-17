"""近況 `02 State/近況.md`。生の日記・出来事から夜に蒸留した「今の全体像」の小さな要約。

- 節は 体と生活／進行中のこと／関係の変化／最近の流れ。全体で `MAX_BYTES` バイト（UTF-8）を上限にする
- 想起（`recall`）はコードで決まる固定枠。Jev の選択を介さず、毎回の返事に節「近況」として渡す。
  先頭に更新日（と何日前か）を付け、古さが書き手に分かるようにする
- 夜の更新（`update`）は night.py から呼ぶ。書き手は記録用 LLM（`Agents/RECORDER.md` の「近況」）。
  材料は直近 `DAYS` 日の日記、その日の出来事、新着の記憶、更新された人物ページの一行要約。
  書く前に `check` で検査し（長すぎる案は保存しない）、write が False の間は生成案をログに出すだけ
"""
import logging
from datetime import date, timedelta

from harness.api import loads
from harness import people
from harness.context import lesson_rows
from harness.vault import join

log = logging.getLogger("utsuwa")

REL = "02 State/近況.md"
SECTION = "近況"             # 会話用 LLM に渡す節の名前（AGENTS.md の「渡される節」に載せる）
KIND = "近況"
HEAD = ("体と生活", "進行中のこと", "関係の変化", "最近の流れ")
MAX_BYTES = 2500             # 本文（節の見出しを含む）の上限
DAYS = 3                     # 材料にする日数
DIARY_CHARS = 600            # 日記1日分の上限（材料として渡す字数）
EVENTS_MAX = 12              # 1日あたりの出来事の上限


def _day(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def render(d):
    return "\n\n".join(f"# {h}\n{d[h]}" for h in HEAD)


def check(d):
    """LLM の答えを検査して整える。(整えた dict, だめなら理由)。"""
    if not isinstance(d, dict):
        return None, "JSON でない"
    out = {h: str(d.get(h) or "").strip() for h in HEAD}
    if not any(out.values()):
        return None, "全部の節が空"
    n = len(render(out).encode("utf-8"))
    if n > MAX_BYTES:
        return None, f"長すぎる（{n}バイト > {MAX_BYTES}）"
    return out, None


def recall(vault, today):
    """会話用の節の本文。近況.md が無い・空・status が active でなければ None。"""
    n = vault.note(REL)
    if not n or not n.body.strip() or str(n.meta.get("status", "active")) != "active":
        return None
    upd = _day(n.meta.get("date"))
    if upd:
        gap = (today - upd).days
        head = f"（{upd.isoformat()} 更新" + ("、今日" if gap <= 0 else f"、{gap}日前") + "）"
    else:
        head = "（更新日は不明）"
    return head + "\n" + n.body.strip()


def material(agent, day):
    """直近 DAYS 日の材料を言葉にする。"""
    v = agent.vault
    days = [day - timedelta(days=i) for i in range(DAYS - 1, -1, -1)]
    since = days[0].isoformat()
    diary, events = [], []
    for d in days:
        n = v.note(f"00 Diary/{d.isoformat()}.md")
        if n and n.body.strip():
            diary.append(f"- {d.isoformat()}: {n.body.strip()[:DIARY_CHARS]}")
        for e in agent.h.data.get("events", {}).get(d.isoformat(), [])[-EVENTS_MAX:]:
            events.append(f"- {d.isoformat()} {e}")
    learned = [f"- {r.date} [{r.kind}] {r.body[:120]}" for r in lesson_rows(v) if r.date >= since]
    met = [f"- {e.name}（{e.summary}）" for e in people.index(v) if e.updated >= since]
    old = v.note(REL)
    return ("【種類】" + KIND + "\n【今の近況】\n" + (old.body.strip() if old and old.body.strip() else "（まだ無い）") +
            "\n【直近の日記】\n" + ("\n".join(diary) or "（無し）") +
            "\n【直近の出来事】\n" + ("\n".join(events) or "（無し）") +
            "\n【新しく覚えたこと】\n" + ("\n".join(learned) or "（無し）") +
            "\n【更新された人物ページ】\n" + ("\n".join(met) or "（無し）"))


async def propose(agent, day):
    """更新案。{"meta","body"} か None。保存はしない。"""
    user = material(agent, day)
    text, _ = await agent.llm.chat([{"role": "system", "content": agent.vault.text("Agents/RECORDER.md")},
                                 {"role": "user", "content": user}], temperature=0)
    d, why = check(loads(text))
    if why:
        log.info("近況を保存しない（%s）", why)
        return None
    return {"meta": {"date": day, "種類": KIND, "status": "active"}, "body": render(d)}


async def update(agent, day, write_=False):
    """近況を蒸留し直す。書いたか案だけなら [(rel, 書いたか)]、作れなければ []。write_ が False なら書かずにログに出す。"""
    p = await propose(agent, day)
    if not p:
        return []
    if write_:
        agent.vault.write_text(REL, join(p["meta"], p["body"]))
    else:
        log.info("近況の更新案（ログだけ）: %s\n%s", REL, p["body"])
    log.info("近況%s: %s", "を更新した" if write_ else "の案を作った", REL)
    return [(REL, write_)]
