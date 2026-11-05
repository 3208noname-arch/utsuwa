"""人格ごとの部品の差し込み口。本体は人格の部品を、このクラスの形でだけ呼ぶ。

どの差し込み口も省略できる（体も気分も持たない人格でも動く）。
ナギの体、気分、怒り、好感度、混ざり方の予想は `personas/nagi/` がこれを継いで書く。

- 部品は数値の状態を持ってよい。置き場は `self.state(名前)`（`02 State/<名前>.json`）
- 会話用 LLM に渡すのは `inner()` が言葉に直した節だけ。数値は渡さない（minato 2026-10-08 の比較）
"""
import re
from dataclasses import dataclass


@dataclass
class Presence:
    state: str = "ふつう"       # ふつう／寝ている／離席中／予定中 など。本体が見るのは busy と away だけ
    label: str = "ふつう"       # 会話用 LLM に渡す【今の様子】
    device: str = "スマホ"      # 打つ速さに使う
    busy: bool = False         # 予定の最中（呼ばれたら遅れて一言だけ返す）
    away: bool = False         # 寝ている・離席中（呼ばれた分は後で返す）
    rate: float = 1.0          # 混ざる確率の倍率


class Persona:
    def __init__(self, agent):
        self.agent = agent      # 本体。config、schedule、vault、state を借りる

    def state(self, name, default=None):
        return self.agent.state(name, default)

    # --- 呼ばれ方 -----------------------------------------------------------
    def names(self):
        """この語が出たら自分が呼ばれた、とみなす正規表現。config.yaml の names から作る。
        names_regex があればそちらを使う（「なぎ」は呼ぶが「つなぎ」は呼ばない、のような除外を書く時）。"""
        c = self.agent.config
        if c.get("names_regex"):
            return re.compile(c["names_regex"], re.I)
        words = c.get("names") or []
        return re.compile("|".join(map(re.escape, words)), re.I) if words else None

    # --- 時間で進むもの -------------------------------------------------------
    def tick(self, t):
        """1分ごとに呼ぶ。起きた出来事（「講義が終わった」など）の言葉のリストを返す。"""
        return []

    def announce(self, event):
        """出来事のうち、会話から抜ける・戻る一言を添えるもの。添えるなら会話用 LLM への【補足】、添えないなら None。"""
        return None

    def presence(self, t):
        """今の様子。既定は予定（SCHEDULE.md）だけで決める。"""
        item = self.agent.schedule().current(t)
        if item:
            return Presence(state="予定中", label=f"{item.name}の最中", busy=True, rate=0.4)
        return Presence()

    # --- 判断 ---------------------------------------------------------------
    def jev_questions(self, scene):
        """判断の時の Jev の1回に足す問い（{名前: noul(...)}）。答えは choose() の scene["answers"] に入る。"""
        return {}

    def choose(self, scene):
        """返す型を決める。None なら本体の既定の規則で決める。"none"／"reaction"／"short"／"normal"／"defer"。"""
        return None

    # --- 書く -----------------------------------------------------------------
    def inner(self, t, target):
        """会話用 LLM に渡す節 {節名: 文}。節名は AGENTS.md に書いてあるものだけを使う。"""
        return {}

    def guards(self, target=None):
        """送る前に弾く正規表現のリスト。1通でも当たった案は使わない。target は返す相手の発言（無ければ None）。"""
        return []

    def typing_sec(self, text, device):
        if device == "スマホ":
            return max(2.0, min(15.0, len(text) * 0.4))
        return max(1.0, min(6.0, len(text) * 0.15))

    # --- 読んだ後・送った後 -----------------------------------------------------
    def wants_read(self, m, recent):
        """自分宛てでも、自分が混ざっている会話でもない発言を、後で読みたいか（好きな相手への言動など）。"""
        return False

    def read_context(self, item):
        """後で読む時の state の頭に足す行（【発言者】【相手】など）。"""
        return ""

    def read_questions(self, item):
        """後で読む時の Jev の1回に足す問い（好感度、怒りと謝り など）。"""
        return {}

    def on_read(self, item, answers):
        """後で読んだ結果を受け取る。"""

    def sent_questions(self, texts):
        """送った返事を Jev が読む問い（今やったこと、口にした好き嫌い など）。会話用 LLM には書かせない。"""
        return {}

    def on_sent(self, texts, answers):
        """送った返事を Jev が読んだ結果を受け取る。"""

    def on_delivered(self, intent, out):
        """実際に送った（文章でもリアクションでも）。影のチャンネルでは呼ばれない。"""
