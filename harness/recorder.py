"""記録① 記録するか（Jev）と、記録② 記録用 LLM が書く。

- 種類を決めるのは Jev（Choice）。記録用 LLM は選ばない。`Agents/RECORDER.md` だけを読んで、渡された種類として書く
- **エージェント自身の発言は記録の元にしない**（作り話が事実として定着し、次の作り話の種になる）
- スマホのメモの本文は LLM に書かせない。送られてきた発言をコードがそのまま写し、LLM は名前と思い出す言葉だけ
- 反省は、同じ振る舞いへの指摘が既にあれば、新しく作らずに `再発` に日付を足す
- 評価から作る記憶は、評価の前後の会話で同じ話をしていれば「会話で教わった」ことにする
- 人名はコードが印 `[[<id>|表示名]]` にして渡す（harness/who.py）。発言者は author_id から引く。書いた後、知らない ID の印は外す。
  教わった人は印で、関係する人は ID で持つ。思い出す言葉に人名は入れない（調味料のポン酢の話で人を思い出さないように）
"""
import logging
from datetime import datetime

from harness import saver, who
from harness.api import choice, loads, noul
from harness.context import lesson_rows

log = logging.getLogger("utsuwa")

NONE = "しない"
SAME_MIN = 0.8        # 同じ事柄・同じ振る舞いへの指摘とみなす閾値
TALK_MIN = 0.7        # 評価と同じ話を会話でしているとみなす閾値


def record_question(kinds, about):
    crit = {k: v.get("about", k) for k, v in kinds.items()}
    crit[NONE] = "覚えるほどのことではない（ただの感想、相づち、流れの指摘）"
    return choice(f"【読む発言】は、{about}が記憶として残すべきものか。残すならどの種類か。", crit)


def speaker(m, mark=False):
    """会話の行の発言者。mark なら人名の印（記録用 LLM に渡す形）。"""
    if m.get("me"):
        return "あなた"
    return who.link(m.get("author_id"), m["author"]) if mark else m["author"]


def convo_lines(rows, mark=False):
    return "\n".join(f"{speaker(m, mark)}: {m['text']}" for m in rows)


def person(m):
    """発言から (ID, 表示名)。"""
    return (str(m.get("author_id") or ""), m.get("author") or "")


def drop_names(words, names):
    """思い出す言葉から人名（台帳の名前・印）を落とす。"""
    keys = {saver.norm(n) for n in names if n}
    return [w for w in words if saver.norm(w) not in keys and not who.ids(w)]


async def find_same(jev, vault, kind, body, question):
    """同じ種類の現役の記憶から、body と同じ事柄のものを Jev で探す。rel か None。"""
    rows = [r for r in lesson_rows(vault) if r.kind == kind]
    if not rows:
        return None
    state = (f"【新しい記憶】{who.plain(body)}\n【今ある記憶】\n" +
             "\n".join(f"{i}. {r.name}: {who.plain(r.body)}" for i, r in enumerate(rows)))
    a = await jev.ask(state, {f"r{i}": noul(f"今ある記憶の {i} 番は、新しい記憶と{question}") for i in range(len(rows))})
    best = max(range(len(rows)), key=lambda i: a.get(f"r{i}", 0))
    return rows[best].rel if a.get(f"r{best}", 0) >= SAME_MIN else None


class Recorder:
    def __init__(self, agent):
        self.agent = agent

    @property
    def kinds(self):
        return self.agent.config.get("kinds") or {}

    async def write(self, kind, said, context, people, source, day, scene_hint=""):
        """記録用 LLM に書かせて保存する。rel か None。
        context は印の付いた会話（convo_lines(..., mark=True)）、people は教わった人の [(ID, 表示名)]。"""
        a = self.agent
        names = a.whois.current()
        user = who.rename(f"【種類】{kind}\n【会話】\n{context}\n【発言】{said}", names)
        text, _ = await a.llm.chat([{"role": "system", "content": a.vault.text("Agents/RECORDER.md")},
                                 {"role": "user", "content": user}], temperature=0)
        d = loads(text)
        known = who.ids(user) | set(names) | {u for u, _ in people if u}
        d, dropped = who.check_value(d, known)
        if dropped:
            log.info("知らない ID の印を外した（%s）: %s", kind, dropped)
        name = who.plain(d.get("name") or "", names).strip()
        body = said.strip() if kind == "スマホのメモ" else str(d.get("body") or "").strip()
        if not name or not body:
            log.info("記録用 LLM が書けなかった（%s）: %r", kind, (text or "")[:80])
            return None
        if kind == "反省":
            same = await find_same(a.jev, a.vault, kind, body, "同じ振る舞いへの指摘か。")
            if same:
                saver.add_recurrence(a.vault, same, day)
                log.info("同じ反省があるので再発に足した: %s", same)
                return same
        if source == "評価":
            same = await find_same(a.jev, a.vault, kind, body, "同じ事柄について述べているか。")
            if same and a.vault.note(same).meta.get("出どころ") == "会話":
                log.info("同じことを会話で教わっているので、評価からは足さない: %s", same)
                return None
        words = saver.keep_words(d.get("keywords"), body, said, context)
        words = drop_names(words, a.whois.all_names() | {n for _, n in people} | set(names.values()))
        taught = [who.link(u, names.get(u) or n) for u, n in people]
        uids = list(dict.fromkeys([u for u, _ in people if u] + sorted(who.ids(body))))
        meta = saver.meta_for(kind, day, taught, source, words, str(d.get("scene") or scene_hint), uids)
        return saver.save(a.vault, self.kinds, kind, name, body, meta)

    async def from_talk(self, item, answer):
        """後で読んだ発言を記録する。answer は record_question の答え（(種類, 確率)）。"""
        m = item["message"]
        if not answer or m.get("me") or m.get("bot"):
            return None
        kind = answer[0]
        if kind == NONE or kind not in self.kinds:
            return None
        ctx = convo_lines(item["before"] + [m] + item["after"], mark=True)
        day = datetime.fromisoformat(m["at"]).date()
        return await self.write(kind, m["text"], ctx, [person(m)], "会話", day)

    async def from_rating(self, rec, around):
        """評価コメントから記憶を作る。around は評価された発言の前後の会話（原本）。"""
        a = self.agent
        if not rec.get("comment") or not rec.get("is_me"):
            return None
        kinds = {k: v for k, v in self.kinds.items() if k in ("常識", "反省", "その他")}
        crit = {k: v.get("about", k) for k, v in kinds.items()}
        crit[NONE] = "どれでもない（ただの感想、流れの指摘）"
        speakers = sorted({m["author"] for m in around if not m.get("me") and not m.get("bot")})
        state = f"【会話】\n{convo_lines(around)}\n【評価された発言】{rec['text']}\n【指摘】{rec['comment']}"
        qs = {"kind": choice("【指摘】は、どの種類の記憶として残すべきか。", crit),
              "talk": noul("【指摘】と同じ内容の話を、【会話】の中で誰かがしているか。")}
        if speakers:
            qs["who"] = choice("【指摘】と同じ内容の話を【会話】の中でしたのは誰か。",
                               {s: f"{s}の発言" for s in speakers} | {"いない": "会話の中では誰も言っていない"})
        ans = await a.jev.ask(state, qs)
        kind = ans.get("kind", (NONE,))[0]
        if kind == NONE:
            return None
        talked = ans.get("talk", 0) >= TALK_MIN
        by = ans.get("who", ("いない",))[0] if talked else "いない"
        if by != "いない":
            m = next((x for x in reversed(around) if x.get("author") == by and not x.get("me")), {"author": by})
            people, source = [person(m)], "会話"
        else:
            people, source = [(str(rec.get("rater_id") or ""), rec["rater"])], "評価"
        day = datetime.fromisoformat(rec["at"]).date()
        log.info("評価から記憶（%s・%s）: %s", kind, source, rec["comment"][:40])
        return await self.write(kind, rec["comment"], convo_lines(around, mark=True), people, source, day)
