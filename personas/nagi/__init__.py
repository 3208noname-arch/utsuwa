"""ナギ（湊 凪）の部品。規則の純関数は minato の `engine/` をそのまま移したもの（`personas/nagi/engine/`）。

乗せたもの
- 体（眠気、空腹、気力、財布）と、予定から決まる今の様子。返す型の規則
- 気分（居心地）：体で基準線が決まり、人の言動と話題で動き、時間で戻る。会話用 LLM には言葉で渡す
- 話題の好き嫌い：判断の時の Jev 1回で、会話がどの話題かを読み、快で気分を動かす。話題の後の経験で図を育てる
- 混ざり方を居心地の予想で選ぶ（決まりごとに当たらない時）。混ざった後の人の反応で予想を直す
- 好感度、怒りと謝り、呼び名：後で読む時の Jev 1回で読む（本体の「記録するか」と同じ呼び出し）
- 送った返事を Jev が読む：今やったこと、奢ってもらったこと、口にした好き嫌い（会話用 LLM には書かせない）

状態は `02 State/` に部品ごとに置く（body、mood、likes、anger、names、topics、policy、nagi）。

予定は `Agents/SCHEDULE.md` から読む（decide.PLAN）。名前に「バイト」を含む予定をバイトとして扱う。
決まりの値（怒りの増え方、好感度の増減など）は `config.yaml` の `rules:` で上書きできる（rules.py）。
臨時の予定（休み、代わりのシフト）も、体の規則（講義中・バイト中）に効く。
"""
import logging
import random
import re
from datetime import datetime, timedelta

from harness.api import choice, noul
from harness.persona import Persona, Presence
from personas.nagi.engine import affinity, anger, body, decide, mood, names, policy, topics
from personas.nagi.rules import apply as apply_rules

log = logging.getLogger("utsuwa")

MOOD_EVERY, MOOD_KEEP = timedelta(minutes=10), timedelta(hours=48)
DOING = {"寝ている": "寝てる", "起きたて": "起きたて", "講義中": "講義中", "バイト中": "バイト中", "離席中": "席を外してる",
         "深夜": "夜ふかし中", "ふつう": "起きてる"}
ANNOUNCE = {"寝た": "眠気が限界なので、今から寝る。会話から抜ける一言だけ",
            "落ちる": "人と話すのがしんどくなったので、少し会話から抜ける。重くならない一言だけ",
            "コンビニに行く": "腹が減ったので、今からコンビニに行く。一言だけ",
            "戻った": "コンビニから戻った／少し休んで戻った。一言だけ"}
SCHED_END = {"講義中": "講義が終わった", "バイト中": "バイトが終わった"}
POLITE = re.compile(r"(です|ます|ました|ません|でした|ください|でしょう)[ねよかがけど]?(?=[。！？!?…、~〜\s]|$)")
CURT = re.compile(r"^(うる(さい|せ[えー]?)|うっせ[えー]?|別に|やめろ|知らん|は[?？]?)[。、!！?？wｗ…\s]*$")
YEN = re.compile(r"(\d[\d,]*)\s*円")
SPLIT = re.compile(r"[\s、。,.!！?？「」『』（）()…・~〜wｗ]+")
TOPIC_AGAIN = timedelta(minutes=30)     # 同じ話題で続けて気分を動かさない
PEOPLE_KEEP = timedelta(hours=2)
SETTLE = affinity.AFTER_WAIT            # 枠が終わってから、遅れて届く判定を待つ時間
NAME_CANDIDATES = 12

# 後で読む時の問い（minato の app/learning.py と同じ文面。相手の名前だけ外から入れる）
LIKE_QS = {"attack": "発言者は【相手】を本気でけなしている、または喧嘩を売っているか（冗談やノリの軽いいじりは含めない）。",
           "tease": "発言者は【相手】を冗談やノリで軽くいじっているか。",
           "support": "発言者は【相手】を本心から庇っている、褒めている、または気にかけているか"
                      "（冗談の前振り・お世辞・皮肉は含めない）。",
           "joke": "発言者は【相手】に、冗談・ボケ・ノリで言っているか"
                   "（わざと怪しい話を持ちかける、罠にかける前振りも含む。その後の会話で冗談だと分かるものも含む）。",
           "belittle": "発言者は【相手】を見下したり、子ども扱い・バカ扱いしたりしているか"
                       "（ふざけた調子や「かわいい」を添えた言い方でも含む）。"}
APOLOGY_QS = {"apology": "発言者は【相手】に、自分の言ったことやしたことを謝っているか。",
              "light": "発言者の謝り方は、軽い・ふざけた・形だけのものか（「ごめんw」「すまんすまん」「はいはいごめんね」など）。"
                       "本気で反省して謝っているなら当てはまらない。"}


TAIL = re.compile(r"(さぁ|さあ|ってば|って|さ|は|が|も|に|の|と|へ|や|よ|ね|な)+$")
RUN = re.compile(r"[ァ-ヴー]{2,}|[一-龥々]{1,4}|[A-Za-z][A-Za-z0-9_]{1,}")


def name_candidates(text):
    """呼び名の候補。LLM に作らせず、本文から拾って Jev に選ばせる。
    区切りごとの語、その語の末尾の助詞を外したもの、カタカナ・漢字・英字の続き（敬称は外す）。"""
    out = []
    for seg in SPLIT.split(text):
        for raw in [TAIL.sub("", seg), *RUN.findall(seg), seg]:
            form = names.clean(raw)
            if form and 1 <= len(form) <= names.MAX_LEN and names.valid(form, text) and form not in out:
                out.append(form)
    return out[:NAME_CANDIDATES]


class Nagi(Persona):
    def __init__(self, agent):
        super().__init__(agent)
        self._rules = None
        t = agent.now()
        self.body_state = self.state("body", body.new(t))
        self.mood_state = self.state("mood", mood.new(t))
        self.likes = self.state("likes", {})
        self.anger_state = self.state("anger", {})
        self.names_state = self.state("names", {})
        self.topics_state = self.state("topics", {"topics": []})
        self.policy_state = self.state("policy", {"q": {}, "pending": []})
        self.mem = self.state("nagi", {"episodes": [], "people_log": []})

    @property
    def b(self):
        return self.body_state.data

    @property
    def m(self):
        return self.mood_state.data

    @property
    def who(self):
        c = self.agent.config
        return f"{c.get('name', 'ナギ')}（{c.get('nick', 'N4Gi')}）"

    # --- 予定と体 --------------------------------------------------------------------
    def refresh(self):
        """SCHEDULE.md の予定と config.yaml の rules を、規則に流し込む（読み直された時もここで効く）。"""
        decide.PLAN = self.agent.schedule()
        rules = self.agent.config.get("rules") or {}
        if rules != self._rules:
            self._rules = rules
            for w in apply_rules(rules):
                log.warning("config.yaml の rules: %s", w)

    def tick(self, t):
        self.refresh()
        sched = decide.schedule(t)
        on, at = decide.alarm(t)
        ev = body.tick(self.b, t, sched, on, at)
        last = self.mem.data.get("last_sched")
        if last == "バイト中" and sched != "バイト中":
            ev += body.after_baito(self.b, t)
        if last in SCHED_END and sched != last:
            ev.append(SCHED_END[last])
        self.mem.data["last_sched"] = sched
        self.mood_settle(t)
        self.keep_mood(t)
        self.close_episodes(t)
        self.close_policy(t)
        for s in (self.body_state, self.mood_state, self.mem):
            s.touch()
        return ev

    def announce(self, event):
        return ANNOUNCE.get(event)

    def presence(self, t):
        self.refresh()
        act = decide.activity(t, self.b)
        st = act["state"]
        return Presence(state=st, label=act["label"], device=act["device"],
                        busy=st in ("講義中", "バイト中"), away=st in ("寝ている", "離席中"),
                        rate=0.4 if st == "バイト中" else 1.0)

    # --- Web UI ----------------------------------------------------------------------------
    def view(self, t):
        p = self.presence(t)
        base = self.mood_base(t)
        m = dict(self.m, events=list(self.m["events"]))     # 読むだけ。今の時刻まで戻した値を見せる
        mood.settle(m, t, base)
        words = mood.as_words(m, base)
        reason = re.search(r"（(.*)）$", words)
        feel = body.as_feelings(self.b, t)
        feel = "" if feel.startswith("特に無し") else "、" + feel
        return {
            "say": f"{DOING.get(p.state, p.label)}。機嫌は{re.sub(r'（.*）$', '', words)}{feel}。",
            "why": f"気分 {m['v']:+.0f}（体から来る基準 {base:+.0f}）" + (f"。理由: {reason[1]}" if reason else ""),
            "device": p.device,
            "meters": [{"name": "眠気", "value": round(body.felt_sleepy(self.b, t)), "max": 100},
                       {"name": "空腹", "value": round(self.b["hunger"]), "max": 100},
                       {"name": "気力", "value": round(self.b["energy"]), "max": 100},
                       {"name": "残高", "value": self.b["money"], "unit": "円"}],
            "trend": {"hist": self.mem.data.get("mood_hist", []), "events": list(reversed(m["events"])), "band": 14,
                      "legend": "直近24時間の快・不快。上が快、下が不快。薄い帯が「ふつう」の範囲。点は気分を動かした出来事"},
            "people": [self.view_likes(t), self.view_anger(t), self.view_names()],
        }

    def view_likes(self, t):
        items = [{"name": p.get("display"), "tag": affinity.band(p.get("v", 0)), "value": f"{p.get('v', 0):+.1f}",
                  "tone": "pos" if p.get("v", 0) >= 15 else "neg" if p.get("v", 0) <= -15 else "",
                  "events": [{"when": affinity.when(datetime.fromisoformat(e["at"]), t), "what": e["what"],
                              "quote": e.get("quote"), "delta": e["delta"]} for e in reversed(p.get("events", []))]}
                 for p in sorted(self.likes.data.values(), key=lambda x: -x.get("v", 0))]
        return {"name": "好感度", "lead": "−100〜+100。最初は+20。名前を押すと、動いた出来事が出る",
                "empty": "まだ誰も覚えていない", "items": items}

    def view_anger(self, t):
        items = []
        for uid, r in self.anger_state.data.items():
            close = self.likes.data.get(uid, {}).get("v", 0) >= anger.CLOSE_FROM
            v = anger.cool(dict(r), t, close)
            if v >= anger.FORGET:
                items.append({"name": r.get("display"), "tag": anger.band(v), "value": round(v), "tone": "neg",
                              "note": f"「{r.get('why')}」と言われた"})
        return {"name": "怒り", "lead": "今怒っている相手。時間が経つと冷める", "empty": "今は誰にも怒っていない",
                "items": sorted(items, key=lambda x: -x["value"])}

    def view_names(self):
        items = [{"name": p.get("display"),
                  "note": "、".join(f"{k} {f['n']}回" for k, f in
                                   sorted(p.get("forms", {}).items(), key=lambda x: -x[1]["n"]))}
                 for p in self.names_state.data.values()]
        return {"name": "呼び名", "lead": "誰がどう呼ばれているか", "empty": "まだ無い", "items": items}

    # --- 気分 ----------------------------------------------------------------------------
    def mood_base(self, t):
        return mood.baseline(body.felt_sleepy(self.b, t), self.b["hunger"], self.b["energy"], self.b["money"] < body.BROKE)

    def mood_settle(self, t):
        base = self.mood_base(t)
        mood.settle(self.m, t, base)
        return base

    def keep_mood(self, t):
        """Web UI のグラフ用に、気分を10分ごとに残す。"""
        hist = self.mem.data.setdefault("mood_hist", [])
        if hist and t - datetime.fromisoformat(hist[-1][0]) < MOOD_EVERY:
            return
        hist.append([t.isoformat(timespec="seconds"), round(self.m["v"], 1)])
        self.mem.data["mood_hist"] = [x for x in hist if t - datetime.fromisoformat(x[0]) <= MOOD_KEEP]

    def mood_hit(self, delta, why, people=True, said_at=None):
        """気分を動かす。people は人の言動によるものか。人の分は、経験と予想の答えに使う。"""
        if not delta:
            return
        t = self.agent.now()
        self.mood_settle(t)
        d = mood.nudge(self.m, delta, t, why)
        if d and people:
            keep = [x for x in self.mem.data.get("people_log", []) if t - datetime.fromisoformat(x[0]) <= PEOPLE_KEEP]
            self.mem.data["people_log"] = keep + [[(said_at or t).isoformat(timespec="seconds"), round(d, 1)]]
        self.mood_state.touch()
        self.mem.touch()

    def people_felt(self, start, minutes):
        end = start + timedelta(minutes=minutes)
        return round(sum(d for at, d in self.mem.data.get("people_log", [])
                         if start <= datetime.fromisoformat(at) <= end), 1)

    # --- 話題の好き嫌いと、その後の経験 ------------------------------------------------------
    @property
    def topic_list(self):
        return self.topics_state.data.setdefault("topics", [])

    def close_episodes(self, t):
        eps = self.mem.data.get("episodes", [])
        done = [e for e in eps if t - datetime.fromisoformat(e["at"]) >= timedelta(minutes=topics.EPISODE_MIN) + SETTLE]
        if not done:
            return
        self.mem.data["episodes"] = [e for e in eps if e not in done]
        for e in done:
            tp = next((x for x in self.topic_list if x["name"] == e["name"]), None)
            if tp:
                topics.experience(tp, e["joined"], self.people_felt(datetime.fromisoformat(e["at"]), topics.EPISODE_MIN))
        self.topics_state.touch()

    # --- 混ざり方を居心地の予想で選ぶ -------------------------------------------------------
    def close_policy(self, t):
        pend = self.policy_state.data.setdefault("pending", [])
        done = [p for p in pend if t - datetime.fromisoformat(p["at"]) >= timedelta(minutes=policy.WINDOW_MIN) + SETTLE]
        if not done:
            return
        self.policy_state.data["pending"] = [p for p in pend if p not in done]
        for p in done:
            policy.update(self.policy_state.data.setdefault("q", {}), p["kind"],
                          self.people_felt(datetime.fromisoformat(p["at"]), policy.WINDOW_MIN))
        self.policy_state.touch()

    def jev_questions(self, scene):
        """判断の時の Jev 1回に、話題の問いを足す（会話の最後の発言が、どの話題そのものか）。"""
        tps = topics.active(self.topic_list, scene.get("convo", ""))
        return {f"topic:{tp['name']}": noul(f"会話の最後の発言は「{tp['name']}」そのものについての話か"
                                            f"（人や物事を{tp['name']}にたとえる比喩は含めない）。") for tp in tps}

    def choose(self, scene):
        t = self.agent.now()
        ans, pres, target = scene.get("answers", {}), scene["presence"], scene["target"]
        probs = {k[len("topic:"):]: v for k, v in ans.items() if k.startswith("topic:")}
        hit = mood.topic_hit(probs, self.topic_list) if probs else None
        tp = None
        if hit:
            name, d = hit
            tp = next(x for x in self.topic_list if x["name"] == name)
            eps = self.mem.data.setdefault("episodes", [])
            if not any(e["name"] == name for e in eps):
                eps.append({"name": name, "at": t.isoformat(timespec="seconds"), "joined": False})
            why = f"{name}の話になった"
            if not any(e["why"] == why and t - datetime.fromisoformat(e["at"]) < TOPIC_AGAIN for e in self.m["events"]):
                self.mood_hit(d, why, people=False)
        self.mem.data.pop("comfort", None)
        if decide.free_choice(scene["addressed"], scene["share"], scene["humans"], pres.state, self.b.get("energy", 70)):
            if tp:
                heat, valence = policy.heat_of(tp["arousal"]), tp["valence"]
            else:
                interest = ans.get("interest")
                heat, valence = (1.0 if interest is not None and interest >= decide.INTEREST_TRUE else 0.0), 0.0
            self.mood_settle(t)
            grudge = self.anger_of(target["author_id"]) * anger.POLICY_PER
            lg = policy.logits(heat, valence, self.m["v"], self.policy_state.data.setdefault("q", {}), grudge)
            kind = policy.pick(lg, random.random())
            self.mem.data["comfort"] = kind          # 混ざったら、後で人の反応で予想を直す
            return kind
        return decide.choose(scene["addressed"], ans.get("interest"), scene["share"], scene["humans"], pres.state,
                             random.random(), self.b.get("energy", 70))

    def on_delivered(self, intent, out):
        for e in self.mem.data.get("episodes", []):
            e["joined"] = True
        kind = self.mem.data.pop("comfort", None)
        if kind and intent.source == "reply":
            self.policy_state.data.setdefault("pending", []).append(
                {"kind": kind, "at": self.agent.now().isoformat(timespec="seconds")})
            self.policy_state.touch()
        if isinstance(out, list):
            body.spend_talk(self.b, sum(map(len, out)), self.agent.now())
            self.body_state.touch()
        self.mem.touch()

    # --- 書く時に渡す節（数値ではなく言葉） ---------------------------------------------------
    def inner(self, t, target):
        s = {"体の状態": f"{body.as_feelings(self.b, t)}／財布: {body.wallet(self.b, t)}",
             "気分": mood.as_words(self.m, self.mood_settle(t))}
        convo = "\n".join(x["text"] for x in list(self.agent.conv(target["channel"]).recent)[-6:]) if target else ""
        taste = topics.as_words(topics.active(self.topic_list, convo))
        if taste:
            s["自分の好き嫌い"] = taste
        if target:
            v = self.anger_of(target["author_id"])
            if v:
                s["返信先への怒り"] = anger.as_words(self.anger_state.data[str(target["author_id"])], v)
        return s

    def guards(self, target=None):
        out = [POLITE]
        if not (target and self.anger_of(target["author_id"]) >= anger.CURT_OK_FROM):
            out.append(CURT)      # 怒っている相手には、短い突き放しの一言も許す
        nm = self.names()
        if nm:
            out.append(re.compile(f"(?:{nm.pattern})(?:のこと|は|が|も|的に)", re.I))   # 自分を名前で呼ばない
        return out

    # --- 好感度と怒り ------------------------------------------------------------------------
    def is_close(self, uid):
        return self.likes.data.get(str(uid), {}).get("v", 0) >= anger.CLOSE_FROM

    def anger_of(self, uid):
        return anger.level(self.anger_state.data, uid, self.agent.now(), self.is_close(uid)) if uid else 0.0

    def _to_me(self, m):
        return bool(m.get("to_me") or m.get("mentions_me") or not m.get("target_author"))

    def wants_read(self, m, recent):
        """ナギが好きな（または苦手な）相手に向けた発言は、ナギ宛てでなくても読む（第三者への言動で好感度が動く）。"""
        tid = m.get("target_id")
        if not tid or m.get("to_me"):
            return False
        v = self.likes.data.get(str(tid), {}).get("v", affinity.INITIAL)
        return affinity.relation(v) is not None

    def read_context(self, item):
        m = item["message"]
        to = self.who if self._to_me(m) else m["target_author"]
        return f"【発言者】{m['author']}\n【相手】{to}\n"

    def read_questions(self, item):
        m = item["message"]
        me = self._to_me(m)
        qs = {k: noul(v) for k, v in LIKE_QS.items()}
        if me and not (m.get("to_me") or m.get("mentions_me")):
            qs["about_me"] = noul(f"【読む発言】は、{self.who}に向けた、または{self.who}のことを言った発言か"
                                  "（ほかの人どうしのやりとりなら含めない）。")
        if not me and m.get("mentions_me"):
            qs["me"] = noul(f"発言者は、この会話の中で{self.who}のことを本心から庇っている、褒めている、または気にかけているか。")
        if me and self.anger_of(m["author_id"]):
            qs.update({k: noul(v) for k, v in APOLOGY_QS.items()})
        if not me:
            cands = name_candidates(m["text"])
            nm = self.names()
            cands = [c for c in cands if not (nm and nm.search(c))]
            if cands:
                qs["name"] = choice(f"発言者は【相手】（{m['target_author']}）を、どの呼び名で呼んだり指したりしているか。",
                                    {c: f"「{c}」と呼んでいる" for c in cands} | {"なし": "呼び名では呼んでいない"})
        return qs

    def on_read(self, item, p):
        if not p:
            return
        m = item["message"]
        t = self.agent.now()
        me = self._to_me(m)
        if "about_me" in p and p["about_me"] < affinity.TRUE:
            return                      # ナギ宛てではなかった（人どうしのやりとり）
        self.learn_names(m, p)
        store = self.likes.data
        name, quote = m["author"], m["text"]
        said = datetime.fromisoformat(m["at"])
        author = affinity.get(store, m["author_id"], name, t)
        kind = affinity.judge(p.get("attack"), p.get("tease"), p.get("support"), p.get("joke"), p.get("belittle"))
        if me:
            if kind in ("attack", "belittle"):
                add = anger.hit(self.anger_state.data, m["author_id"], name, kind, quote, t, self.is_close(m["author_id"]))
                if add:
                    self.anger_state.touch()
            if "apology" in p:
                cut = anger.apologize(self.anger_state.data, m["author_id"], p.get("apology"), p.get("light"), t,
                                      self.is_close(m["author_id"]))
                if cut:
                    self.anger_state.touch()
                    self.mood_hit(cut / 8, f"{name}に謝られた", said_at=said)
            d = affinity.apply(author, affinity.DELTA[(kind, "me")], t, f"ナギに{kind}", quote) if kind else 0
            if kind:
                self.mood_hit(mood.people_delta(kind, "me"), mood.people_reason(kind, name), said_at=said)
            else:
                self.mood_hit(mood.chat_delta(author["v"]), f"{name}に話しかけられた", said_at=said)
                affinity.chat(author, t)
        else:
            tgt = affinity.get(store, m["target_id"], m["target_author"], t)
            rel = affinity.relation(tgt["v"])
            if (kind, rel) in affinity.DELTA:
                affinity.apply(author, affinity.DELTA[(kind, rel)], t, f"{m['target_author']}に{kind}", quote)
                self.mood_hit(mood.people_delta(kind, rel), mood.people_reason(kind, m["target_author"], by=name),
                              said_at=said)
            if p.get("me", 0) >= affinity.TRUE:
                affinity.apply(author, affinity.DELTA[("support", "me")], t, "ナギを庇った", quote)
                self.mood_hit(mood.people_delta("support", "me"), mood.people_reason("support", name), said_at=said)
        self.likes.touch()

    def learn_names(self, m, p):
        """呼び名を数える。ナギの名前は正規表現で、ほかの人の呼び名は本文の候補から Jev が選んだもの。"""
        t, nm = self.agent.now(), self.names()
        hit = nm.search(m["text"]) if nm else None
        if hit:
            form = names.clean(names.around(m["text"], hit.start(), hit.end()))
            if names.valid(form, m["text"]):
                names.record(self.names_state.data, "me", self.agent.config.get("nick", "N4Gi"), form, m["author_id"], t)
                self.names_state.touch()
        c = p.get("name")
        if c and c[0] != "なし" and m.get("target_id") and not m.get("to_me"):
            names.record(self.names_state.data, m["target_id"], m["target_author"], c[0], m["author_id"], t)
            self.names_state.touch()

    # --- 送った返事を Jev が読む（会話用 LLM には帳簿を付けさせない）----------------------------------
    def sent_questions(self, texts):
        joined = " ".join(texts)
        qs = {"did": noul("【あなた】の発言は、話し手がこの会話の直前に、実際に新しく何かをした（買った、食べた、遊んだ、"
                          "どこかへ行った）と言っているか。普段の習慣、一般論、これからの予定、昔の話は当てはまらない。"),
              "ate": noul("【あなた】の発言は、話し手が今、何かを食べた・飲んだと言っているか。"),
              "treated": noul("【あなた】の発言は、話し手が会話の中で誰かに食べ物や飲み物を奢ってもらって受け取ったと言っているか。"),
              "like": noul("【あなた】の発言で、話し手は何かを好きだと言っているか（「〜は好き」「〜いいよな」）。"),
              "dislike": noul("【あなた】の発言で、話し手は何かを嫌い・苦手だと言っているか（「〜無理」「あんまり行かない」）。")}
        yens = sorted({int(x.group(1).replace(",", "")) for x in YEN.finditer(joined)})
        if yens:
            qs["yen"] = choice("【あなた】の発言で、話し手が今使ったと言っている金額はどれか。",
                               {str(y): f"{y}円" for y in yens} | {"なし": "金額は言っていない"})
        cands = [c for c in dict.fromkeys(SPLIT.split(joined)) if 1 < len(c) <= 12][:NAME_CANDIDATES]
        if cands:
            qs["topic"] = choice("【あなた】の発言で、話し手が好き嫌いを言っている対象はどれか。",
                                 {c: f"「{c}」" for c in cands} | {"なし": "好き嫌いは言っていない"})
        speakers = sorted({x["author"] for c in self.agent.convs.values() for x in list(c.recent)[-12:]
                           if not x.get("me") and not x.get("bot")})
        if speakers:
            qs["by"] = choice("話し手に奢ってくれたのは誰か。", {s: f"{s}が奢った" for s in speakers} | {"なし": "奢ってもらっていない"})
        return qs

    def on_sent(self, texts, a):
        t = self.agent.now()
        if a.get("did", 0) >= decide.INTEREST_TRUE:
            y = a.get("yen", ("なし",))[0]
            ate = a.get("ate", 0) >= decide.INTEREST_TRUE
            by = a.get("by", ("なし",))[0] if ate and a.get("treated", 0) >= decide.INTEREST_TRUE else "なし"
            ev = body.did(self.b, t, " ".join(texts)[:30], 0 if by != "なし" else (int(y) if y.isdigit() else 0), ate,
                          treat=by != "なし")
            if ev:
                if by != "なし":
                    ev = f"{by}に{ev}を奢ってもらった"
                    self.mood_hit(mood.people_delta("treat", "me"), mood.people_reason("treat", by))
                    uid = next((u for u, rec in self.likes.data.items() if rec.get("display") == by), None)
                    if uid:
                        affinity.apply(affinity.get(self.likes.data, uid, by, t), affinity.DELTA[("treat", "me")], t,
                                       "ナギに奢った", " ".join(texts)[:40])
                        self.likes.touch()
                self.agent.note_event(t, ev)
                self.body_state.touch()
        like, dislike = a.get("like", 0), a.get("dislike", 0)
        topic = a.get("topic", ("なし",))[0]
        if topic != "なし" and max(like, dislike) >= decide.INTEREST_TRUE:
            topics.learn(self.topic_list, topic, round(like - dislike, 2), t.isoformat(timespec="seconds"))
            self.topics_state.touch()


PERSONA = Nagi
