"""言いたいことのキューと、外へ出す窓口。

喋る入口（返事、後で返す、放置を拾う、自分から話題、抜ける一言）は多いままにし、どれも「言いたいこと」を積むだけにする。
書いて送るのは窓口だけで、1回に1つだけ選ぶ。重複を省く、古くなったら捨てる、をここ1か所でやる
（minato は入口ごとに送っていて、二重返事の対策を入口ごとに足していた）。
積むのは文章ではなく「言いたいこと」だけなので、積む数が増えても LLM の呼び出しは増えない。
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# 急ぎの順。同じ発言への言いたいことが重なったら、高い方を残す
PRIORITY = {"late": 50, "reply": 40, "pickup": 20, "announce": 15, "start": 10}
TTL = {"late": timedelta(hours=12), "reply": timedelta(minutes=10), "pickup": timedelta(minutes=30),
       "announce": timedelta(minutes=5), "start": timedelta(minutes=10)}
ANSWERED_KEEP = 50


@dataclass
class Intent:
    source: str                    # late／reply／pickup／announce／start
    channel: str
    kind: str                      # reaction／short／normal
    at: datetime
    target: dict = None            # 返す相手の発言。独り言なら None
    note: str = ""                 # 会話用 LLM への【補足】
    addressed: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def priority(self):
        return PRIORITY.get(self.source, 0)

    def key(self):
        return (self.channel, str(self.target["id"])) if self.target else (self.channel, self.source)

    def alive(self, t):
        return t - self.at < TTL.get(self.source, timedelta(minutes=10))


class Outbox:
    def __init__(self):
        self.items = {}
        self.answered = []      # 返事済みの発言ID（同じ発言に二度返さない）

    def add(self, intent):
        if intent.target and str(intent.target["id"]) in self.answered:
            return False
        old = self.items.get(intent.key())
        if old and old.priority >= intent.priority:
            return False
        self.items[intent.key()] = intent
        return True

    def take(self, t):
        """窓口が次に出す1つ。古いものは捨てる。無ければ None。"""
        for k in [k for k, v in self.items.items() if not v.alive(t)]:
            del self.items[k]
        if not self.items:
            return None
        best = max(self.items.values(), key=lambda v: (v.priority, -v.at.timestamp()))
        del self.items[best.key()]
        return best

    def mark(self, target_id):
        self.answered = (self.answered + [str(target_id)])[-ANSWERED_KEEP:]
        for k in [k for k, v in self.items.items() if v.target and str(v.target["id"]) == str(target_id)]:
            del self.items[k]

    def __len__(self):
        return len(self.items)
