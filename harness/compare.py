"""影の返事を、本番の Bot の返事と並べて比べる用のチャンネルへ出す。

    -# [本番の Bot の文章](発言の URL)
    このエージェントの文章

config.yaml の compare_with: {id: 本番の Bot の ID, name: 呼び名} があれば、この形で出す。
同じ発言への返事を待ち合わせ、片方が来なければ「黙っていた」と出す。本番の Bot の分割は1通目だけを見る。
"""
import re
from datetime import timedelta

WAIT_THEIRS = timedelta(minutes=3)    # こちらが書いてから、本番の Bot を待つ
WAIT_MINE = timedelta(minutes=6)      # 本番の Bot が返してから、こちらを待つ（気付くまでの間の分だけ長い）
KEEP_DONE = timedelta(minutes=30)     # 出し終えた発言を覚えておく（遅れて書き終えた返事の二重の黙りを防ぐ）
TEXT_MAX = 100


def line(text):
    """-# の行に入るように、返信の前置きを除いて1行にする。"""
    text = re.sub(r"^（[^）]*(返信|宛て)）\s*", "", text or "")
    text = re.sub(r"\s+", " ", text).replace("[", "［").replace("]", "］").strip()
    return text[:TEXT_MAX] or "（本文なし）"


class Compare:
    def __init__(self, agent):
        self.agent = agent
        self.open, self.done = {}, {}          # 返す相手の発言の id -> 待ち合わせ

    @property
    def rival(self):
        return self.agent.config.get("compare_with") or {}

    def _entry(self, target, t):
        return self.open.setdefault(str(target["id"]), {"target": target, "theirs": None, "mine": None, "at": t})

    async def theirs(self, m, recent, t):
        """本番の Bot の発言を受ける。返信ならその相手、違えば直前の人の発言への返事と読む。"""
        target = next((x for x in reversed(recent) if str(x["id"]) == str(m.get("reply_to"))), None)
        target = target or next((x for x in reversed(recent) if not x.get("bot")), None)
        if target is None or str(target["id"]) in self.done:
            return
        e = self._entry(target, t)
        if e["theirs"] is None:
            e["theirs"], e["at"] = m, t
        if e["mine"]:
            await self.post(self.open.pop(str(target["id"])))

    async def mine(self, it, body, t):
        """こちらの返事を受ける。本番の Bot の返事が既にあれば、すぐ出す。"""
        tid = str(it.target["id"])
        if tid in self.done:
            await self.post({**self.done.pop(tid), "mine": body})
            return
        e = self._entry(it.target, t)
        e["mine"], e["at"] = body, t
        if e["theirs"]:
            await self.post(self.open.pop(tid))

    async def tick(self, t):
        for tid, e in list(self.open.items()):
            if t - e["at"] >= (WAIT_THEIRS if e["mine"] else WAIT_MINE):
                await self.post(self.open.pop(tid))
        self.done = {k: e for k, e in self.done.items() if t - e["at"] < KEEP_DONE}

    async def post(self, e):
        self.done[str(e["target"]["id"])] = e
        name = self.rival.get("name", "本番")
        if e["theirs"]:
            head = f"-# [{line(e['theirs']['text'])}](<{e['theirs'].get('url')}>)"
        else:
            head = f"-# {name}は黙っていた（[返信先の発言](<{e['target'].get('url')}>)）"
        me = self.agent.about
        await self.agent.sender.send(str(self.agent.config.get("compare_channel")),
                                     [f"{head}\n{e['mine'] or f'（{me}は黙った）'}"], reply_to=None, typing=[0])
