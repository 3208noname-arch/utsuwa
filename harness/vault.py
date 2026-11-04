"""エージェントのフォルダ（Vault の `12 AIたち/<エージェント>/`）の読み書き。

- md はフロントマター（YAML）と本文に分けて読む。LLM に渡すのは本文だけ
- **読むたびに更新時刻を見て、変わっていたら読み直す。**Obsidian で直したものが再起動なしで効く
- 書けるのは WRITABLE の下だけ。`Agents/` は人が直す場所なので、ここからは書けない
- 書く時は一時ファイルに書いてから名前を変える（同期が書きかけのファイルを拾わないように）
"""
import json
import logging
import os
from dataclasses import dataclass, field

import yaml

log = logging.getLogger("utsuwa")

MD_WRITABLE = ("00 Diary", "01 Lessons")       # Vault の md（人格の記憶と日記）
DATA_WRITABLE = ("02 State", "03 Logs")        # 数値の状態と会話の原本。--data で CT の別の場所に置ける
WRITABLE = MD_WRITABLE + DATA_WRITABLE          # 全部を1つのフォルダに置く時（見本とテスト）
WATCH_EXT = (".md", ".yaml", ".yml", ".base")


class NotWritable(Exception):
    """許可リストの外へ書こうとした。"""


@dataclass
class Note:
    meta: dict = field(default_factory=dict)
    body: str = ""


def split(text):
    """md をフロントマターと本文に分ける。フロントマターが無い・壊れていれば meta は空。"""
    if text.startswith("﻿"):
        text = text[1:]
    if not text.startswith("---"):
        return Note({}, text)
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                meta = yaml.safe_load("\n".join(lines[1:i])) or {}
            except yaml.YAMLError:
                meta = {}
            return Note(meta if isinstance(meta, dict) else {}, "\n".join(lines[i + 1:]).lstrip("\n"))
    return Note({}, text)


def dump_meta(meta):
    return yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip("\n")


def join(meta, body):
    return f"---\n{dump_meta(meta)}\n---\n{body.rstrip()}\n" if meta else body.rstrip() + "\n"


def _stamp(path):
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


class Vault:
    def __init__(self, root, writable=WRITABLE):
        self.root = os.path.abspath(root)
        self.writable = writable
        self._cache = {}      # rel -> (stamp, 解いた結果)
        self._seen = None     # 書き換えの検知に使う、前回見た時の {rel: stamp}

    # --- 読む -------------------------------------------------------------
    def path(self, rel):
        p = os.path.abspath(os.path.join(self.root, rel))
        if os.path.commonpath([p, self.root]) != self.root:
            raise NotWritable(f"フォルダの外: {rel}")
        return p

    def exists(self, rel):
        return os.path.exists(self.path(rel))

    def load(self, rel, parse):
        """rel を parse(text) で解いた結果。**更新時刻が変わった時だけ読み直す。**無ければ None。"""
        p = self.path(rel)
        try:
            stamp = _stamp(p)
        except FileNotFoundError:
            self._cache.pop((rel, parse), None)
            return None
        hit = self._cache.get((rel, parse))
        if hit and hit[0] == stamp:
            return hit[1]
        with open(p, encoding="utf-8") as f:
            value = parse(f.read())
        if hit:
            log.info("書き換わったので読み直した: %s", rel)
        self._cache[(rel, parse)] = (stamp, value)
        return value

    def note(self, rel):
        return self.load(rel, split)

    def text(self, rel):
        """フロントマターを外した本文。LLM に渡す形。"""
        n = self.note(rel)
        return n.body.strip() if n else ""

    def notes(self, sub):
        """sub の下の md をすべて [(rel, Note)]。雛形（date が空）も含む。"""
        base = self.path(sub)
        out = []
        for d, _, files in os.walk(base):
            for name in sorted(files):
                if name.endswith(".md"):
                    rel = os.path.relpath(os.path.join(d, name), self.root).replace(os.sep, "/")
                    n = self.note(rel)
                    if n is not None:
                        out.append((rel, n))
        return out

    def json(self, rel, default=None):
        v = self.load(rel, json.loads)
        return default if v is None else v

    # --- 書き換えの検知 -----------------------------------------------------
    def changes(self, subs=("Agents", "01 Lessons", "00 Diary")):
        """前回呼んだ時から書き換わった・増えた・消えたファイル（md、yaml、base）。初回は空。"""
        now = {}
        for sub in subs:
            base = self.path(sub)
            for d, _, files in os.walk(base):
                for name in files:
                    if name.endswith(WATCH_EXT):
                        p = os.path.join(d, name)
                        try:
                            now[os.path.relpath(p, self.root).replace(os.sep, "/")] = _stamp(p)
                        except FileNotFoundError:
                            pass
        before, self._seen = self._seen, now
        if before is None:
            return []
        return sorted(k for k in set(now) | set(before) if now.get(k) != before.get(k))

    # --- 書く ---------------------------------------------------------------
    def _check_writable(self, rel):
        top = rel.replace("\\", "/").split("/")[0]
        if top not in self.writable:
            raise NotWritable(f"ハーネスはここに書けない: {rel}")
        return self.path(rel)

    def write_text(self, rel, text):
        p = self._check_writable(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, p)

    def write_note(self, rel, meta, body):
        """新しいノートを作る。**同じ名前があれば書かずに False。**"""
        if self.exists(rel):
            return False
        self.write_text(rel, join(meta, body))
        return True

    def write_json(self, rel, value):
        self.write_text(rel, json.dumps(value, ensure_ascii=False, indent=1))

    def append_line(self, rel, line):
        p = self._check_writable(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(line.rstrip("\n") + "\n")

    def set_meta(self, rel, key, value):
        """フロントマターの key の項目だけを差し替える（ほかの行と本文には触らない）。"""
        p = self._check_writable(rel)
        with open(p, encoding="utf-8") as f:
            lines = f.read().split("\n")
        if not lines or lines[0].strip() != "---":
            raise ValueError(f"フロントマターが無い: {rel}")
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        new = dump_meta({key: value}).split("\n")
        start = next((i for i in range(1, end) if lines[i].split(":")[0].strip() == key
                      and not lines[i].startswith((" ", "-"))), None)
        if start is None:
            lines[end:end] = new
        else:
            stop = start + 1
            while stop < end and (lines[stop].startswith((" ", "\t", "-")) or not lines[stop].strip()):
                stop += 1
            lines[start:stop] = new
        self.write_text(rel, "\n".join(lines))
