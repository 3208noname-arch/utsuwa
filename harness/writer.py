"""③会話用 LLM が書く。④送る前に確かめて、1案を選ぶ。

- system は `Agents/SOUL.md`＋`Agents/AGENTS.md`（フロントマターを外したもの）だけ。指示はすべて md に置く
- user はデータの節だけ（【今】【直近の会話】など）。節を組むのはコード
- 1回の呼び出しで2〜3案を書かせ、コードの検査と Jev の立場チェックで1案を選ぶ。全部だめなら1回だけ書き直す
"""
import json
import logging
import re
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


_FW = str.maketrans("０１２３４５６７８９：", "0123456789:")


def _numbers(text):
    """金額と時刻の表記を、比べられる形にそろえて抜く。"""
    t = text.translate(_FW).replace(",", "").replace("，", "")
    out = set()
    for h, m in re.findall(r"(\d{1,2}):(\d{2})", t):
        out.add(f"{int(h)}:{m}")
    for ap, h, half, m in re.findall(r"(午前|午後)?(\d{1,2})時(?!間)(半|(\d{1,2})分)?", t):
        hh = int(h) + (12 if ap == "午後" and int(h) < 12 else 0)
        out.add(f"{hh}:{'30' if half == '半' else (m or '00').zfill(2)}")
    def _man(m):
        out.add(str(int(m.group(1)) * 10000 + (int(m.group(2)) if m.lastindex and m.lastindex > 1 and m.group(2) else 0)))
        return " "
    t = re.sub(r"(\d+)万(\d+)円", _man, t)     # 「4万8000円」の 8000円 を別に拾わないよう、先に抜いて消す
    t = re.sub(r"(\d+)万円", _man, t)
    for n in re.findall(r"(\d+)円", t):
        out.add(n.lstrip("0") or "0")
    for n in re.findall(r"(\d+)時間", t):
        out.add(f"{n}時間")
    for n in re.findall(r"(\d+)[〜~]", t):
        out.add(n.lstrip("0") or "0")
    return out


def stray_numbers(msgs, allowed_text):
    """候補に出てくる金額・時刻のうち、渡した節と会話のどこにも無いもの。
    書き手が数字を作った疑いとして、案を落とす材料にする。"""
    allowed = _numbers(allowed_text)
    hours = {x.split(":")[0] for x in allowed if ":" in x}
    out = set()
    for m in msgs:
        for n in _numbers(m):
            if n in allowed:
                continue
            if ":" in n and n.endswith(":00") and n.split(":")[0] in hours:
                continue        # 「16時」は、節に 16:42 とあれば作った数字とはみなさない
            out.add(n)
    return sorted(out)


def passes(msgs, guards):
    return not any(g.search(m) for g in guards for m in msgs)


async def pick(jev, about, log_lines, mine, cands, recalled=""):
    """立場チェック。案ごとの Noul を1回で聞き、閾値を超えた中で一番高い案。
    Jev が失敗したら素通ししない（None を返し、呼び出し側が1回だけ書き直す）。
    recalled は、この返事を書く時に引いた記憶の節（render 済み）。あれば、それと食い違わないことも見る。"""
    tail = ("相手の台詞を代わりに言っていない、自分（あなた）の最近の発言や会話の事実と食い違っていない、"
            "自分の言ったことを相手のせいにしていない。" + ("思い出した記憶と矛盾していない。" if recalled else ""))
    qs = {f"c{i}": noul(f"案{i}は、{about}自身の立場から言える発言として筋が通っているか。" + tail)
          for i in range(len(cands))}
    state = (f"【会話】\n{log_lines}\n【あなたが最近言ったこと】\n" + ("\n".join(mine) or "（無し）") + "\n" +
             (f"【思い出した記憶】\n{recalled}\n" if recalled else "") +
             "\n".join(f"【案{i}】\n" + "\n".join(f"あなた: {m}" for m in c) for i, c in enumerate(cands)))
    a = await jev.ask(state, qs)
    if not a:
        return None, None
    best = max(range(len(cands)), key=lambda i: a.get(f"c{i}", 0))
    score = a.get(f"c{best}", 0)
    return (cands[best], score) if score >= POV_MIN else (None, score)


async def write(llm, jev, vault, about, sections, kind, guards, reactions=(), mine=(), log_lines="", recalled=None):
    """返事を書いて1案を選ぶ。リアクションなら絵文字、文章ならメッセージのリスト。書けなければ None（黙る）。"""
    user = sections_text({**sections, "型": KIND_LABEL[kind], "案の数": str(CANDIDATES)})
    if kind == "reaction" and reactions:
        user += "\n【絵文字の候補】" + " ".join(reactions)
    messages = [{"role": "system", "content": system_prompt(vault)}, {"role": "user", "content": user}]
    recalled_text = sections_text(recalled or {})
    for attempt in range(2):     # 全部だめなら1回だけ書き直す
        text = await llm.chat(messages)
        cands = parse(text, kind, reactions)
        if kind == "reaction":
            return cands[0] if cands else None
        allowed = user + "\n" + log_lines + "\n" + "\n".join(mine)
        ok = []
        for c in cands:
            if not passes(c, guards):
                continue
            stray = stray_numbers(c, allowed)
            if stray:
                log.info("数字チェック: 節と会話に無い数字 %s を含む案を落とした: %s", stray, " ｜ ".join(c))
                continue
            ok.append(c)
        if ok:
            chosen, score = await pick(jev, about, log_lines, mine, ok, recalled_text)
            if chosen is None and score is None:
                chosen, score = await pick(jev, about, log_lines, mine, ok, recalled_text)   # Jev の一時失敗かもしれないので、同じ案でもう1回だけ
            if chosen:
                log.info("案%d件（検査を通った%d件）から選んだ（立場 %s）: %s", len(cands), len(ok),
                         None if score is None else round(score, 2), " ｜ ".join(chosen))
                return chosen
            if score is None:
                log.info("立場チェックができなかった。書き直す")
            else:
                log.info("どの案も立場か事実を取り違えている（最高 %.2f）。書き直す", score)
        else:
            log.info("どの案も検査を通らない（%d件）。書き直す", len(cands))
        if attempt == 0:
            messages = messages + [{"role": "assistant", "content": text or "{}"},
                                   {"role": "user", "content": "【書き直し】どの案も使えなかった。同じ形で、別の案を書き直す"}]
    return None


def dumps(v):
    return json.dumps(v, ensure_ascii=False)
