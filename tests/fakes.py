"""テスト用の偽物。本物の Jev、LLM、Discord と同じ形で答える。"""
import asyncio
import os
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "template", "あかり")


def copy_template():
    """見本のエージェントのフォルダを、使い捨ての場所に写す。"""
    d = tempfile.mkdtemp(prefix="utsuwa-")
    dst = os.path.join(d, "あかり")
    shutil.copytree(TEMPLATE, dst)
    return dst


class FakeJev:
    """rule(state, name, question) が答え（Noul なら確率、Choice なら選択肢の名前）を返す。None は棄権。"""

    def __init__(self, rule=None):
        self.rule = rule or (lambda state, name, q: None)
        self.calls = []

    async def ask(self, state, questions):
        self.calls.append((state, questions))
        out = {}
        for name, q in questions.items():
            a = self.rule(state, name, q)
            if a is None:
                continue
            if q["type"] == "choice":
                out[name] = (a, {c: (1.0 if c == a else 0.0) for c in q["criteria"]})
            else:
                out[name] = float(a)
        return out


class FakeLLM:
    """responses を順に返す。関数なら fn(messages) の戻り値。"""

    def __init__(self, responses, delays=()):
        self.responses = responses
        self.delays = list(delays)     # 呼ばれた順に、返すまでの秒（書いている最中に会話を進めるため）
        self.calls = []

    async def chat(self, messages, temperature=1.0):
        self.calls.append(messages)
        if self.delays:
            await asyncio.sleep(self.delays.pop(0))
        if callable(self.responses):
            return self.responses(messages)
        return self.responses.pop(0) if self.responses else None


class FakeSender:
    """送った物を残し、自分の発言として本体に返す（Discord が自分の発言も届けるのと同じ）。"""

    def __init__(self):
        self.sent, self.reactions, self.notes = [], [], []
        self.agent = None
        self.n = 0

    async def send(self, channel, texts, reply_to=None, typing=()):
        self.sent.append((channel, list(texts), reply_to))
        if self.agent and self.agent.channel_mode(channel) == "live":
            for t in texts:
                self.n += 1
                await self.agent.on_message(msg(f"me{self.n}", channel, "Akari", t, me=True, bot=True,
                                                at=self.agent.now()))

    async def react(self, channel, message_id, emoji):
        self.reactions.append((channel, message_id, emoji))

    async def notify(self, text):
        self.notes.append(text)


def msg(id, channel, author, text, at, me=False, bot=False, **kw):
    return {"id": id, "at": at.isoformat(timespec="seconds"), "channel": channel, "author": author,
            "author_id": "akari" if me else f"u-{author}", "bot": bot, "me": me, "text": text, **kw}
