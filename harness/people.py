"""人物ページ `05 人物/`。相手ごとに「関係・最近の出来事・約束・呼び名」を物語として持つ。

- `INDEX.md` は表（表示名｜ID｜呼び名｜一行要約｜最終更新）。発言者との対応は、発言者の `author_id` を「ID」で引く。
  表示名は変わるので、ID が書いてある行は表示名では引かない。ID が空の行（手で書いた行、ID 列の無い古い表）だけ、
  発言者の表示名を「表示名」か「呼び名」で引き、次の夜の更新で ID を埋める
- 1人1ページ `<author_id>.md`。ページ名は ID なので、表示名が変わっても情報が分かれない。フロントマターに
  今の表示名（`表示名`）・ユーザー名（`ユーザー名`）・過去の表示名（`aliases`）を持ち、夜の更新で書き換える。
  本文の人名は `[[<id>|表示名]]` の印で書く（harness/who.py）。節は 関係／最近の出来事／約束・宣言／呼び名・呼び方
- ID の分からない人（手で書いた行）のページは `<表示名>.md`。ID の分かった夜に `<id>.md` へ移す
- 想起（`recall`）はコードで決まる固定枠。Jev の選択を介さず、返信先の1人分だけを節にして渡す
- 夜の更新（`update`）は night.py から呼ぶ。書き手は記録用 LLM（`Agents/RECORDER.md` の「人物」）。
  材料はその日の会話の相手の発言、評価コメント、State（人格が `people_facts` で言葉にしたもの）。
  **エージェント自身の発言は材料にしない。** 書く前に `check` で検査し、write が False の間は生成案をログに出すだけ
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date

from harness.api import loads
from harness import who
from harness.saver import norm, safe_name
from harness.vault import join

log = logging.getLogger("utsuwa")

FOLDER = "05 人物"
INDEX = f"{FOLDER}/INDEX.md"
SECTION = "返信先との関係"       # 会話用 LLM に渡す節の名前（AGENTS.md の「渡される節」に載せる）
KIND = "人物"
HEAD = ("関係", "最近の出来事", "約束・宣言", "呼び名・呼び方")
COLS = ("表示名", "ID", "呼び名", "一行要約", "最終更新")
OLD_COLS = ("表示名", "呼び名", "一行要約", "最終更新")       # ID 列を足す前の表。読む時だけ受ける
RECALL_CHARS = {"関係": 200, "最近の出来事": 150, "約束・宣言": 100, "呼び名・呼び方": 40}   # 節ごとの上限（想起で渡す字数）
RECENT_KEEP = 3          # 想起で渡す「最近の出来事」の件数（新しい方）
LIMITS = {"要約": 60, "関係": 200, "出来事": 80, "約束": 80, "呼び名": 12}
EVENTS_MAX, PROMISES_MAX, FORMS_MAX = 8, 5, 4
TODAY_MAX = 5            # 1晩に更新する人数の上限（発言の多い順）
MIN_SAYS = 2             # その日の発言がこれ未満の人は更新しない
CHAT_MAX = 30


@dataclass
class Entry:
    name: str                # 今の表示名
    forms: list = field(default_factory=list)
    summary: str = ""
    updated: str = ""
    uid: str = ""            # 発言者の author_id。空なら、まだ分かっていない

    def rel(self):
        """このページの置き場。ID があれば `<id>.md`、無ければ `<表示名>.md`。"""
        return f"{FOLDER}/{safe_name(self.uid or self.name)}.md"

    def legacy(self):
        """前の作り（ページ名が表示名）の置き場。"""
        return f"{FOLDER}/{safe_name(self.name)}.md"


def page_rel(vault, e):
    """e のページの、今ある置き場。`<id>.md` が無く、前の作りの `<表示名>.md` があればそちら。"""
    if e.uid and not vault.exists(e.rel()) and vault.exists(e.legacy()):
        return e.legacy()
    return e.rel()


# --- INDEX ---------------------------------------------------------------------------------
def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _cell(s):
    """表の1マスに書ける形にする。`|` は列の区切りなので全角に、改行は空白にする。"""
    return str(s or "").replace("|", "｜").replace("\n", " ").replace("\r", " ").strip()


def _uid(v):
    """author_id を文字列に。無い時（None、ログに無くて "None" になったもの）は空。INDEX の `[[id]]` も受ける。"""
    s = _cell(v)
    s = who.uid_of(s) or s
    return "" if s == "None" else s


def parse_index(text):
    """INDEX の表を Entry の並びにする。見出し行と区切り行、列が足りない行は飛ばす。
    列は見出し行で決める（ID 列の無い古い表も読める）。見出しが無ければ列の数で決める。"""
    out, cols = [], None
    for line in text.split("\n"):
        if not line.strip().startswith("|"):
            continue
        c = _cells(line)
        if c[0] == COLS[0]:
            cols = c
            continue
        use = cols or (COLS if len(c) >= len(COLS) else OLD_COLS)
        if len(c) < len(use) or set(c[0]) <= set("-: ") or not c[0]:
            continue
        d = dict(zip(use, c))
        out.append(Entry(d["表示名"], [x.strip() for x in re.split(r"[、,]", d.get("呼び名", "")) if x.strip()],
                         d.get("一行要約", ""), d.get("最終更新", ""), _uid(d.get("ID"))))
    return out


def index(vault):
    n = vault.note(INDEX)
    return parse_index(n.body) if n else []


def render_index(entries):
    rows = [f"| {_cell(e.name)} | {('[[' + _uid(e.uid) + ']]') if _uid(e.uid) else ''} | {'、'.join(_cell(f) for f in e.forms)} | "
            f"{e.summary.replace('|', '／')} | {e.updated} |" for e in entries]
    return "\n".join(["| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS), *rows])


def find(vault, author, uid=None):
    """発言者に当たる Entry。無ければ None。
    uid（author_id）があれば、まず ID の一致で引く（表示名が変わっても同じ人に当たる）。
    当たらなければ、表示名か呼び名の一致（大文字小文字・空白は無視）で引く。この時、別の ID が書いてある行は
    別人なので当てない（uid が無い呼び出しでは、どの行も表示名で引ける）。"""
    ents = index(vault)
    uid = _uid(uid)
    if uid:
        hit = next((e for e in ents if e.uid == uid), None)
        if hit:
            return hit
        ents = [e for e in ents if not e.uid]
    key = norm(_cell(author))
    if not key:
        return None
    return (next((e for e in ents if norm(e.name) == key), None)
            or next((e for e in ents if key in {norm(f) for f in e.forms}), None))


# --- ページ --------------------------------------------------------------------------------
def parse_page(body):
    """本文を {節名: 文} にする。`# 節名` か `## 節名` の見出しで分ける。"""
    out, cur = {}, None
    for line in body.split("\n"):
        m = re.match(r"#{1,3}\s*(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


EMPTY = "（まだ無し）"


def _bullets(s):
    items = [x.strip().lstrip("-・ ").strip() for x in s.split("\n")]
    return [x for x in items if x and x != EMPTY]


def recall(vault, author, uid=None):
    """返信先の人物ページの要約（節の本文）。ページが無ければ None。見出しは今の表示名（ページ名は古い表示名のことがある）。"""
    e = find(vault, author, uid)
    n = vault.note(page_rel(vault, e)) if e else None
    if not n or str(n.meta.get("status", "active")) != "active":
        return None
    sec = parse_page(n.body)
    parts = []
    for h in HEAD:
        items = _bullets(sec.get(h, ""))
        if h == "最近の出来事":
            items = items[-RECENT_KEEP:]
        text = " / ".join(items) if h != "関係" else "".join(items)
        if text:
            parts.append(f"{h}: {text[:RECALL_CHARS[h]]}")
    if not parts:
        return None
    who = str(author or "").strip() or e.name
    return (f"{who}（{e.summary}）\n" if e.summary else f"{who}\n") + "\n".join(parts)


def render_page(name, d, created, forms, uid="", user="", aliases=()):
    """LLM の答え d から、ページの (meta, 本文) を組む。"""
    def lines(xs):
        return "\n".join(f"- {x}" for x in xs) or f"- {EMPTY}"
    body = (f"# 関係\n{d['関係']}\n\n# 最近の出来事\n{lines(d['最近の出来事'])}\n\n"
            f"# 約束・宣言\n{lines(d['約束・宣言'])}\n\n# 呼び名・呼び方\n{lines(forms)}")
    meta = {"date": created, "種類": KIND, "status": "active"}
    if uid:
        meta.update({"id": str(uid), "表示名": _cell(name), "ユーザー名": _cell(user), "aliases": list(aliases)})
    meta["related"] = []
    return meta, body


def _strs(v):
    return [str(x).strip() for x in (v if isinstance(v, list) else []) if str(x).strip()]


def clean(d, material_text):
    """LLM の答えを検査して整える。(整えた dict, だめなら理由)。呼び名は材料に実際に出てくる語だけ。
    字数は人名の印を外した形で数える（印の ID の分で長すぎにしない）。"""
    n = lambda x: len(who.plain(x))
    if not isinstance(d, dict):
        return None, "JSON でない"
    summary, rel_ = str(d.get("要約") or "").strip(), str(d.get("関係") or "").strip()
    if not summary or not rel_:
        return None, "要約か関係が空"
    if n(summary) > LIMITS["要約"] or n(rel_) > LIMITS["関係"]:
        return None, f"長すぎる（要約{n(summary)}字・関係{n(rel_)}字）"
    ev = _strs(d.get("最近の出来事"))
    pr = _strs(d.get("約束・宣言"))
    if len(ev) > EVENTS_MAX or len(pr) > PROMISES_MAX:
        return None, f"件数が多すぎる（出来事{len(ev)}・約束{len(pr)}）"
    if any(n(x) > LIMITS["出来事"] for x in ev) or any(n(x) > LIMITS["約束"] for x in pr):
        return None, "1件が長すぎる"
    hay = norm(material_text)
    forms = []
    for f in d.get("呼び名") or []:
        f = who.plain(str(f)).strip()
        if f and len(f) <= LIMITS["呼び名"] and norm(f) in hay and f not in forms:
            forms.append(f)
    return {"要約": summary, "関係": rel_, "最近の出来事": ev, "約束・宣言": pr, "呼び名": forms[:FORMS_MAX]}, None


# --- 材料 ----------------------------------------------------------------------------------
def day_speakers(agent, day):
    """その日に発言した人（自分と Bot は除く）。[(author_id, 表示名, [発言], ユーザー名)]、発言の多い順。
    表示名とユーザー名は、その日の最後の発言のもの（途中で変えたら新しい方）。"""
    p = agent.vault.path(f"03 Logs/chat/{day.isoformat()}.jsonl")
    by = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for x in f:
                if not x.strip():
                    continue
                m = json.loads(x)
                if m.get("me") or m.get("bot") or not m.get("author") or not str(m.get("text") or "").strip():
                    continue
                rec = by.setdefault(str(m.get("author_id")), {"says": []})
                rec["name"], rec["user"] = m["author"], m.get("username") or rec.get("user", "")
                rec["says"].append(m["text"])
    return sorted(((uid, r["name"], r["says"], r["user"]) for uid, r in by.items()), key=lambda x: -len(x[2]))


def day_ratings(vault, day, name, uid=""):
    """その日にその人が付けた評価コメント。rater_id のある行は ID で、無い古い行は表示名で引く。"""
    rel = f"03 Logs/ratings/{day:%Y-%m}.jsonl"
    p = vault.path(rel)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]

    def mine(r):
        if uid and r.get("rater_id"):
            return str(r["rater_id"]) == str(uid)
        return r.get("rater") == name
    return [r["comment"] for r in rows if mine(r) and r.get("comment")
            and str(r.get("msg_at") or r.get("at"))[:10] == day.isoformat()]


def names_of(agent):
    """{id: 今の表示名}。本体が台帳を持っていなければ人物ページだけ。"""
    n = getattr(agent, "whois", None)
    return n.current() if n else {u: d["name"] for u, d in who.page_names(agent.vault).items() if d["name"]}


async def propose(agent, day, uid, name, says, user=""):
    """1人分の更新案を作る。(Entry 向けの dict, ページの meta, 本文) か None。保存はしない。"""
    v = agent.vault
    uid = _uid(uid)
    e = find(v, name, uid)
    page = Entry(_cell(name), uid=uid or (e.uid if e else ""))
    if norm(safe_name(page.uid or page.name)) in {"", "index"}:
        return None
    old_rel = page_rel(v, e) if e else None
    old = v.note(old_rel) if old_rel else None
    names = names_of(agent)
    names = {**names, page.uid: page.name} if page.uid else names
    facts = agent.persona.people_facts(uid, name, day, "\n".join(says))
    rated = day_ratings(v, day, name, uid)
    me = who.link(page.uid, name)
    user_text = (f"【種類】{KIND}\n【相手】{me}\n【今のページ】\n" +
                 (who.rename(old.body.strip(), names) if old else "（まだ無い）") +
                 f"\n【その日の{me}の発言】\n" + "\n".join(f"- {x[:120]}" for x in says[-CHAT_MAX:]) +
                 "\n【その日の評価コメント】\n" + ("\n".join(f"- {x}" for x in rated) or "（無し）") +
                 "\n【その日の状態】\n" + ("\n".join(f"- {x}" for x in facts) or "（無し）"))
    text, _ = await agent.llm.chat([{"role": "system", "content": v.text("Agents/RECORDER.md")},
                                 {"role": "user", "content": user_text}], temperature=0)
    d, why = clean(loads(text), user_text)
    if why:
        log.info("人物ページを保存しない（%s）: %s", why, name)
        return None
    d, dropped = who.check_value(d, who.ids(user_text) | set(names))
    if dropped:
        log.info("人物ページ: 知らない ID の印を外した %s: %s", dropped, name)
    kept = parse_page(old.body).get("呼び名・呼び方", "") if old else ""
    forms = list(dict.fromkeys(_bullets(kept) + d["呼び名"]))
    if not page.uid:      # ID の無いページ（前の作り）は、今の表示名を呼び名に残して引けるようにする
        forms = list(dict.fromkeys(([_cell(name)] if e and norm(_cell(name)) != norm(e.name) else []) + forms))
    forms = forms[:FORMS_MAX + 2]
    meta_old = old.meta if old else {}
    prev = [str(x) for x in (meta_old.get("aliases") or []) if str(x).strip()]
    before = str(meta_old.get("表示名") or (e.name if e else "")).strip()
    aliases = list(dict.fromkeys(prev + ([before] if before and before != page.name else [])))
    aliases = [x for x in aliases if x != page.name]
    user = _cell(user) or str(meta_old.get("ユーザー名") or "")
    created = meta_old.get("date") or day
    meta, body = render_page(page.name, d, created, forms, page.uid, user, aliases)
    return {"entry": Entry(page.name, forms, d["要約"], day.isoformat(), page.uid),
            "meta": meta, "body": body, "old_rel": old_rel}


def save(vault, proposal):
    """ページを書き（既存なら上書き）、INDEX の行を差し替える。前の作りの `<表示名>.md` から移した時は、古い方を消す。rel を返す。"""
    e = proposal["entry"]
    vault.write_text(e.rel(), join(proposal["meta"], proposal["body"]))
    old = proposal.get("old_rel")
    if old and old != e.rel():
        vault.remove(old)
        log.info("人物ページを ID の名前に移した: %s → %s", old, e.rel())
    same = (lambda x: x.uid == e.uid) if e.uid else (lambda x: not x.uid and norm(x.name) == norm(e.name))
    ents = [x for x in index(vault) if not same(x)
            and not (e.uid and not x.uid and old and x.legacy() == old)] + [e]
    ents.sort(key=lambda x: x.name)
    n = vault.note(INDEX)
    meta = dict(n.meta) if n and n.meta else {"date": date.today(), "status": "active", "種類": "索引"}
    head = n.body.split("|", 1)[0].rstrip() + "\n\n" if n and "|" in n.body else ""
    vault.write_text(INDEX, join(meta, head + render_index(ents)))
    return e.rel()


async def update(agent, day, write_=False):
    """その日に話した人の人物ページを更新する。[(rel, 書いたか)] を返す。write_ が False なら書かずにログに出す。"""
    done = []
    for uid, name, says, user in day_speakers(agent, day)[:TODAY_MAX]:
        if len(says) < MIN_SAYS:
            continue
        p = await propose(agent, day, uid, name, says, user)
        if not p:
            continue
        rel = p["entry"].rel()
        if write_:
            save(agent.vault, p)
        else:
            log.info("人物ページの更新案（ログだけ）: %s｜%s\n%s", rel, p["entry"].summary, p["body"])
        log.info("人物ページ%s: %s", "を更新した" if write_ else "の案を作った", rel)
        done.append((rel, write_))
    return done
