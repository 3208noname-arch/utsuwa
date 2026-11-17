"""既存のノートの人名を `[[<id>|表示名]]` の印に置き換える移行。既定は dry-run（何も書かない）。

    python -m harness.migrate_names "<Vault>/12 AIたち/<名前>" [--word ゆず胡椒] [--diff] [--write]

- 名前と ID の対応は `03 Logs/chat/*.jsonl` の author / author_id から作る（自分と Bot は除く）。
  人物ページの INDEX に ID のある行の表示名も足す。同じ名前に ID が2つ以上あれば、その名前は置き換えない（曖昧）
- 対象は 00 Diary、01 Lessons、02 State（md と 約束.json の「相手」）、05 人物
- 本文: 知っている名前を印にする。ただし「普通の語でもある名前」（`--word` で渡した語と、ひらがな3字以下の名前）は、
  そのノートがその人に結びつき（教わった人にいる、日記ならその日のログで発言しているか返事を受けている）、
  名前の後に人を指す言い方（から・に・が・と・は…）が続き、同じ文に食べ物の手がかり（鍋・かけ・味…）が無い時だけ置き換える。
  それ以外は置き換えずに曖昧の一覧に出す
- フロントマター: 教わった人を印に、関係する人（ID）を足す、思い出す言葉から人名を落とす
  （普通の語でもある名前は、ノートがその人に結びつく時だけ落とし、それ以外は一覧に出す）
- 05 人物/<表示名>.md（INDEX に ID のある行）は 05 人物/<id>.md に移し、フロントマターに id・表示名・ユーザー名・aliases を足す
"""
import argparse
import difflib
import glob
import json
import os
import re
import sys
from collections import defaultdict

from harness import people, who
from harness.vault import Vault, dump_meta, join, split

FOLDERS = ("00 Diary", "01 Lessons", "02 State", "05 人物")
PERSON_AFTER = re.compile(r"^(?:から|に|が|と|は|も|へ|さん|くん|君|ちゃん|氏|の(?:発言|話|こと|奴|やつ)|[、。！？!?」』）)\s]|$)")
FOOD = re.compile(r"鍋|かけ|味|醤油|調味料|餃子|しゃぶ|酸っぱ|ドレッシング|瓶|ボトル|冷蔵庫|料理|食べ|食う|飯|牡蠣|柿|大根")
SENT = re.compile(r"[^。！？!?\n]*[。！？!?\n]?")


def directory(root):
    """ログから {名前: {ID}}、{ID: 最新の表示名}、{ID: ユーザー名}、{日付: {その日に関わった ID}}。"""
    by_name, latest, user, days = defaultdict(set), {}, {}, defaultdict(set)
    for p in sorted(glob.glob(os.path.join(root, "03 Logs", "chat", "*.jsonl"))):
        day = os.path.basename(p)[:10]
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                m = json.loads(line)
                if m.get("me"):
                    if m.get("target_id") and m.get("target_author"):
                        days[day].add(str(m["target_id"]))
                    continue
                if m.get("bot") or not m.get("author") or not m.get("author_id"):
                    continue
                uid = str(m["author_id"])
                by_name[m["author"]].add(uid)
                latest[uid] = m["author"]
                if m.get("username"):
                    user[uid] = m["username"]
                days[day].add(uid)
    v = Vault(root)
    for e in people.index(v):
        if e.uid:
            by_name[e.name].add(e.uid)
            latest.setdefault(e.uid, e.name)
    return by_name, latest, user, days


def wordlike(name, words):
    return name in words or bool(re.fullmatch(r"[ぁ-ゖー]{1,3}", name))


class Plan:
    def __init__(self):
        self.replaced, self.ambiguous, self.meta, self.moves, self.writes = [], [], [], [], {}

    def amb(self, rel, line, name, why, ctx):
        self.ambiguous.append({"file": rel, "line": line, "name": name, "why": why, "context": ctx})


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _sentence(text, pos):
    start = max(text.rfind(c, 0, pos) for c in "。！？!?\n") + 1
    ends = [i for i in (text.find(c, pos) for c in "。！？!?\n") if i >= 0]
    return text[start:(min(ends) + 1 if ends else len(text))].strip()


def mark_body(rel, body, by_name, tied, words, plan):
    """本文の人名を印にする。(新しい本文, 置き換えた ID の集合)。"""
    names = sorted((n for n in by_name if n), key=len, reverse=True)
    spans = [(m.start(), m.end()) for m in who.LINK.finditer(body)]          # 既にある印の中は触らない
    hits = []
    for n in names:
        for m in re.finditer(re.escape(n), body):
            s, e = m.start(), m.end()
            if any(a < e and s < b for a, b in spans):
                continue
            spans.append((s, e))
            hits.append((s, e, n))
    out, last, used = [], 0, set()
    for s, e, n in sorted(hits):
        ids = by_name[n]
        ctx = _sentence(body, s)
        line = _line_of(body, s)
        if len(ids) != 1:
            plan.amb(rel, line, n, f"同じ名前の ID が{len(ids)}つ", ctx)
            continue
        uid = next(iter(ids))
        if wordlike(n, words):
            if uid not in tied:
                plan.amb(rel, line, n, "普通の語でもある名前で、ノートがこの人に結びつかない", ctx)
                continue
            if not PERSON_AFTER.match(body[e:e + 4]):
                plan.amb(rel, line, n, "普通の語でもある名前で、後ろが人を指す言い方でない", ctx)
                continue
            if FOOD.search(ctx.replace(n, "")):
                plan.amb(rel, line, n, "普通の語でもある名前で、同じ文に食べ物の手がかりがある", ctx)
                continue
        out.append(body[last:s])
        out.append(who.link(uid, n))
        last = e
        used.add(uid)
        plan.replaced.append({"file": rel, "line": line, "name": n, "id": uid, "context": ctx})
    out.append(body[last:])
    return "".join(out), used


def _list(v):
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v]) if str(x).strip()]


def migrate_note(v, rel, by_name, latest, days, words, plan, all_names):
    """1ノート分。新しい全文か、変わらなければ None。"""
    raw = open(v.path(rel), encoding="utf-8").read()
    note = split(raw)
    meta = dict(note.meta)
    taught = _list(meta.get("教わった人"))
    tied = set()
    new_taught = []
    for t in taught:
        u = who.uid_of(t)
        if u:
            tied.add(u)
            new_taught.append(t)
            continue
        ids = by_name.get(t, set())
        if len(ids) == 1:
            u = next(iter(ids))
            tied.add(u)
            new_taught.append(who.link(u, t))
        else:
            if ids:
                plan.amb(rel, 0, t, f"教わった人: 同じ名前の ID が{len(ids)}つ", "（フロントマター）")
            new_taught.append(t)
    m = re.match(r"(?:\d{4}-\d{2}-\d{2})", os.path.basename(rel))
    day = m.group(0) if m else str(meta.get("date") or "")[:10]
    if rel.startswith("00 Diary"):
        tied |= days.get(day, set())
    if rel.startswith("05 人物") and meta.get("id"):
        tied.add(str(meta["id"]))
    body, used = mark_body(rel, note.body, by_name, tied, words, plan)
    changes = {}
    if new_taught != taught:
        changes["教わった人"] = new_taught
    if "教わった人" in meta or "思い出す言葉" in meta:
        rel_ids = list(dict.fromkeys(_list(meta.get("関係する人")) +
                                     [who.uid_of(t) for t in new_taught if who.uid_of(t)] + sorted(used | who.ids(body))))
        if rel_ids != _list(meta.get("関係する人")):
            changes["関係する人"] = rel_ids
    words_ = _list(meta.get("思い出す言葉"))
    keep = []
    for w in words_:
        ids = by_name.get(w, set())
        if w not in all_names:
            keep.append(w)
        elif wordlike(w, words) and not (ids & (tied | used)):
            plan.amb(rel, 0, w, "思い出す言葉: 普通の語でもある名前で、ノートがこの人に結びつかない（残した）", "（フロントマター）")
            keep.append(w)
        # それ以外の人名は落とす
    if keep != words_:
        changes["思い出す言葉"] = keep
    if not changes and body == note.body:
        return None
    for k, val in changes.items():
        plan.meta.append({"file": rel, "key": k, "before": meta.get(k), "after": val})
    return _rewrite(raw, note, changes, body)


def _rewrite(raw, note, changes, body):
    """フロントマターの変わった項目だけを差し替え、本文を新しくした全文。ほかの行はそのまま。"""
    if not raw.startswith("---"):
        return body
    lines = raw.split("\n")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    head = lines[1:end]
    for key, value in changes.items():
        new = dump_meta({key: value}).split("\n")
        start = next((i for i, x in enumerate(head) if x.split(":")[0].strip() == key and not x.startswith((" ", "-"))), None)
        if start is None:
            anchor = next((i + 1 for i, x in enumerate(head) if x.split(":")[0].strip() == "教わった人"
                           and not x.startswith((" ", "-"))), len(head))
            while anchor < len(head) and head[anchor].startswith((" ", "\t", "-")):
                anchor += 1
            head[anchor:anchor] = new
        else:
            stop = start + 1
            while stop < len(head) and (head[stop].startswith((" ", "\t", "-")) or not head[stop].strip()):
                stop += 1
            head[start:stop] = new
    rest = "\n".join(lines[end + 1:])
    lead = rest[:len(rest) - len(rest.lstrip("\n"))]
    return "\n".join(["---", *head, "---"]) + "\n" + lead + body


def plan_pages(v, latest, user, plan):
    """前の作りの人物ページ（<表示名>.md、INDEX に ID あり）を <id>.md に移す案。[(古い rel, 新しい rel, 新しい全文)]。"""
    out = []
    for e in people.index(v):
        if not e.uid or v.exists(e.rel()) or not v.exists(e.legacy()):
            continue
        n = v.note(e.legacy())
        name = latest.get(e.uid, e.name)
        aliases = [e.name] if e.name != name else []
        meta = {**n.meta, "id": e.uid, "表示名": name, "ユーザー名": user.get(e.uid, ""), "aliases": aliases}
        out.append((e.legacy(), e.rel(), join(meta, n.body)))
        plan.moves.append({"from": e.legacy(), "to": e.rel(), "表示名": name, "aliases": aliases})
    return out


def run(root, words=(), write=False, diff=False, out=sys.stdout):
    v = Vault(root)
    by_name, latest, user, days = directory(root)
    words = set(words)
    all_names = {n for n in by_name if n} | set(user.values())
    plan = Plan()
    moved = plan_pages(v, latest, user, plan)
    move_to = {a: (b, text) for a, b, text in moved}
    for folder in FOLDERS:
        if not v.exists(folder):
            continue
        for rel, _ in v.notes(folder):
            if rel == people.INDEX:
                continue
            if rel in move_to:
                new_rel, text = move_to[rel]
                tmp_note = split(text)
                meta = tmp_note.meta
                body, used = mark_body(new_rel, tmp_note.body, by_name, {meta["id"]}, words, plan)
                plan.writes[new_rel] = (None, join(meta, body))
                plan.writes[rel] = (open(v.path(rel), encoding="utf-8").read(), None)     # 消す
                continue
            new = migrate_note(v, rel, by_name, latest, days, words, plan, all_names)
            if new is not None:
                plan.writes[rel] = (open(v.path(rel), encoding="utf-8").read(), new)
    # 約束の台帳の「相手」
    prel = "02 State/約束.json"
    if v.exists(prel):
        before = open(v.path(prel), encoding="utf-8").read()
        rows = json.loads(before) if before.strip() else []
        changed = False
        for r in rows if isinstance(rows, list) else []:
            to = str(r.get("相手") or "")
            if not to or who.uid_of(to):
                continue
            ids = by_name.get(to, set())
            if len(ids) == 1:
                r["相手"] = who.link(next(iter(ids)), to)
                plan.meta.append({"file": prel, "key": f"{r.get('id')}.相手", "before": to, "after": r["相手"]})
                changed = True
            elif ids:
                plan.amb(prel, 0, to, f"約束の相手: 同じ名前の ID が{len(ids)}つ", r.get("内容", ""))
        if changed:
            plan.writes[prel] = (before, json.dumps(rows, ensure_ascii=False, indent=1))
    if moved:
        ents = []
        for e in people.index(v):
            e.name = latest.get(e.uid, e.name) if e.uid else e.name
            ents.append(e)
        n = v.note(people.INDEX)
        head = n.body.split("|", 1)[0].rstrip() + "\n\n" if "|" in n.body else ""
        plan.writes[people.INDEX] = (open(v.path(people.INDEX), encoding="utf-8").read(),
                                     join(n.meta, head + people.render_index(ents)))
    report(plan, words | {n for n in by_name if wordlike(n, words)}, out, diff)
    if write:
        for rel, (_, new) in plan.writes.items():
            if new is None:
                v.remove(rel)
            else:
                v.write_text(rel, new if new.endswith("\n") else new + "\n")
        print(f"\n書いた: {len(plan.writes)} ファイル", file=out)
    else:
        print("\n（dry-run: 何も書いていない。書くには --write）", file=out)
    return plan


def report(plan, words, out, diff):
    p = lambda *a: print(*a, file=out)
    p(f"# 人名の移行（普通の語として扱った名前: {'、'.join(sorted(words)) or 'なし'}）")
    p(f"\n## 本文の置き換え候補 {len(plan.replaced)}件")
    for r in plan.replaced:
        p(f"- {r['file']}:{r['line']}  {r['name']} → [[{r['id']}|{r['name']}]]  「{r['context']}」")
    p(f"\n## フロントマターの変更 {len(plan.meta)}件")
    for r in plan.meta:
        p(f"- {r['file']}  {r['key']}: {r['before']} → {r['after']}")
    p(f"\n## 人物ページの移動 {len(plan.moves)}件")
    for r in plan.moves:
        p(f"- {r['from']} → {r['to']}（表示名 {r['表示名']}、aliases {r['aliases']}）")
    p(f"\n## 曖昧（置き換えない） {len(plan.ambiguous)}件")
    for r in plan.ambiguous:
        where = f"{r['file']}:{r['line']}" if r["line"] else r["file"]
        p(f"- {where}  {r['name']}（{r['why']}）  「{r['context']}」")
    if diff:
        p("\n## 差分")
        for rel, (old, new) in plan.writes.items():
            a = (old or "").splitlines(keepends=True)
            b = (new or "").splitlines(keepends=True)
            out.writelines(difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}" if new is not None else "/dev/null"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", help="エージェントのフォルダ（Vault の 12 AIたち/<名前>）")
    ap.add_argument("--word", action="append", default=[], help="普通の語でもある名前（何度でも）")
    ap.add_argument("--diff", action="store_true", help="差分も出す")
    ap.add_argument("--write", action="store_true", help="実際に書き換える（既定は dry-run）")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    run(a.root, a.word, a.write, a.diff)


if __name__ == "__main__":
    main()
