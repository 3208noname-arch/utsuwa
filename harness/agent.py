"""本体。1人のエージェントのフォルダを読み、発言を受けて、判断し、書き、送り、記録する。

Discord には依存しない。発言は dict で受け取り、送るのは sender（gateway.py か、テストの偽物）に任せる。

返事の流れ
  ①判断する（規則＋Jev 1回）→ 言いたいことのキューに積む
  窓口が1つ選ぶ → ②文脈を組む → ③書く（2〜3案）→ ④検査して1案を選ぶ
  **書くのは、気付くまでの間を待つ間に済ませる。**その間に会話が進んだら、Jev が「通す、書き直す、黙る」を選ぶ
記録の流れ
  発言の後の6件か10分を待ち、前後と一緒に Jev 1回で読む（記録するか、人格の部品の問い）→ 記録用 LLM → 保存
"""
import asyncio
import importlib
import json
import logging
import os
import random
from collections import deque
from datetime import datetime, timedelta

import yaml

from harness import context, night, writer
from harness.api import choice, noul
from harness.outbox import Intent, Outbox
from harness.persona import Persona
from harness.recorder import Recorder, record_question
from harness.schedule import Schedule, ago, now_text
from harness.store import Conversation, Logs, State, my_lines

log = logging.getLogger("utsuwa")

QUIET_SEC = 6            # 最後の発言からこれだけ静かになったら判断する
MAX_WAIT_SEC = 20        # 会話が続いていても、これだけ溜めたら判断する
INTEREST_TRUE = 0.70
SHARE_SLACK = 1.5        # 「参加人数分の1」の何倍まで喋ってよいか
SHARE_WINDOW = 12
USER_DAILY_MAX, TOTAL_DAILY_MAX = 30, 150
PENDING_TTL = timedelta(minutes=3)    # 気付くまでの間の後、これ以上かかったら捨てる（Jev が失敗した時の歯止め）
STALE = timedelta(hours=2)            # 直近の会話がこれより古ければ「もう終わっている」と添える
READ_AFTER_N, READ_AFTER = 6, timedelta(minutes=10)   # 後で読むのは、後の6件か10分
NEAR_N = 10              # 自分が直近これだけの発言の中で喋っていれば「混ざっている会話」
PICKUP_AFTER, PICKUP_UNTIL, PICKUP_RATE = timedelta(minutes=3), timedelta(minutes=30), 0.6
START_QUIET, START_ALIVE, START_RATE, STARTS_PER_DAY = timedelta(minutes=90), timedelta(hours=24), 1 / 45, 2
DEFER_KEEP = 5

RECHECK = {"通す": "書いた案は、新しい発言の後でもそのまま通じる。少し遅れた返事として自然",
           "書き直す": "返す値打ちはあるが、書いた案は新しい発言とずれている",
           "黙る": "もう返すきっかけが流れた。返すと場違いになる"}


def day_key(t):
    return (t - timedelta(hours=4)).date().isoformat()


def load_persona(name):
    """config.yaml の persona（personas/ の下のフォルダ名）から、人格の部品のクラス。無ければ何も持たない部品。"""
    if not name:
        return Persona
    return importlib.import_module(f"personas.{name}").PERSONA


class Agent:
    def __init__(self, vault, jev, llm, sender, persona_cls=None, clock=datetime.now, notice=None):
        self.vault, self.jev, self.llm, self.sender = vault, jev, llm, sender
        self.now = clock
        self.notice = notice           # テストで気付くまでの間を差し替える（秒を返す関数）
        self.quiet_sec = QUIET_SEC
        self.logs = Logs(vault)
        self._states = {}
        self.h = self.state("harness", {})
        self.convs = {}
        self.batches, self.batch_tasks, self.last_at = {}, {}, {}
        self.outbox = Outbox()
        self.outbox.answered = self.h.data.get("answered", [])
        self.reading = deque()
        self.pending = {}              # channel -> 書いている最中の返事（会話が進んだかの確かめ用）
        self.window_task = None
        self.recorder = Recorder(self)
        self.persona = (persona_cls or load_persona(self.config.get("persona")))(self)
        self.vault.changes()           # 書き換えの検知の起点

    # --- フォルダから読むもの（書き換わったら読み直される）------------------------
    @property
    def config(self):
        return self.vault.load("Agents/config.yaml", yaml.safe_load) or {}

    def schedule(self):
        return self.vault.load("Agents/SCHEDULE.md", Schedule.parse) or Schedule()

    def state(self, name, default=None):
        if name not in self._states:
            self._states[name] = State(self.vault, name, default)
        return self._states[name]

    def channel_mode(self, channel):
        for c in self.config.get("channels") or []:
            if str(c.get("id")) == str(channel):
                return c.get("mode", "live")
        return None

    def conv(self, channel):
        if channel not in self.convs:
            self.convs[channel] = Conversation(self.logs.recent(channel, 40, self.now()))
        return self.convs[channel]

    @property
    def about(self):
        return self.config.get("name", "あなた")

    def notify(self, text):
        log.info("知らせ: %s", text)
        if hasattr(self.sender, "notify"):
            asyncio.ensure_future(self.sender.notify(text))

    # --- 発言を受ける -----------------------------------------------------------
    async def on_message(self, m):
        ch = str(m["channel"])
        mode = self.channel_mode(ch)
        if mode is None or mode == "compare":
            return
        names = self.persona.names()
        m.setdefault("mentions_me", bool(names and names.search(m.get("text", ""))))
        conv = self.conv(ch)
        conv.add(m)
        self.logs.chat(m)
        for it in self.reading:
            if it["channel"] == ch and it["message"]["id"] != m["id"]:
                it["after"].append(m)
        p = self.pending.get(ch)
        if p and not m.get("me"):
            p.new_message()
        if m.get("bot") or m.get("me"):
            return
        recent = list(conv.recent)
        if (m.get("mentions_me") or m.get("to_me") or any(x.get("me") for x in recent[-NEAR_N:])
                or self.persona.wants_read(m, recent)):
            self.reading.append({"channel": ch, "message": m, "before": recent[-7:-1], "after": [], "at": self.now()})
        self.batches.setdefault(ch, []).append(m)
        loop = asyncio.get_running_loop()
        self.last_at[ch] = loop.time()      # 静けさは実際に経った秒で数える（会話の時刻とは別）
        task = self.batch_tasks.get(ch)
        if task is None or task.done():
            self.batch_tasks[ch] = asyncio.ensure_future(self._wait_and_judge(ch, loop.time()))

    async def _wait_and_judge(self, ch, started):
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(min(1.0, self.quiet_sec / 6))
            t = loop.time()
            if t - self.last_at[ch] >= self.quiet_sec or t - started >= MAX_WAIT_SEC:
                break
        batch, self.batches[ch] = self.batches.get(ch, []), []
        try:
            await self.judge(ch, batch)
        except Exception:
            log.exception("判断に失敗")

    # --- ①判断する ---------------------------------------------------------------
    def _budget_ok(self, uid, t):
        c = self.h.data.setdefault("counts", {})
        if c.get("day") != day_key(t):
            return True
        return c.get("total", 0) < TOTAL_DAILY_MAX and c.get("users", {}).get(str(uid), 0) < USER_DAILY_MAX

    def _count(self, uid, t):
        c = self.h.data.setdefault("counts", {})
        if c.get("day") != day_key(t):
            c.clear()
            c.update({"day": day_key(t), "total": 0, "users": {}})
        c["total"] += 1
        c["users"][str(uid)] = c["users"].get(str(uid), 0) + 1
        self.h.touch()

    @staticmethod
    def share(recent):
        """直近の発言のうち自分の割合と、人間の人数。連投は1回と数える。"""
        turns, prev = [], None
        for m in recent:
            who = "me" if m.get("me") else m["author_id"]
            if who != prev:
                turns.append((who, m.get("bot") and not m.get("me")))
            prev = who
        mine = sum(1 for w, _ in turns if w == "me")
        humans = len({w for w, bot in turns if w != "me" and not bot})
        return (mine / len(turns) if turns else 0.0), humans

    def default_choose(self, addressed, interest, share, humans, pres, r):
        if pres.away:
            return "defer" if addressed else "none"
        if addressed:
            return "short" if pres.busy or r < 0.35 else "normal"
        if share > SHARE_SLACK / (humans + 1):
            return "none"
        r = r / max(pres.rate, 0.01)
        if interest is not None and interest >= INTEREST_TRUE:
            table = [("normal", 0.30), ("short", 0.25), ("reaction", 0.20)]
        else:
            table = [("short", 0.10), ("reaction", 0.15)]
        acc = 0.0
        for kind, p in table:
            acc += p
            if r < acc:
                return kind
        return "none"

    async def judge(self, ch, batch):
        if not batch or self.h.data.get("stopped"):
            return
        t = self.now()
        target = next((m for m in reversed(batch) if m.get("mentions_me") or m.get("to_me")), batch[-1])
        addressed = bool(target.get("mentions_me") or target.get("to_me"))
        if not self._budget_ok(target["author_id"], t):
            log.info("上限に達したので黙る")
            return
        pres = self.persona.presence(t)
        recent = list(self.conv(ch).recent)
        share, humans = self.share(recent[-SHARE_WINDOW:])
        convo = "\n".join(f"{m['author']}: {m['text']}" for m in recent[-6:])
        scene = {"addressed": addressed, "target": target, "presence": pres, "share": share, "humans": humans,
                 "convo": convo}
        qs = dict(self.persona.jev_questions(scene))
        cfg = self.config.get("jev") or {}
        if not addressed and not pres.away:
            if cfg.get("interest"):
                qs["interest"] = noul(cfg["interest"])
            qs["invite"] = noul("会話の最後の発言は、特定の相手ではなく、その場にいる誰かに話しかけて、"
                                "話し相手や反応を求めているか（例: 誰か話そう、暇な人いる？、ねえ聞いて）。")
        ans = await self.jev.ask(convo, qs) if qs else {}
        if ans.get("invite", 0) >= INTEREST_TRUE:
            addressed = scene["addressed"] = True
        scene["answers"] = ans
        kind = self.persona.choose(scene) or self.default_choose(addressed, ans.get("interest"), share, humans, pres,
                                                                  random.random())
        log.info("判断: 宛先=%s 関心=%s シェア=%.2f 人数=%d 様子=%s → %s", addressed, ans.get("interest"), share, humans,
                 pres.state, kind)
        if kind == "defer":
            d = self.h.data.setdefault("deferred", [])
            d.append({"channel": ch, "target": target, "at": t.isoformat(), "was": pres.state})
            self.h.data["deferred"] = d[-DEFER_KEEP:]
            self.h.touch()
            return
        if kind in ("none", None):
            return
        self.offer(Intent("reply", ch, kind, t, target=target, addressed=addressed))

    # --- 窓口 ------------------------------------------------------------------------
    def offer(self, intent):
        """入口から言いたいことを積む。窓口が空いていれば、すぐに出す。"""
        if self.outbox.add(intent):
            log.info("言いたいことを積んだ（%s・%s）", intent.source, intent.kind)
        if self.window_task is None or self.window_task.done():
            self.window_task = asyncio.ensure_future(self._window())

    async def _window(self):
        while True:
            it = self.outbox.take(self.now())
            if it is None:
                return
            try:
                await self.handle(it)
            except Exception:
                log.exception("返事に失敗")

    def notice_sec(self, pres, addressed):
        """気付くまでの間（minato の decide.notice_sec に、呼ばれた時の早さを足した）。"""
        if self.notice:
            return self.notice(pres, addressed)
        if pres.busy:
            return random.uniform(60, 360)
        r = random.random()
        if r < (0.9 if addressed else 0.7):      # 呼ばれたら早めに気付く
            return random.uniform(5, 40)
        if r < 0.95 or addressed:
            return random.uniform(60, 300)
        return random.uniform(300, 900)

    def wait_sec(self, it, pres, t):
        """書いたものを送るまでに待つ秒数。窓口で待った分は、気付くまでの間に含める。"""
        if it.source in ("late",):
            return 0
        return max(0, self.notice_sec(pres, it.addressed) - (t - it.at).total_seconds())

    async def handle(self, it):
        ch, t = it.channel, self.now()
        pres = self.persona.presence(t)
        p = Pending(self, it)
        self.pending[ch] = p
        try:
            wait = self.wait_sec(it, pres, t)
            p.start()
            await asyncio.sleep(wait)
            t0 = self.now()        # 気付くまでの間は、書く時間に数えない
            out = await p.result()
            if out is not None and self.now() - t0 > PENDING_TTL:
                log.info("書くのに時間がかかりすぎたので捨てる")
                out = None
        finally:
            self.pending.pop(ch, None)
        if out is None:
            return
        await self.deliver(it, out, pres)

    async def deliver(self, it, out, pres):
        t, ch = self.now(), it.channel
        mode = self.channel_mode(ch)
        if it.target:
            self.outbox.mark(it.target["id"])
            self.h.data["answered"] = self.outbox.answered
            self._count(it.target["author_id"], t)
        self.h.touch()
        if mode == "shadow":
            where = self.config.get("compare_channel")
            head = f"（{it.target['author']}「{it.target['text'][:40]}」に）" if it.target else f"（{it.source}）"
            body = out if isinstance(out, str) else "／".join(out)
            if where:
                await self.sender.send(str(where), [head + body], reply_to=None, typing=[0])
            log.info("影: %s%s", head, body)
            return
        if isinstance(out, str):
            await self.sender.react(ch, it.target["id"], out)
            self.persona.on_delivered(it, out)
            return
        moved = it.target and self.conv(ch).last_id() != it.target["id"]
        reply_to = it.target["id"] if it.target and (moved or it.addressed) else None
        await self.sender.send(ch, out, reply_to=reply_to, typing=[self.persona.typing_sec(x, pres.device) for x in out])
        self.persona.on_delivered(it, out)
        qs = self.persona.sent_questions(out)
        if qs:
            convo = "\n".join(f"{m['author']}: {m['text']}" for m in list(self.conv(ch).recent)[-6:])
            ans = await self.jev.ask(convo + "\n" + "\n".join(f"あなた: {x}" for x in out), qs)
            self.persona.on_sent(out, ans)

    # --- ②③④ 文脈を組んで書く -----------------------------------------------------------
    def sections(self, it, t):
        conv = self.conv(it.channel)
        rows = list(conv.recent)
        upto = it.extra.get("upto")
        if upto is not None:
            # 書き始めた時点の会話だけを使う（その後に来た発言は、通す・書き直す・黙るの判断で見る）
            ids = [m["id"] for m in rows]
            if upto in ids:
                rows = rows[:ids.index(upto) + 1]
        lines = "\n".join(writer.line(m, t) for m in rows)
        if rows and t - datetime.fromisoformat(rows[-1]["at"]) >= STALE:
            lines += f"\n（直近の会話は{ago((t - datetime.fromisoformat(rows[-1]['at'])).total_seconds())}で、もう終わっている）"
        s = {"今": now_text(t), "この先1週間の予定": self.schedule().week_text(t), "直近の会話": lines,
             "返信先": writer.line(it.target, t) if it.target else "なし（誰かの発言への返事ではない）",
             "今の様子": self.persona.presence(t).label}
        events = self.recent_events(t)
        if events:
            s["最近の出来事"] = "\n".join(events)
        if self.config.get("nick"):
            s["呼ばれ方"] = f"このサーバーでのあなたの表示名は {self.config['nick']}"
        s.update(self.persona.inner(t, it.target))
        return s, lines

    async def compose(self, it):
        t = self.now()
        s, lines = self.sections(it, t)
        mem, _, stats = await context.select(self.jev, self.vault, lines, self.about, t.date(),
                                             {k: v.get("about", "") for k, v in (self.config.get("kinds") or {}).items()})
        s.update(mem)
        mine = my_lines(self.convs.values(), t)
        s["あなたが最近言ったこと"] = "\n".join(mine) or "（無し）"
        if it.note:
            s["補足"] = it.note
        log.info("節の字数: %s／記憶 %s", {k: len(v) for k, v in s.items()}, stats)
        return await writer.write(self.llm, self.jev, self.vault, self.about, s, it.kind, self.persona.guards(it.target),
                                  self.config.get("reactions") or (), mine, lines)

    async def recheck(self, it, start_lines, new, draft, allow_rewrite=True):
        """書いている間に会話が進んだ。通す／書き直す／黙る を Jev が選ぶ。Jev が失敗したら黙る。"""
        crit = {k: v for k, v in RECHECK.items() if allow_rewrite or k != "書き直す"}
        t = self.now()
        body = draft if isinstance(draft, str) else "\n".join(draft)
        state = (f"【書き始めた時点の会話】\n{start_lines}\n【その後に来た発言】\n" +
                 "\n".join(writer.line(m, t) for m in new) + f"\n【書いた返事の案】\n{body}")
        a = await self.jev.ask(state, {"v": choice(f"{self.about}が書いた返事の案を、新しい発言の後に送るか。", crit)})
        v = a.get("v", (None,))[0]
        log.info("書いている間に会話が進んだ（%d件）→ %s", len(new), v or "Jev失敗のため黙る")
        return v

    # --- 時間で進むもの ---------------------------------------------------------------
    async def tick(self):
        """1分ごとに呼ぶ。"""
        t = self.now()
        for rel in self.vault.changes():
            log.info("Obsidian で書き換わった: %s（次に読む時から効く）", rel)
        for ev in self.persona.tick(t):
            self.note_event(t, ev)
            note = self.persona.announce(ev) if hasattr(self.persona, "announce") else None
            if note:
                for ch in self.live_channels():
                    self.offer(Intent("announce", ch, "short", t, note=note))
        self.take_requests(t)
        await self.persona.slow(t)
        self.release_deferred(t)
        for ch in self.live_channels():
            self.pickup(ch, t)
            self.start_topic(ch, t)
        await self.read_due(t)
        hour = (self.config.get("night") or {}).get("hour", 4)
        if t.hour == hour and self.h.data.get("night") != t.date().isoformat():
            self.h.data["night"] = t.date().isoformat()
            self.h.touch()
            try:
                await night.run(self, t)
            except Exception:
                log.exception("夜の整理に失敗")
        self.flush(t)

    def take_requests(self, t):
        """Web UI の変更依頼を受ける。受けたらすぐファイルへ書く（UI はファイルを読むので）。"""
        rels = self.vault.names("02 State/requests", ".json")
        for rel in rels:
            try:
                self.persona.on_request(self.vault.json(rel), t)
            except Exception:
                log.exception("依頼に失敗: %s", rel)
            self.vault.remove(rel)
        if rels:
            self.flush(t, force=True)

    def flush(self, t, force=False):
        for s in self._states.values():
            s.flush(t, force)

    def live_channels(self):
        return [str(c["id"]) for c in self.config.get("channels") or [] if c.get("mode", "live") in ("live", "shadow")]

    def note_event(self, t, what):
        days = self.h.data.setdefault("events", {})
        days.setdefault(t.date().isoformat(), []).append(f"{t:%H:%M} {what}")
        for k in sorted(days)[:-3]:
            del days[k]
        self.h.touch()

    def recent_events(self, t, n=8):
        """今日と昨日にしたこと（体の出来事、送った返事を Jev が読んだもの）。会話用 LLM の【最近の出来事】。"""
        days = self.h.data.get("events", {})
        out = []
        for d, label in (((t - timedelta(days=1)).date(), "昨日"), (t.date(), "今日")):
            out += [f"{label} {e}" for e in days.get(d.isoformat(), [])]
        return out[-n:]

    def release_deferred(self, t):
        d = self.h.data.get("deferred") or []
        if not d or self.persona.presence(t).away:
            return
        alive = [x for x in d if t - datetime.fromisoformat(x["at"]) < timedelta(hours=12)]
        self.h.data["deferred"] = []
        self.h.touch()
        if alive:
            x = alive[-1]
            mins = int((t - datetime.fromisoformat(x["at"])).total_seconds() // 60)
            note = f"この発言は約{mins}分前のもの。あなたは見ていなかった（{x['was']}）。周りはそれを知らない"
            self.offer(Intent("late", x["channel"], "normal", t, target=x["target"], note=note, addressed=True))

    def pickup(self, ch, t):
        rows = list(self.conv(ch).recent)
        if not rows:
            return
        last = rows[-1]
        if last.get("me") or last.get("bot") or last.get("to_other") or self.h.data.get("picked") == last["id"]:
            return
        age = t - datetime.fromisoformat(last["at"])
        pres = self.persona.presence(t)
        if not (PICKUP_AFTER <= age < PICKUP_UNTIL) or pres.away or pres.busy:
            return
        self.h.data["picked"] = last["id"]
        self.h.touch()
        if random.random() < PICKUP_RATE:
            self.offer(Intent("pickup", ch, "normal", t, target=last, addressed=True,
                              note="この発言には誰も反応していない。友達として拾って、話を広げる"))

    def start_topic(self, ch, t):
        rows = list(self.conv(ch).recent)
        if not rows or not (self.config.get("start_topics", True)):
            return
        st = self.h.data.setdefault("starts", {})
        if st.get("day") != day_key(t):
            st.update({"day": day_key(t), "n": 0})
        humans = [m for m in rows if not m.get("bot") and not m.get("me")]
        last_at = datetime.fromisoformat(rows[-1]["at"])
        pres = self.persona.presence(t)
        if (st["n"] >= STARTS_PER_DAY or t - last_at < START_QUIET or not humans
                or t - datetime.fromisoformat(humans[-1]["at"]) > START_ALIVE or pres.away or pres.busy
                or 2 <= t.hour < 11 or random.random() >= START_RATE):
            return
        st["n"] += 1
        self.h.touch()
        self.offer(Intent("start", ch, "normal", t, note="チャンネルがしばらく静か。誰かへの返事ではなく、自分から話題を1つ振る"))

    # --- 後で読む（記録と、人格の部品の学習）-------------------------------------------------
    async def read_due(self, t):
        while self.reading and (len(self.reading[0]["after"]) >= READ_AFTER_N or t - self.reading[0]["at"] >= READ_AFTER):
            it = self.reading.popleft()
            try:
                await self.read(it)
            except Exception:
                log.exception("後で読むのに失敗")

    async def read(self, it):
        m = it["message"]
        lines = "\n".join(f"{x['author']}: {x['text']}" for x in it["before"])
        after = "\n".join(f"{x['author']}: {x['text']}" for x in it["after"])
        state = (self.persona.read_context(it) +
                 f"【前の会話】\n{lines}\n【読む発言】{m['author']}: {m['text']}\n【後の会話】\n{after}")
        qs = dict(self.persona.read_questions(it))
        if self.config.get("kinds"):
            qs["record"] = record_question(self.config["kinds"], self.about)
        ans = await self.jev.ask(state, qs) if qs else {}
        self.persona.on_read(it, ans)
        await self.recorder.from_talk(it, ans.get("record"))

    # --- 評価 ------------------------------------------------------------------------
    async def rate(self, rec):
        """評価を1件受ける。rec = {message_id, channel, text, is_me, label, comment, rater, at}。"""
        around = self.logs.around(str(rec["channel"]), rec["message_id"], rec.get("msg_at") or rec["at"])
        rec = {**rec, "context": [f"{m['author']}: {m['text']}" for m in around]}
        self.logs.rating(rec)
        return await self.recorder.from_rating(rec, around)

    # --- 日記の材料 ----------------------------------------------------------------------
    def day_material(self, day):
        events = self.h.data.get("events", {}).get(day.isoformat(), [])
        learned = [r.body for r in context.lesson_rows(self.vault) if r.date == day.isoformat()]
        mine = []
        p = self.vault.path(f"03 Logs/chat/{day.isoformat()}.jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                mine = [json.loads(x)["text"] for x in f if x.strip() and json.loads(x).get("me")]
        return events, learned, mine[-30:]


class Pending:
    """書いている最中の返事。待つ間に会話が進んだら、裏で Jev に「通す、書き直す、黙る」を聞いておく。"""

    def __init__(self, agent, it):
        self.agent, self.it = agent, it
        conv = agent.conv(it.channel)
        self.start_id = conv.last_id()
        it.extra["upto"] = self.start_id
        self.start_lines = "\n".join(writer.line(m, agent.now()) for m in list(conv.recent))
        self.draft_task = None
        self.check_task, self.checked_n = None, 0
        self.rewrote = False

    def start(self):
        self.draft_task = asyncio.ensure_future(self.agent.compose(self.it))

    def _new(self):
        return [m for m in self.agent.conv(self.it.channel).since(self.start_id) if not m.get("me")]

    def new_message(self):
        """新しい発言が来た。前の確かめは捨て、最後の発言の後で聞き直す。"""
        if self.check_task and not self.check_task.done():
            self.check_task.cancel()
        self.checked_n = len(self._new())
        self.check_task = asyncio.ensure_future(self._check())

    async def _check(self):
        draft = await self.draft_task
        if draft is None:
            return None
        return await self.agent.recheck(self.it, self.start_lines, self._new(), draft, not self.rewrote)

    async def result(self):
        out = await self.draft_task
        while out is not None:
            new = self._new()
            if not new:
                return out
            if self.check_task is None or self.checked_n != len(new):
                self.new_message()
            try:
                v = await self.check_task
            except asyncio.CancelledError:
                continue
            if self.checked_n != len(self._new()):
                continue               # 確かめている間にまた来た。最後の発言の後の答えを採る
            if v == "通す":
                return out
            if v != "書き直す" or self.rewrote:
                return None
            self.rewrote = True        # 書き直しは1回まで。その後は通すか黙るかだけ
            conv = self.agent.conv(self.it.channel)
            self.start_id = conv.last_id()
            self.it.extra["upto"] = self.start_id
            self.start_lines = "\n".join(writer.line(m, self.agent.now()) for m in list(conv.recent))
            self.check_task, self.checked_n = None, 0
            self.draft_task = asyncio.ensure_future(self.agent.compose(self.it))
            out = await self.draft_task
        return None
