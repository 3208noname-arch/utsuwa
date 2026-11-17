"""人名の印 `[[<id>|表示名]]`。人を Discord のユーザー ID で指し、表示名が変わっても同じ人に結びつける。

- 印はコードが付ける。LLM に人名かどうかを判断させない。記録用 LLM に渡す材料は、発言者を author_id から引いて
  最初から印の形にする。発言の本文そのもの（「ポン酢かけた」）には印を付けない
- 記録用 LLM に渡す時は `rename`（印は残し、名前を今の名前に）。会話用 LLM と Jev に渡す時は `plain`（印を外して今の名前）
- 書いた後は `check` で、材料に出た ID か台帳にある ID の印だけを残す。知らない ID の印は外して名前だけにする
- 台帳（{id: 今の表示名}）は人物ページのフロントマターと、本体が発言を受けるたびに覚える表示名から作る（`Names`）
"""
import re

LINK = re.compile(r"\[\[([^\[\]|\n]+)\|([^\[\]\n]*)\]\]")
FOLDER = "05 人物"


def link(uid, name):
    """印。uid が無ければ名前だけ（ID の分からない人に印は付けない）。"""
    uid, name = str(uid or "").strip(), clean_name(name)
    if not uid or uid == "None":
        return name
    return f"[[{uid}|{name}]]"


def clean_name(name):
    """印の中に置ける名前。`[` `]` `|` と改行は印を壊すので全角に・空白にする。"""
    return (str(name or "").replace("|", "｜").replace("[", "［").replace("]", "］")
            .replace("\n", " ").replace("\r", " ").strip())


def ids(text):
    """text にある印の ID の集合。"""
    return {m.group(1) for m in LINK.finditer(str(text or ""))}


def rename(text, names):
    """記録用 LLM に渡す形。印は残し、名前を台帳の今の名前にする（台帳に無い ID はそのまま）。"""
    def sub(m):
        now = names.get(m.group(1))
        return f"[[{m.group(1)}|{clean_name(now)}]]" if now else m.group(0)
    return LINK.sub(sub, str(text or ""))


def plain(text, names=None):
    """会話用 LLM・Jev・画面に渡す形。印を外して今の名前（台帳に無ければ印の中の名前）。"""
    names = names or {}
    return LINK.sub(lambda m: str(names.get(m.group(1)) or m.group(2)), str(text or ""))


def check(text, known):
    """書いた後の検査。known（ID の集合）に無い ID の印は外して名前だけにする。(直した文, 外した印のリスト)。"""
    dropped = []

    def sub(m):
        if m.group(1) in known:
            return m.group(0)
        dropped.append(m.group(0))
        return m.group(2)
    return LINK.sub(sub, str(text or "")), dropped


def check_value(v, known):
    """check を文字列・リスト・dict の中まで掛ける。(直した値, 外した印のリスト)。"""
    if isinstance(v, str):
        return check(v, known)
    if isinstance(v, list):
        out, dropped = [], []
        for x in v:
            y, d = check_value(x, known)
            out.append(y)
            dropped += d
        return out, dropped
    if isinstance(v, dict):
        out, dropped = {}, []
        for k, x in v.items():
            y, d = check_value(x, known)
            out[k] = y
            dropped += d
        return out, dropped
    return v, []


def uid_of(v):
    """`[[id|名前]]` か `id` から ID を取る。どちらでもなければ空。"""
    s = str(v or "").strip()
    m = LINK.fullmatch(s)
    if m:
        return m.group(1)
    m = re.fullmatch(r"\[\[([^\[\]|\n]+)\]\]", s)
    return m.group(1) if m else ""


def page_names(vault):
    """人物ページのフロントマターから {id: 情報}。情報は {"name", "user", "aliases"}。"""
    out = {}
    try:
        notes = vault.notes(FOLDER)
    except FileNotFoundError:
        return out
    for rel, n in notes:
        m = n.meta or {}
        uid = str(m.get("id") or "").strip()
        if not uid:
            continue
        al = m.get("aliases") or []
        out[uid] = {"name": str(m.get("表示名") or "").strip(), "user": str(m.get("ユーザー名") or "").strip(),
                    "aliases": [str(x).strip() for x in (al if isinstance(al, list) else [al]) if str(x).strip()]}
    return out


class Names:
    """名前の台帳。人物ページ（夜に書く）と、発言を受けるたびに覚える最新の表示名を重ねる。後者が新しい方なので優先。"""

    def __init__(self, vault):
        self.vault = vault
        self.seen = {}           # {id: 最新の表示名}（発言から）

    def saw(self, uid, name):
        uid, name = str(uid or "").strip(), clean_name(name)
        if uid and uid != "None" and name:
            self.seen[uid] = name

    def current(self):
        """{id: 今の表示名}。"""
        out = {u: d["name"] for u, d in page_names(self.vault).items() if d["name"]}
        out.update(self.seen)
        return out

    def all_names(self):
        """台帳にある名前（今の名前・過去の名前・ユーザー名）の集合。思い出す言葉から人名を落とすのに使う。"""
        out = set(self.seen.values())
        for d in page_names(self.vault).values():
            out |= {x for x in [d["name"], d["user"], *d["aliases"]] if x}
        return out

    def uid_for(self, name):
        """表示名から ID（発言で見た新しい方を優先）。一意に決まらなければ空。"""
        name = clean_name(name)
        if not name:
            return ""
        hits = [u for u, n in self.seen.items() if n == name]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return ""
        hits = [u for u, d in page_names(self.vault).items() if name in {d["name"], *d["aliases"]}]
        return hits[0] if len(hits) == 1 else ""
