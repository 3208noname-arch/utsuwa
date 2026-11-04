"""記録③ 保存する。プロパティはコードが付け、検査してから `01 Lessons/` に1件1ノートで書く。LLM にはプロパティを書かせない。"""
import logging
import os
import re
from datetime import date

log = logging.getLogger("utsuwa")

BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]')
NAME_MAX = 40
SOURCED = {"常識", "その他", "反省"}        # 出どころ（会話／評価）を付ける種類
SHORT = {"常識": 120, "反省": 120}          # 本文の字数の上限


def norm(s):
    return "".join(str(s or "").split()).casefold()


def safe_name(name):
    return BAD_NAME.sub("", str(name or "")).strip(" .")[:NAME_MAX]


def keep_words(words, *texts):
    """思い出す言葉のうち、本文か元の発言に実際に出てくるもの（LLM が作った語をそのまま信じない）。"""
    hay = norm("".join(texts))
    out = []
    for w in words or []:
        w = str(w).strip()[:20]
        if w and norm(w) in hay and w not in out:
            out.append(w)
    return out[:6]


def meta_for(kind, day: date, people, source=None, words=(), scene=""):
    m = {"date": day, "教わった人": list(people), "status": "active", "種類": kind}
    if kind in SOURCED:
        m["出どころ"] = source or "会話"
    m["思い出す言葉"] = list(words)
    if kind == "思い出":
        m["喜怒哀楽"] = ""          # 形が未定なので空のまま（AIたちの運用ルール）
    if kind == "反省":
        m["場面"] = scene
        m["再発"] = []
    m["related"] = []
    return m


def check(vault, kinds, kind, name, body):
    """保存してよいか。だめなら理由の文字列。"""
    if kind not in kinds:
        return f"知らない種類: {kind}"
    if not name:
        return "名前が空"
    if not body.strip():
        return "本文が空"
    if kind in SHORT and len(body) > SHORT[kind]:
        return f"本文が長すぎる（{len(body)}字）"
    folder = kinds[kind]["folder"]
    for rel, n in vault.notes(f"01 Lessons/{folder}"):
        if n.meta.get("種類") == kind and norm(n.body) == norm(body):
            return f"同じ本文のノートがある: {rel}"
    return None


def save(vault, kinds, kind, name, body, meta):
    """書けたら rel、書かなかったら None。同じ名前があれば末尾に番号を付ける。"""
    name = safe_name(name)
    why = check(vault, kinds, kind, name, body)
    if why:
        log.info("保存しない（%s）: %s", why, name)
        return None
    folder = kinds[kind]["folder"]
    rel, i = f"01 Lessons/{folder}/{name}.md", 2
    while vault.exists(rel):
        rel, i = f"01 Lessons/{folder}/{name} {i}.md", i + 1
    vault.write_note(rel, meta, body)
    ensure_month_base(vault, meta["date"])
    log.info("保存した: %s", rel)
    return rel


def add_recurrence(vault, rel, day: date):
    """反省の再発に日付を足す（同じ指摘がまた来た）。"""
    n = vault.note(rel)
    days = [str(x) for x in (n.meta.get("再発") or [])]
    if day.isoformat() not in days:
        vault.set_meta(rel, "再発", days + [day.isoformat()])


def ensure_month_base(vault, day: date):
    """月の Base（`01 Lessons/2026/11月.base`）が無ければ、ある月の Base を写して作る。"""
    rel = f"01 Lessons/{day.year}/{day.month}月.base"
    if vault.exists(rel):
        return False
    base = vault.path("01 Lessons")
    src = None
    for d, _, files in os.walk(base):
        for f in files:
            if re.fullmatch(r"\d+月\.base", f):
                src = os.path.join(d, f)
    if not src:
        return False
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"date\.year == \d+", f"date.year == {day.year}", text)
    text = re.sub(r"date\.month == \d+", f"date.month == {day.month}", text)
    text = re.sub(r"name: \d+月", f"name: {day.month}月", text)
    vault.write_text(rel, text)
    log.info("月の Base を作った: %s", rel)
    return True
