"""約束・宣言の台帳 `02 State/約束.json`。自分が言った約束や宣言を状態つきで持ち、次の会話の前提にする。

- 1件は {id, 内容, 相手（表示名か空）, 種類（約束／宣言）, 期限（ISO 日付か空）, 状態（未完了／済み／流れた）, 作られた日}。ファイル全体は件の並び
- 記録（`add`）は人格が送った返事を Jev が読んだ後に呼ぶ。同じ内容・同じ相手の未完了は足さない
- 想起（`recall`）はコードで決まる固定枠。未完了の最大 `RECALL_MAX` 件を、返信先と一致する件、期限が近い順（期限なしは後で、新しい順）で節にする
- 夜の更新（`update`）は night.py から呼ぶ。期限を過ぎた未完了と、期限が無いまま作られてから `UNDATED_DAYS` 日を過ぎた未完了は、
  コードの判定だけで「流れた」にする（LLM に聞かない）。期限なしに寿命が無いと、古い宣言が固定枠を占め続ける。
  その日の会話と出来事で「済み」になった件は Jev が判定する。write が False の間は状態を変えず案をログに出すだけ
- 置き場は 02 State（許可リスト内）。読み書きは harness/store.py の State を使う
"""
import json
import logging
import os
from datetime import date, timedelta

from harness.api import noul
from harness.saver import norm

log = logging.getLogger("utsuwa")

NAME = "約束"
SECTION = "自分の約束・宣言"      # 会話用 LLM に渡す節の名前（AGENTS.md の §5 に載せる）
KINDS = ("約束", "宣言")
OPEN, DONE, LAPSED = "未完了", "済み", "流れた"
RECALL_MAX = 3
UNDATED_DAYS = 14           # 期限の無い件の寿命。作られてからこの日数を過ぎたら「流れた」にする
TEXT_MAX = 80
DONE_MIN = 0.8              # 夜の「済み」判定を採る確率
DAY_LINES = 40              # 夜の判定に渡す、その日の会話の行数


def ledger(agent):
    """台帳の State（data は件のリスト）。"""
    st = agent.state(NAME, [])
    if not isinstance(st.data, list):
        st.data = []
    return st


def _same(a, b):
    na, nb = norm(a), norm(b)
    return na == nb or (min(len(na), len(nb)) >= 6 and (na in nb or nb in na))


def add(st, text, who, kind, today, due=""):
    """台帳に足す。足した件、重複なら None。st は State、today は date。"""
    text, who = str(text or "").strip()[:TEXT_MAX], str(who or "").strip()
    if not text or kind not in KINDS:
        return None
    if any(r["状態"] == OPEN and norm(r["相手"]) == norm(who) and _same(r["内容"], text) for r in st.data):
        return None
    n = sum(1 for r in st.data if r["作られた日"] == today.isoformat()) + 1
    rec = {"id": f"{today.isoformat()}-{n}", "内容": text, "相手": who, "種類": kind, "期限": due or "",
           "状態": OPEN, "作られた日": today.isoformat()}
    while any(r["id"] == rec["id"] for r in st.data):
        n += 1
        rec["id"] = f"{today.isoformat()}-{n}"
    st.data.append(rec)
    st.touch()
    return rec


def due_from(text, today):
    """言い回しから期限が決まる時だけ ISO 日付にする（今日中・明日・あさって）。決まらなければ空。"""
    for word, days in (("あさって", 2), ("明後日", 2), ("明日", 1), ("あした", 1), ("今日中", 0)):
        if word in text:
            return (today + timedelta(days=days)).isoformat()
    return ""


def _made(r):
    """作られた日（date）。読めなければ None。"""
    try:
        return date.fromisoformat(str(r.get("作られた日") or ""))
    except ValueError:
        return None


def pick(rows, author, n=RECALL_MAX):
    """想起で渡す件。返信先と一致する件が先、次に期限が近い順（期限なしは後）。
    同じ期限なら作られた順、期限なし同士は新しい順（古い宣言が枠を占め続けないように）。"""
    key = norm(author or "")
    opened = [r for r in rows if r.get("状態") == OPEN]

    def order(r):
        made = _made(r).toordinal() if _made(r) else 0
        return (not (key and norm(r.get("相手", "")) == key), r.get("期限") or "9999-12-31",
                made if r.get("期限") else -made)
    opened.sort(key=order)
    return opened[:n]


def recall(agent, author):
    """返事を書く時の節の本文。未完了が無ければ None。"""
    rows = pick(ledger(agent).data, author)
    if not rows:
        return None
    out = []
    for r in rows:
        tags = [r["種類"]] + ([f"{r['相手']}に"] if r.get("相手") else []) + ([f"期限{r['期限']}"] if r.get("期限") else [])
        out.append(f"- {r['内容']}（{'・'.join(tags)}）")
    return "\n".join(out)


def lapse(rows, today):
    """流れた未完了。期限を過ぎた件（期限の日はまだ有効）と、期限が無いまま作られてから UNDATED_DAYS 日を過ぎた件
    （ちょうど UNDATED_DAYS 日目はまだ有効）。コードの判定。"""
    def over(r):
        if r.get("期限"):
            return r["期限"] < today.isoformat()
        return bool(_made(r)) and (today - _made(r)).days > UNDATED_DAYS
    return [r for r in rows if r.get("状態") == OPEN and over(r)]


def day_text(agent, day):
    """夜の判定の材料: その日の出来事と、会話の末尾。"""
    events = agent.h.data.get("events", {}).get(day.isoformat(), [])
    lines = []
    p = agent.vault.path(f"03 Logs/chat/{day.isoformat()}.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for x in f:
                if x.strip():
                    m = json.loads(x)
                    lines.append(f"{'あなた' if m.get('me') else m.get('author', '')}: {m.get('text', '')}")
    return ("【その日の出来事】\n" + ("\n".join(f"- {e}" for e in events) or "（無し）") +
            "\n【その日の会話】\n" + ("\n".join(lines[-DAY_LINES:]) or "（無し）"))


async def update(agent, day, write_=False):
    """夜の状態更新。[(id, 新しい状態, 書いたか)] を返す。write_ が False なら変えずにログに出す。"""
    st = ledger(agent)
    done = []
    for r in lapse(st.data, day):
        done.append((r["id"], LAPSED, write_))
        if write_:
            r["状態"] = LAPSED
        why = f"期限 {r['期限']} 超過" if r.get("期限") else f"期限なしで {r['作られた日']} から{UNDATED_DAYS}日を過ぎた"
        log.info("約束の台帳%s: %s を %s にする（%s）", "" if write_ else "（ログだけ）", r["内容"], LAPSED, why)
    skip = {d[0] for d in done}
    rest = [r for r in st.data if r["状態"] == OPEN and r["id"] not in skip]
    if rest:
        state = day_text(agent, day) + "\n【自分の約束・宣言】\n" + "\n".join(f"{i}. {r['内容']}" for i, r in enumerate(rest))
        ans = await agent.jev.ask(state, {f"d{i}": noul(f"{i} 番の約束・宣言は、その日の会話か出来事で、実際に果たされた（済んだ）か。")
                                          for i in range(len(rest))})
        for i, r in enumerate(rest):
            if ans.get(f"d{i}", 0) >= DONE_MIN:
                done.append((r["id"], DONE, write_))
                if write_:
                    r["状態"] = DONE
                log.info("約束の台帳%s: %s を %s にする", "" if write_ else "（ログだけ）", r["内容"], DONE)
    if write_ and done:
        st.touch()
        st.flush(agent.now(), force=True)
    return done
