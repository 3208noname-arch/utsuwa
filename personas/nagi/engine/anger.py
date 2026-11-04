# -*- coding: utf-8 -*-
"""人ごとの怒り（純関数）。怒りに行き先を作る（10-24 ユーザー「いくら貶しても怒ったことが無い。怒っても何も変化する先が無い」）。

- 本気のけなし・見下しを受けたら、その相手への怒りを積む（0〜100。けなしは大きく、見下しは小さく）
- 時間で冷める（半減期 HALF_LIFE。仲のいい相手は CLOSE_HALF_LIFE で早く冷める）
- 謝られたら減る。**謝り方の軽さで減り方を変える**（ユーザー「怒りの度合い、謝りの軽さも判定に入れたい」）。
  本気の謝罪なら大きく、「ごめんw」程度なら少しだけ
- 怒っている間は: LLMに怒りと理由を渡して言い返す・そっけなくする・根に持たせる、短い突き放しの一言も弾かない、
  その相手の発言には混ざりにくくする（engine/policy.py に渡す罰）
"""
from datetime import datetime, timedelta

ADD = {"attack": 40, "belittle": 15}
HALF_LIFE = timedelta(hours=2)
CLOSE_HALF_LIFE = timedelta(hours=1)
CLOSE_FROM = 50                 # 好感度がこれ以上なら仲がいい（早く冷める）
APOLOGY_TRUE = 0.70
CUT_SINCERE, CUT_LIGHT = 0.8, 0.2   # 謝られた時に減る割合（本気／軽い）
LIGHT_FROM = 0.5                # 軽さがこれ以下なら本気の謝罪とみなす
FORGET = 5                      # これを割ったら消す
BANDS = [(50, "かなり怒っている"), (20, "むかついている"), (FORGET, "少し根に持っている")]
CURT_OK_FROM = 20               # これ以上怒っている相手には、短い突き放しの一言も許す
POLICY_PER = 1 / 25             # 怒り25ごとに、その相手の発言へ混ざるロジットを−1


def cool(rec, t, close=False):
    """経った時間の分だけ冷ます。"""
    hl = CLOSE_HALF_LIFE if close else HALF_LIFE
    dt = max(0.0, (t - datetime.fromisoformat(rec["at"])).total_seconds())
    rec["v"] = rec["v"] * 0.5 ** (dt / hl.total_seconds())
    rec["at"] = t.isoformat(timespec="seconds")
    return rec["v"]


def hit(store, uid, display, kind, quote, t, close=False):
    """けなし・見下しを受けた。怒りを積み、理由を残す。積んだ量を返す（その種類で怒らなければ0）。"""
    add = ADD.get(kind, 0)
    if not add:
        return 0
    rec = store.setdefault(str(uid), {"v": 0.0, "at": t.isoformat(timespec="seconds")})
    cool(rec, t, close)
    rec.update({"v": min(100.0, rec["v"] + add), "display": display, "why": quote[:40], "kind": kind})
    return add


def apologize(store, uid, p_apology, p_light, t, close=False):
    """謝られた。軽さ（p_light）で減り方を変える。減った量を返す（謝っていなければ0）。"""
    rec = store.get(str(uid))
    if not rec or p_apology is None or p_apology < APOLOGY_TRUE:
        return 0.0
    cool(rec, t, close)
    # Jev は本気の謝罪（「言いすぎた、ごめん。悪かった」）でも軽さを 0.56 と読むので、0.5〜1.0 の間で効かせる
    light = min(1.0, max(0.0, ((p_light or 0.0) - LIGHT_FROM) / (1 - LIGHT_FROM)))
    cut = rec["v"] * (CUT_SINCERE * (1 - light) + CUT_LIGHT * light)
    rec["v"] -= cut
    if rec["v"] < FORGET:
        store.pop(str(uid))
    return round(cut, 1)


def level(store, uid, t, close=False):
    """今の怒り（冷ました後）。冷めきっていたら消して0。"""
    rec = store.get(str(uid))
    if not rec:
        return 0.0
    if cool(rec, t, close) < FORGET:
        store.pop(str(uid))
        return 0.0
    return rec["v"]


def band(v):
    return next((name for floor, name in BANDS if v >= floor), None)


def as_words(rec, v):
    """LLMに渡す怒り。無ければ None。"""
    b = band(v)
    if not b:
        return None
    return (f"{rec['display']}に{b}（理由: 「{rec['why']}」と言われた）。"
            "言い返す・そっけなくする・根に持って理由を持ち出す。頼みごとは断ってよい。謝られるまで機嫌を直さない。"
            "怒りの言葉そのもの（「怒ってる」）は言わず、態度に出す")
