"""評価ログの再生: python -m harness.replay <エージェントのフォルダ> [--ratings 評価.jsonl] [--env .env] [--fake] [--diary]

想起（context.select）の良し悪しを数える物差し。評価（`03 Logs/ratings/*.jsonl`）の1件ごとに、
1. context（前後の会話）を会話文に組み直し、select に通す（Vault は渡されたフォルダを読む）
2. 「その失敗から後に作られた Lessons」を正解候補にする：
   評価の日付以降の date を持ち、評価のコメントか発言に 思い出す言葉 が含まれるもの
3. 選ばれた行に正解候補が入っていたか（ヒット／外れ／正解候補なし）を出す

日記は個人的な内容なので、既定では想起の材料に入れない（--diary で入れる）。正解候補にも日記は使わない。
鍵は context と同じく .env の config.yaml の keys.typesafe。--fake は FakeJev（何も選ばない）でのスモーク用。
"""
import argparse
import asyncio
import glob
import json
import os
import sys
from contextlib import contextmanager
from datetime import date

import yaml

from harness import context, fade
from harness.vault import Vault

HIT, MISS, NONE = "ヒット", "外れ", "正解候補なし"


def convo_text(rating):
    """評価に付いた context（1行ずつの会話）を、select に渡す会話文に戻す。"""
    return "\n".join(rating.get("context") or [])


def rating_day(rating):
    return date.fromisoformat(str(rating.get("at") or rating.get("msg_at"))[:10])


def candidates(rows, rating):
    """正解候補: 評価の日以降に作られ、思い出す言葉がコメントか発言に含まれる Lessons の行。"""
    day = rating_day(rating)
    hay = f"{rating.get('comment') or ''}\n{rating.get('text') or ''}"
    out = []
    for r in rows:
        try:
            made = date.fromisoformat(str(r.date)[:10])
        except ValueError:
            continue
        if made >= day and any(w and w in hay for w in r.words):
            out.append(r)
    return out


def judge(picked, cands):
    if not cands:
        return NONE, []
    want = {c.rel for c in cands}
    got = [r.rel for r in picked if r.rel in want]
    return (HIT if got else MISS), got


def total(results):
    """(ヒット数, 正解候補があった件数)。"""
    with_cand = [x for x in results if x["verdict"] != NONE]
    return sum(x["verdict"] == HIT for x in with_cand), len(with_cand)


def load_ratings(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@contextmanager
def _no_diary(off):
    saved = context.diary_rows
    if off:
        context.diary_rows = lambda vault, today: []
    try:
        yield
    finally:
        context.diary_rows = saved


class RecordingJev:
    """jev を包み、ask の問いと答えをそのまま記録する（select の挙動は変えない）。答えに無い問いは棄権。"""

    def __init__(self, jev):
        self.jev, self.calls = jev, []

    async def ask(self, state, questions):
        ans = await self.jev.ask(state, questions)
        self.calls.append((questions, ans))
        return ans


def probs_of(calls, kinds, cand_rows):
    """記録から確率を取り出す。None は棄権。呼び出しの順は select の流れ（種類 → 行）。"""
    ks = [c for c in calls if next(iter(c[0]), "").startswith("k")]
    rs = [c for c in calls if next(iter(c[0]), "").startswith("r")]
    a = ks[0][1] if ks else {}
    out = {"kinds": {k: a.get(f"k{i}") for i, k in enumerate(kinds)}, "rows": []}
    if rs:
        out["rows"] = [(r, rs[0][1].get(f"r{i}")) for i, r in enumerate(cand_rows)]
    return out


async def replay(jev, vault, ratings, about, kinds_about=None, kinds_when=None, probs=False, config=None):
    """config に recall.mode: human があれば、選ばれた行に薄れ層も通し、結果の "fade" に鮮明・曖昧・落とした行・痕跡の有無を入れる。
    picked（選ばれた行）は薄れ層の前の物で、exact の時と変わらない。"""
    rows = context.lesson_rows(vault)
    results = []
    for rt in ratings:
        rec = RecordingJev(jev) if probs else jev
        mem, picked, stats = await context.select(rec, vault, convo_text(rt), about, rating_day(rt), kinds_about,
                                                   kinds_when)
        mem, _, finfo = fade.apply(mem, picked, config or {}, rating_day(rt))
        cands = candidates(rows, rt)
        verdict, got = judge(picked, cands)
        results.append({"at": rt.get("at"), "label": rt.get("label"), "comment": rt.get("comment"),
                        "picked": [(r.name, r.kind) for r in picked], "candidates": [r.name for r in cands],
                        "got": got, "verdict": verdict, "stats": stats, "fade": finfo, "mem": mem})
        if probs:
            allrows = rows + context.diary_rows(vault, rating_day(rt))
            kinds = sorted({r.kind for r in allrows})
            wanted = set(stats.get("kinds", []))
            cand_rows = [r for r in allrows if r.kind in wanted] if stats.get("candidates") else []
            results[-1]["probs"] = probs_of(rec.calls, kinds, cand_rows)
            results[-1]["cand_rels"] = {c.rel for c in cands}
    return results


def _p(v):
    return "棄権" if v is None else f"{v:.3f}"


def report_probs(results):
    """--probs: 種類ごとの確率（通ったものに✔）と、候補行ごとの確率（正解候補に★、選ばれた行に✔）。"""
    out = []
    for i, x in enumerate(results, 1):
        pr = x["probs"]
        passed = set(x["stats"].get("kinds", []))
        out.append(f"{i}. {x['at']} 「{x['label']}」 → {x['verdict']}")
        out.append(f"   種類の段（下限 {context.KIND_FLOOR}、差 {context.KIND_GAP}、最大{context.MAX_KINDS}）")
        for k, v in sorted(pr["kinds"].items(), key=lambda t: -(t[1] if t[1] is not None else -1)):
            out.append(f"     {'✔' if k in passed else ' '} {_p(v)} {k}")
        if pr["rows"]:
            picked = {n for n, _ in x["picked"]}
            out.append(f"   行の段（下限 {context.ROW_FLOOR}、差 {context.ROW_GAP}、種類ごと最大{context.MAX_ROWS_PER_KIND}。"
                       "★=正解候補 ✔=選ばれた）")
            for r, v in sorted(pr["rows"], key=lambda t: -(t[1] if t[1] is not None else -1)):
                star = "★" if r.rel in x["cand_rels"] else " "
                ok = "✔" if r.name in picked else " "
                out.append(f"     {star}{ok} {_p(v)} {r.name}（{r.kind}）")
        else:
            out.append("   行の段: 候補なし（種類で全滅）")
    return "\n".join(out)


def report(results):
    out = []
    for i, x in enumerate(results, 1):
        cm = (x["comment"] or "").replace("\n", " ")[:30]
        out.append(f"{i}. {x['at']} 「{x['label']}」{cm}")
        out.append("   選ばれた行: " + ("、".join(f"{n}（{k}）" for n, k in x["picked"]) or "なし"))
        st = x["stats"]
        out.append(f"   種類: {'、'.join(st.get('kinds', [])) or 'なし'} / 候補{st.get('candidates', 0)}行（Jev が棄権すると、ここが0になる）")
        out.append(f"   正解候補: {'、'.join(x['candidates']) or 'なし'} → {x['verdict']}")
        f = x["fade"]
        if f:
            out.append(f"   薄れ層: 鮮明 {'、'.join(f['vivid']) or 'なし'} / 曖昧 {'、'.join(f['blur']) or 'なし'}"
                       f" / 落とした {'、'.join(f['dropped']) or 'なし'} / 痕跡の1行 {'あり' if f['tip'] else 'なし'}")
    hit, n = total(results)
    out.append(f"合計: ヒット {hit} / 正解候補があった件 {n}（全{len(results)}件）")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness.replay")
    ap.add_argument("vault", help="エージェントのフォルダ（Vault の 12 AIたち/<名前>）")
    ap.add_argument("--ratings", help="評価の jsonl（省略時は 03 Logs/ratings/*.jsonl の全部）")
    ap.add_argument("--env", help=".env のパス")
    ap.add_argument("--fake", action="store_true", help="FakeJev で回す（何も選ばない。動作確認用）")
    ap.add_argument("--probs", action="store_true", help="種類・行の生の確率を出す（棄権は「棄権」）。select の挙動は変えない")
    ap.add_argument("--human", action="store_true", help="薄れ層も通す（config の recall.mode を human にして回す）")
    ap.add_argument("--diary", action="store_true", help="日記も想起の材料に入れる")
    a = ap.parse_args(argv)

    vault = Vault(a.vault)
    config = vault.load("Agents/config.yaml", yaml.safe_load) or {}
    files = [a.ratings] if a.ratings else sorted(glob.glob(os.path.join(vault.root, "03 Logs", "ratings", "*.jsonl")))
    ratings = [r for f in files for r in load_ratings(f)]
    if not ratings:
        print("評価が見つからない", file=sys.stderr)
        return 1
    if a.fake:
        from tests.fakes import FakeJev
        jev = FakeJev()
    else:
        from harness.__main__ import load_env
        from harness.api import Jev
        load_env(a.env)
        name = (config.get("keys") or {}).get("typesafe", "TYPESAFE_KEY")
        key = os.environ.get(name, "")
        if not key:
            print(f"Jev の鍵（{name}）が無い。--env で .env を渡すか、--fake で動作確認だけする", file=sys.stderr)
            return 2
        jev = Jev(key)
    kinds_about = {k: v.get("about", "") for k, v in (config.get("kinds") or {}).items()}
    kinds_when = {k: v.get("when", "") for k, v in (config.get("kinds") or {}).items()}
    with _no_diary(not a.diary):
        if a.human:
            config = {**config, "recall": {**(config.get("recall") or {}), "mode": "human"}}
        results = asyncio.run(replay(jev, vault, ratings, config.get("name", "あなた"), kinds_about, kinds_when,
                                  a.probs, config))
    print(report(results))
    if a.probs:
        print("\n=== 確率 ===\n" + report_probs(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
