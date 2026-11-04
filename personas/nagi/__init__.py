"""ナギ（湊 凪）の部品。規則の純関数は minato の `engine/` をそのまま移したもの（`personas/nagi/engine/`）。

原型の時点で乗せたもの：体（眠気、空腹、気力、財布）、予定から決まる今の様子、返す型の規則、抜ける・戻る一言、
送る前の検査、送った返事から「今やったこと」を Jev で読む（会話用 LLM には書かせない）。
まだ乗せていないもの：気分と話題の好き嫌い（mood、topics、policy）、好感度（affinity）、怒り（anger）、呼び名（names）。
規則とテストは移してあり、差し込み口（read_questions／on_read、choose）に繋ぐのが次の作業。

予定は `Agents/SCHEDULE.md` から読み、minato の時間割（decide.TIMETABLE）に流し込む。名前に「バイト」を含む予定をバイトとして扱う。
臨時の予定は、本体の【この先1週間の予定】には出るが、体の規則（講義中・バイト中）にはまだ効かない。
"""
import random
import re
from datetime import time

from harness.api import choice, noul
from harness.persona import Persona, Presence
from personas.nagi.engine import body, decide

ANNOUNCE = {"寝た": "眠気が限界なので、今から寝る。会話から抜ける一言だけ",
            "落ちる": "人と話すのがしんどくなったので、少し会話から抜ける。重くならない一言だけ",
            "コンビニに行く": "腹が減ったので、今からコンビニに行く。一言だけ",
            "戻った": "コンビニから戻った／少し休んで戻った。一言だけ"}
SCHED_END = {"講義中": "講義が終わった", "バイト中": "バイトが終わった"}
POLITE = re.compile(r"(です|ます|ました|ません|でした|ください|でしょう)[ねよかがけど]?(?=[。！？!?…、~〜\s]|$)")
CURT = re.compile(r"^(うる(さい|せ[えー]?)|うっせ[えー]?|別に|やめろ|知らん|は[?？]?)[。、!！?？wｗ…\s]*$")
YEN = re.compile(r"(\d[\d,]*)\s*円")


def _hm(minutes):
    return time(23, 59) if minutes >= 24 * 60 else time(minutes // 60, minutes % 60)


class Nagi(Persona):
    def __init__(self, agent):
        super().__init__(agent)
        self.body_state = self.state("body", body.new(agent.now()))
        self.mem = self.state("nagi", {})

    @property
    def b(self):
        return self.body_state.data

    def apply_schedule(self):
        """SCHEDULE.md の毎週の予定を、minato の時間割の形に流し込む（読み直された時もここで効く）。"""
        s = self.agent.schedule()
        table, baito_days, baito = {}, [], None
        for wd, items in s.weekly.items():
            for x in items:
                if "バイト" in x.name:
                    baito_days.append(wd)
                    baito = (_hm(x.start), _hm(x.end))
                else:
                    table.setdefault(wd, []).append((_hm(x.start), _hm(x.end), x.name))
        if s.weekly:
            decide.TIMETABLE, decide.BAITO_DAYS = table, tuple(sorted(set(baito_days)))
            if baito:
                decide.BAITO = baito

    def tick(self, t):
        self.apply_schedule()
        sched = decide.schedule(t)
        on, at = decide.alarm(t)
        ev = body.tick(self.b, t, sched, on, at)
        last = self.mem.data.get("last_sched")
        if last == "バイト中" and sched != "バイト中":
            ev += body.after_baito(self.b, t)
        if last in SCHED_END and sched != last:
            ev.append(SCHED_END[last])
        self.mem.data["last_sched"] = sched
        self.mem.touch()
        self.body_state.touch()
        return ev

    def announce(self, event):
        return ANNOUNCE.get(event)

    def presence(self, t):
        self.apply_schedule()
        act = decide.activity(t, self.b)
        st = act["state"]
        return Presence(state=st, label=act["label"], device=act["device"],
                        busy=st in ("講義中", "バイト中"), away=st in ("寝ている", "離席中"),
                        rate=0.4 if st == "バイト中" else 1.0)

    def choose(self, scene):
        pres, ans = scene["presence"], scene.get("answers", {})
        return decide.choose(scene["addressed"], ans.get("interest"), scene["share"], scene["humans"], pres.state,
                             random.random(), self.b.get("energy", 70))

    def inner(self, t, target):
        return {"体の状態": f"{body.as_feelings(self.b, t)}／財布: {body.wallet(self.b, t)}"}

    def guards(self):
        out = [POLITE, CURT]
        names = self.names()
        if names:
            out.append(re.compile(f"(?:{names.pattern})(?:のこと|は|が|も|的に)", re.I))   # 自分を名前で呼ばない
        return out

    # 送った返事を Jev が読む（minato は会話用 LLM に返事の JSON で did を書かせていた）
    def sent_questions(self, texts):
        qs = {"did": noul("【あなた】の発言は、話し手がこの会話の直前に、実際に新しく何かをした（買った、食べた、遊んだ、"
                          "どこかへ行った）と言っているか。普段の習慣、一般論、これからの予定、昔の話は当てはまらない。"),
              "ate": noul("【あなた】の発言は、話し手が今、何かを食べた・飲んだと言っているか。")}
        yens = sorted({int(m.group(1).replace(",", "")) for m in YEN.finditer(" ".join(texts))})
        if yens:
            qs["yen"] = choice("【あなた】の発言で、話し手が今使ったと言っている金額はどれか。",
                               {str(y): f"{y}円" for y in yens} | {"なし": "金額は言っていない"})
        return qs

    def on_sent(self, texts, answers):
        if answers.get("did", 0) < decide.INTEREST_TRUE:
            return
        y = answers.get("yen", ("なし",))[0]
        t = self.agent.now()
        ev = body.did(self.b, t, " ".join(texts)[:30], int(y) if y.isdigit() else 0,
                      answers.get("ate", 0) >= decide.INTEREST_TRUE)
        if ev:
            self.agent.note_event(t, ev)
            self.body_state.touch()


PERSONA = Nagi
