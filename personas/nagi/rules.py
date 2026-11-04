"""決まりの値（怒りの増え方、好感度の増減、体の進み方など）を、config.yaml の `rules:` で上書きする。

    rules:
      anger:
        ADD: {attack: 40, belittle: 15}
        HALF_LIFE: 120
      affinity:
        DELTA: {attack/me: -8}

- 既定値は `engine/` の各ファイルの頭の定数。書いた分だけ上書きし、消せば既定値に戻る
- 時間（timedelta）は分、時刻（time）は "08:00"、組をキーにした表（("attack", "me")）は "attack/me" で書く
- 表は、書いたキーだけを上書きする。帯（BANDS）は全部を書き直す
- 規則は1つのプロセスに1人分しか持てない（モジュールの定数を差し替えるため）。エージェントごとにプロセスを分ける

`python -m personas.nagi` で、今の既定値を config.yaml に貼れる形（コメントつき）で出す。
"""
import re
from datetime import time, timedelta
from pathlib import Path

from personas.nagi.engine import affinity, anger, body, decide, mood, names, policy, topics

MODULES = {"affinity": affinity, "anger": anger, "body": body, "decide": decide,
           "mood": mood, "names": names, "policy": policy, "topics": topics}
SKIP = {"KINDS", "PLAN"}   # 形そのもの（返す型の並び）と、本体から差し込む予定
NUM = (int, float)
YAML_WORDS = {"TRUE", "FALSE", "YES", "NO", "ON", "OFF", "NULL", "Y", "N"}


def _tunable(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, NUM + (timedelta, time)):
        return True
    if isinstance(v, dict):
        return bool(v) and all(isinstance(x, NUM) for x in v.values())
    if isinstance(v, list):
        return bool(v) and all(isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], NUM) for x in v)
    if isinstance(v, tuple):
        return bool(v) and all(isinstance(x, str) for x in v)
    return False


DEFAULTS = {m: {k: v for k, v in vars(mod).items()
                if k.isupper() and k not in SKIP and _tunable(v)}
            for m, mod in MODULES.items()}


def _key(k):
    return "/".join(k) if isinstance(k, tuple) else k


def _from(default, v):
    """yaml の値を、既定値と同じ型に直す。合わなければ ValueError。"""
    if isinstance(default, timedelta):
        return timedelta(minutes=float(v))
    if isinstance(default, time):
        return time.fromisoformat(str(v))
    if isinstance(default, NUM):
        if isinstance(v, bool) or not isinstance(v, NUM):
            raise ValueError(f"数ではない: {v!r}")
        return v
    if isinstance(default, dict):
        if not isinstance(v, dict):
            raise ValueError("表（キー: 値）で書く")
        out = dict(default)
        keys = {_key(k): k for k in default}
        for kk, vv in v.items():
            if str(kk) not in keys:
                raise ValueError(f"知らないキー: {kk}（{'、'.join(keys)}）")
            out[keys[str(kk)]] = _from(default[keys[str(kk)]], vv)
        return out
    if isinstance(default, list):
        return [(_from(0.0, a), str(b)) for a, b in v]
    if isinstance(default, tuple):
        return tuple(str(x) for x in v)
    raise ValueError("上書きできない値")


def _to(v):
    if isinstance(v, timedelta):
        m = v.total_seconds() / 60
        return int(m) if m == int(m) else round(m, 3)
    if isinstance(v, time):
        return v.strftime("%H:%M")
    if isinstance(v, dict):
        return {_key(k): x for k, x in v.items()}
    if isinstance(v, list):
        return [list(x) for x in v]
    if isinstance(v, tuple):
        return list(v)
    return v


def apply(rules):
    """既定値に戻してから、rules を上書きする。書き間違いは飛ばして、その説明のリストを返す。"""
    warns = []
    for m, consts in DEFAULTS.items():
        for k, v in consts.items():
            setattr(MODULES[m], k, v)
    for m, kv in (rules or {}).items():
        if m not in DEFAULTS:
            warns.append(f"知らない部品: {m}（{'、'.join(DEFAULTS)}）")
            continue
        for k, v in (kv or {}).items():
            k = str(k).upper()
            if k not in DEFAULTS[m]:
                warns.append(f"{m}.{k} は無い")
                continue
            try:
                setattr(MODULES[m], k, _from(DEFAULTS[m][k], v))
            except (ValueError, TypeError) as e:
                warns.append(f"{m}.{k}: {e}（既定値のまま）")
    return warns


def current():
    """今の既定値を、config.yaml の rules: と同じ形で。"""
    return {m: {k: _to(v) for k, v in consts.items()} for m, consts in DEFAULTS.items()}


def _comments(mod):
    """定数の行の末尾のコメント（`X = 1   # これ`）。"""
    out = {}
    for ln in Path(mod.__file__).read_text(encoding="utf-8").split("\n"):
        mt = re.match(r"^([A-Z_][A-Z0-9_]*(?:\s*,\s*[A-Z_][A-Z0-9_]*)*)\s*=.*?#\s*(.+)$", ln)
        if mt:
            for k in re.split(r"\s*,\s*", mt.group(1)):
                out[k] = mt.group(2).strip()
    return out


def _flow(v):
    """yaml の1行の書き方（日本語をそのまま出す）。"""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_flow(x) for x in v) + "]"
    if isinstance(v, str):
        return f'"{v}"' if re.match(r"^[\d:]|[#:{}\[\],]", v) else v
    return repr(v)


def dump():
    lines = ["rules:"]
    for m, consts in current().items():
        notes = _comments(MODULES[m])
        lines.append(f"  {m}:")
        for k, v in consts.items():
            unit = "  # 分" if isinstance(DEFAULTS[m][k], timedelta) else ""
            note = f"  # {notes[k]}" if k in notes else unit
            if k in notes and unit:
                note = f"  # 分。{notes[k]}"
            key = f'"{k}"' if k in YAML_WORDS else k   # TRUE は yaml だと真偽値になる
            lines.append(f"    {key}: {_flow(v)}{note}")
    return "\n".join(lines) + "\n"
