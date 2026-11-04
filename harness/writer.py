"""③会話用 LLM が書く。④送る前に確かめて、1案を選ぶ。

- system は `Agents/SOUL.md`＋`Agents/AGENTS.md`（フロントマターを外したもの）だけ。指示はすべて md に置く
- user はデータの節だけ（【今】【直近の会話】など）。節を組むのはコード
- 1回の呼び出しで2〜3案を書かせ、コードの検査と Jev の立場チェックで1案を選ぶ。全部だめなら1回だけ書き直す
"""
import json
import logging
from datetime import datetime

from harness.api import loads, noul
from harness.schedule import ago

log = logging.getLogger("utsuwa")

KIND_LABEL = {"reaction": "リアクション", "short": "一言", "normal": "通常"}
CANDIDATES = 3
POV_MIN = 0.65        # minato の実測: 悪い返事 0.45〜0.55、良い返事 0.72〜0.88


def line(m, t):
    """会話の1行。いつの発言かを先頭に付け、自分の発言は「あなた」と書く（minato 0.12.0）。"""
    sec = (t - datetime.fromisoformat(m["at"])).total_seconds() if m.get("at") else 0
    who = f"あなた（{m['author']}）" if m.get("me") else m["author"]
    return f"［{ago(sec)}］{who}: {m['text']}"


def sections_text(sections):
    return "\n".join(f"【{k}】\n{v}" if "\n" in v else f"【{k}】{v}" for k, v in sections.items() if v)


def system_prompt(vault):
    return vault.text("Agents/SOUL.md") + "\n\n" + vault.text("Agents/AGENTS.md")


def parse(text, kind, reactions=()):
    """{"candidates": [{"messages": [...]}, ...]} か {"candidates": [{"reaction": "絵文字"}]}。案のリスト。"""
    d = loads(text)
    raw = d.get("candidates")
    if raw is None:
        raw = [d]          # 1案だけで返してきた時
    out = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        if kind == "reaction":
            e = c.get("reaction")
            if isinstance(e, str) and (not reactions or e in reactions):
                out.append(e)
            continue
        msgs = [m.strip() for m in c.get("messages") or [] if isinstance(m, str) and m.strip()]
        if msgs:
            out.append(msgs[:1] if kind == "short" else msgs[:2])
    return out


def passes(msgs, guards):
    return not any(g.search(m) for g in guards for m in msgs)


async def pick(jev, about, log_lines, mine, cands):
    """立場チェック。案ごとの Noul を1回で聞き、閾値を超えた中で一番高い案。Jev が失敗したら最初の案（棄権）。"""
    qs = {f"c{i}": noul(f"案{i}は、{about}自身の立場から言える発言として筋が通っているか。相手の台詞を代わりに言っていない、"
                        f"自分（あなた）の最近の発言や会話の事実と食い違っていない、自分の言ったことを相手のせいにしていない。")
          for i in range(len(cands))}
    state = (f"【会話】\n{log_lines}\n【あなたが最近言ったこと】\n" + ("\n".join(mine) or "（無し）") + "\n" +
             "\n".join(f"【案{i}】\n" + "\n".join(f"あなた: {m}" for m in c) for i, c in enumerate(cands)))
    a = await jev.ask(state, qs)
    if not a:
        return cands[0], None
    best = max(range(len(cands)), key=lambda i: a.get(f"c{i}", 0))
    score = a.get(f"c{best}", 0)
    return (cands[best], score) if score >= POV_MIN else (None, score)


async def write(llm, jev, vault, about, sections, kind, guards, reactions=(), mine=(), log_lines=""):
    """返事を書いて1案を選ぶ。リアクションなら絵文字、文章ならメッセージのリスト。書けなければ None（黙る）。"""
    user = sections_text({**sections, "型": KIND_LABEL[kind], "案の数": str(CANDIDATES)})
    if kind == "reaction" and reactions:
        user += "\n【絵文字の候補】" + " ".join(reactions)
    messages = [{"role": "system", "content": system_prompt(vault)}, {"role": "user", "content": user}]
    for attempt in range(2):     # 全部だめなら1回だけ書き直す
        text = await llm.chat(messages)
        cands = parse(text, kind, reactions)
        if kind == "reaction":
            return cands[0] if cands else None
        ok = [c for c in cands if passes(c, guards)]
        if ok:
            chosen, score = await pick(jev, about, log_lines, mine, ok)
            if chosen:
                log.info("案%d件（検査を通った%d件）から選んだ（立場 %s）: %s", len(cands), len(ok),
                         None if score is None else round(score, 2), " ｜ ".join(chosen))
                return chosen
            log.info("どの案も立場か事実を取り違えている（最高 %.2f）。書き直す", score or 0)
        else:
            log.info("どの案も検査を通らない（%d件）。書き直す", len(cands))
        if attempt == 0:
            messages = messages + [{"role": "assistant", "content": text or "{}"},
                                   {"role": "user", "content": "【書き直し】どの案も使えなかった。同じ形で、別の案を書き直す"}]
    return None


def dumps(v):
    return json.dumps(v, ensure_ascii=False)
