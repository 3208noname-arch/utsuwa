"""状態（`02 State/`）と会話の原本（`03 Logs/`）。どちらもハーネスだけが書く。

- 状態は部品ごとに1ファイル（body.json、likes.json など）。**書き手は1人**（minato は state.json 1枚を全部の機能が書いていた）
- Vault の上なので、毎分は書かない。変わった時に FLUSH_EVERY に1回までと、止める時に書く
- 会話は発言ごとに1行足す（日ごとの JSONL）。評価の時に前後の会話を引ける（minato は時間が経つと空だった）
"""
import json
import os
from collections import deque
from datetime import datetime, timedelta

FLUSH_EVERY = timedelta(minutes=5)
RECENT = 40                 # 会話用に覚えておく直近の発言数（縦画面のモニター1枚ぶん。minato 0.13.0）
MY_LINES_HOURS = 6          # 自分の発言を別に覚えておく時間
MY_LINES_MAX = 15


class State:
    """1つの部品の状態。data を直接いじり、変えたら touch() する。"""

    def __init__(self, vault, name, default=None):
        self.vault, self.rel = vault, f"02 State/{name}.json"
        self.data = vault.json(self.rel, None)
        if self.data is None:
            self.data = default if default is not None else {}
        self.dirty, self.saved_at = False, None

    def touch(self):
        self.dirty = True

    def flush(self, t, force=False):
        if not self.dirty:
            return False
        if not force and self.saved_at and t - self.saved_at < FLUSH_EVERY:
            return False
        self.vault.write_json(self.rel, self.data)
        self.dirty, self.saved_at = False, t
        return True


class Logs:
    def __init__(self, vault):
        self.vault = vault

    def chat(self, m):
        day = m["at"][:10]
        self.vault.append_line(f"03 Logs/chat/{day}.jsonl", json.dumps(m, ensure_ascii=False))

    def rating(self, rec):
        self.vault.append_line(f"03 Logs/ratings/{rec['at'][:7]}.jsonl", json.dumps(rec, ensure_ascii=False))

    def said(self, rec):
        """返事を出した時の記録。発言の ID が無い影の返事も、本文で評価と突き合わせる。"""
        self.vault.append_line(f"03 Logs/said/{rec['at'][:7]}.jsonl", json.dumps(rec, ensure_ascii=False))

    def find_said(self, text, at):
        """評価された本文に当たる、いちばん新しい返事の記録。"""
        day = datetime.fromisoformat(at)
        rows = []
        for d in (day.replace(day=1) - timedelta(days=1), day):
            p = self.vault.path(f"03 Logs/said/{d:%Y-%m}.jsonl")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    rows += [json.loads(x) for x in f if x.strip()]
        return next((r for r in reversed(rows) if any(x and x in text for x in r["text"].split("\n"))), None)

    def around(self, channel, message_id, at, before=6, after=6):
        """ある発言の前後の会話（評価の時に使う）。その日と前の日の原本から探す。"""
        day = datetime.fromisoformat(at)
        rows = []
        for d in (day - timedelta(days=1), day):
            p = self.vault.path(f"03 Logs/chat/{d:%Y-%m-%d}.jsonl")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    rows += [json.loads(x) for x in f if x.strip()]
        rows = [r for r in rows if r.get("channel") == channel]
        i = next((k for k, r in enumerate(rows) if str(r["id"]) == str(message_id)), None)
        if i is None:
            return []
        return rows[max(0, i - before):i + after + 1]

    def recent(self, channel, n, t):
        """起動した時に、直近の会話を原本から戻す。"""
        rows = []
        for d in (t - timedelta(days=1), t):
            p = self.vault.path(f"03 Logs/chat/{d:%Y-%m-%d}.jsonl")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    rows += [json.loads(x) for x in f if x.strip()]
        return [r for r in rows if r.get("channel") == channel][-n:]


class Conversation:
    """1つのチャンネルの直近の会話。"""

    def __init__(self, rows=()):
        self.recent = deque(rows, maxlen=RECENT)

    def add(self, m):
        self.recent.append(m)

    def last_id(self):
        return self.recent[-1]["id"] if self.recent else None

    def since(self, message_id):
        """message_id より後の発言。"""
        out, seen = [], message_id is None
        for m in self.recent:
            if seen:
                out.append(m)
            elif m["id"] == message_id:
                seen = True
        return out


def my_lines(convs, t):
    """自分が最近言ったこと（会話が流れても残す。minato 0.13.0）。"""
    lines = [m for c in convs for m in c.recent
             if m.get("me") and t - datetime.fromisoformat(m["at"]) < timedelta(hours=MY_LINES_HOURS)]
    lines.sort(key=lambda m: m["at"])
    return [m["text"] for m in lines[-MY_LINES_MAX:]]
