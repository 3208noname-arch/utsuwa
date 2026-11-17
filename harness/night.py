"""記録④ 夜の整理。エージェントが寝ている時間に1日1回動かす。

1. 似た記憶の組を集める（コード）：同じ種類で、思い出す言葉か本文の語が重なる現役の組
2. Jev が比べる：同じ事柄なら、新しい方を正にし、古い方を `status: inactive` にする（消さない）。
   片方が会話、片方が評価で教わったものなら、日付に関係なく会話の方を正にする
3. 「その他」を数える：溜まったら知らせる（新しい種類を作るかは人が決める）
4. 日記を書く：その日の出来事、覚えたこと、自分が言ったことだけを材料に、記録用 LLM が書く

5. 人物ページを更新する：その日に話した人ごとに、記録用 LLM が `05 人物/` のページを書き直す（harness/people.py）
6. 近況を蒸留する：直近3日の日記・出来事・新着の記憶・人物ページの更新から、記録用 LLM が `02 State/近況.md` を書き直す（harness/digest.py）

6'. 約束・宣言の台帳を更新する：期限切れの未完了は「流れた」（コード）、その日に果たした分は「済み」（Jev。harness/promises.py）

config.yaml の `night.write` が false の間は、2で何を古いにするか、5と6と台帳で何を書くかをログに出すだけで書き換えない。
`night.parts`（diary・lessons・people・digest・promises）に書いた部品だけは、write に関係なくその値が優先する。
書かなかった部品は write に従う（日記だけは従来どおり、parts に false と書かない限り書く）。
夜の実行ごとに、部品ごとの生成数・保存数・見送り数を `03 Logs/night/YYYY-MM-DD.json` に残す（write の設定に関係なく）。
部品の1つが例外を出しても、残りの部品と夜の報告は続ける。失敗した部品は件数を0にし、報告の `errors` に例外を残す。
"""
import json
import logging
from datetime import datetime, timedelta
from itertools import combinations

from harness.api import loads, noul
from harness import digest, people, promises, who
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


PARTS = ("diary", "lessons", "people", "digest", "promises")


def part_on(cfg, name):
    """部品 name を書くか。parts に書いてあればその値、無ければ write に従う（日記だけは既定で書く）。"""
    parts = cfg.get("parts") or {}
    if name in parts:
        return bool(parts[name])
    return True if name == "diary" else bool(cfg.get("write"))


def count(made, saved):
    return {"generated": made, "saved": saved, "skipped": made - saved}


def report(agent, day, cfg, parts_on, counts, n_other, errors=None):
    """夜の報告を `03 Logs/night/日付.json` に足す（同じ日に何度回しても runs に積む）。
    errors は、例外で失敗した部品 {部品名: "例外の種類: 文"}。失敗が無ければ空。"""
    rel = f"03 Logs/night/{day.isoformat()}.json"
    try:
        data = json.loads(agent.vault.text(rel)) if agent.vault.exists(rel) else {}
    except ValueError:
        data = {}
    data["date"] = day.isoformat()
    data.setdefault("runs", []).append({"at": agent.now().isoformat(timespec="seconds"), "write": bool(cfg.get("write")),
                                       "parts_on": parts_on, "parts": counts, "other": n_other,
                                       "errors": errors or {}})
    agent.vault.write_text(rel, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return rel


def count_other(agent):
    return sum(1 for r in lesson_rows(agent.vault) if r.kind == "その他")


async def diary_draft(agent, day, events, learned, mine):
    """その日の日記の本文を作る。材料にあることだけを書かせる。既にある日・書けなかった日は None。"""
    rel = f"00 Diary/{day.isoformat()}.md"
    if agent.vault.exists(rel):
        return None
    names = people.names_of(agent)
    user = who.rename("【種類】日記\n【その日の出来事】\n" + ("\n".join(f"- {e}" for e in events) or "（無し）") +
                      "\n【その日に覚えたこと】\n" + ("\n".join(f"- {x}" for x in learned) or "（無し）") +
                      "\n【自分が言ったこと】\n" + ("\n".join(f"- {x}" for x in mine) or "（無し）"), names)
    text, _ = await agent.llm.chat([{"role": "system", "content": agent.vault.text("Agents/RECORDER.md")},
                                 {"role": "user", "content": user}], temperature=0.7)
    body, dropped = who.check(str(loads(text).get("body") or "").strip(), who.ids(user) | set(names))
    if dropped:
        log.info("日記: 知らない ID の印を外した %s", dropped)
    return body or None


def diary_save(agent, day, body):
    rel = f"00 Diary/{day.isoformat()}.md"
    agent.vault.write_note(rel, {"date": day, "種類": "日記", "status": "active"}, body)
    log.info("日記を書いた: %s", rel)
    return rel


async def diary(agent, day, events, learned, mine):
    """その日の日記を書く。書けたら rel。"""
    body = await diary_draft(agent, day, events, learned, mine)
    return diary_save(agent, day, body) if body else None


async def run(agent, t: datetime):
    """夜の整理を1回。"""
    cfg = agent.config.get("night") or {}
    on = {k: part_on(cfg, k) for k in PARTS}
    day = (t - timedelta(days=1)).date() if t.hour < 12 else t.date()
    errors = {}

    async def part(name, work, default):
        """部品を1つ動かす。例外は受けて報告に残し、残りの部品と夜の報告を続ける。"""
        try:
            return await work()
        except Exception as e:
            log.exception("夜の整理: 部品 %s に失敗", name)
            errors[name] = f"{type(e).__name__}: {e}"
            return default

    async def other():
        return count_other(agent)

    async def write_diary():
        events, learned, mine = agent.day_material(day)
        body = await diary_draft(agent, day, events, learned, mine)
        if body and on["diary"]:
            diary_save(agent, day, body)
        elif body:
            log.info("日記の案（ログだけ）: %s\n%s", day, body)
        return body

    done = await part("lessons", lambda: tidy(agent, day, write=on["lessons"]), [])
    n_other = await part("lessons", other, 0)
    if n_other > OTHER_ALERT:
        agent.notify(f"「その他」の記憶が{n_other}件になった。新しい種類を作るか見てほしい")
    body = await part("diary", write_diary, None)
    met = await part("people", lambda: people.update(agent, day, write_=on["people"]), [])
    kept = await part("promises", lambda: promises.update(agent, day, write_=on["promises"]), [])
    gist = await part("digest", lambda: digest.update(agent, day, write_=on["digest"]), [])
    counts = {
        "diary": count(int(bool(body)), int(bool(body and on["diary"]))),
        "lessons": count(len(done), len(done) if on["lessons"] else 0),
        "people": count(len(met), sum(1 for x in met if x[-1])),
        "digest": count(len(gist), sum(1 for x in gist if x[-1])),
        "promises": count(len(kept), sum(1 for x in kept if x[-1])),
    }
    report(agent, day, cfg, on, counts, n_other, errors)
    return {"tidied": done, "other": n_other, "people": met, "promises": kept, "digest": gist, "counts": counts,
            "errors": errors}
