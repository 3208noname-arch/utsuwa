"""エージェントの様子を見る Web UI: python -m harness.ui <エージェントのフォルダ> [--env .env]

- Bot とは別のプロセスで動かす。UI が落ちても Bot は止まらない
- 状態は読むだけ（Bot がメモリに持っているので、直接書くと上書きで消える）。直す時は 02 State/requests/ に依頼を置く
- 認証は無い。外から届かないアドレスで待ち受ける（.env の UI_HOST、UI_PORT）
"""
import argparse
import asyncio
import glob
import json
import os
import re
import time
from datetime import datetime

import yaml
from aiohttp import web

from harness.__main__ import load_env
from harness.agent import TOTAL_DAILY_MAX, Agent, day_key
from harness.vault import Vault

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT_MAX, LOG_MAX = 400, 1000


def unit_name(root):
    """systemd のサービス名。日本語のフォルダ名は \\xNN の形になる。"""
    name = os.path.basename(root.rstrip("/\\"))
    return "utsuwa@" + "".join(c if c.isascii() else "".join(f"\\x{b:02x}" for b in c.encode()) for c in name) + ".service"


async def run(*cmd):
    """コマンドの出力。無い環境（Windows など）では None。"""
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return None
    out, _ = await p.communicate()
    return out.decode("utf-8", "replace")


def saved_at(root):
    """状態がファイルに書かれた最後の時刻。"""
    stamps = [os.path.getmtime(p) for p in glob.glob(os.path.join(root, "02 State", "*.json"))]
    return datetime.fromtimestamp(max(stamps)).isoformat(timespec="seconds") if stamps else None


def read_state(root):
    agent = Agent(Vault(root), None, None, None)       # 読むたびに作り直す。ファイルの今の中身を見せる
    t = agent.now()
    h, c = agent.h.data, agent.h.data.get("counts", {})
    return {"name": agent.config.get("name", os.path.basename(root)), "now": t.isoformat(timespec="seconds"),
            "saved_at": saved_at(root), "view": agent.persona.view(t),
            "replies": c.get("total", 0) if c.get("day") == day_key(t) else 0, "replies_max": TOTAL_DAILY_MAX,
            "deferred": len(h.get("deferred", [])),
            "events": list(reversed(h.get("events", {}).get(t.date().isoformat(), [])))}


def read_chat(root, day):
    files = glob.glob(os.path.join(root, "03 Logs", "chat", "*.jsonl"))
    days = sorted((os.path.basename(p)[:-6] for p in files), reverse=True)
    day = day or (days[0] if days else "")
    if day not in days:
        return {"days": days, "day": day, "rows": []}
    with open(os.path.join(root, "03 Logs", "chat", day + ".jsonl"), encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    config = Vault(root).load("Agents/config.yaml", yaml.safe_load) or {}
    shadow = {str(c.get("id")) for c in config.get("channels") or [] if c.get("mode") == "shadow"}
    return {"days": days, "day": day,
            "rows": [{"at": r["at"], "author": r.get("author"), "text": r.get("text"), "me": r.get("me", False),
                      "shadow": str(r.get("channel")) in shadow} for r in reversed(rows[-CHAT_MAX:])]}


def add_request(root, body):
    """値の変更依頼を置く。Bot が次の分に受け取る。おかしければ理由の文字列を返す。"""
    agent = Agent(Vault(root), None, None, None)
    plot = agent.persona.view(agent.now()).get("plot")
    if not plot or not isinstance(body, dict):
        return "依頼を受けられない"
    axis = next((a for a in plot["axes"] if a["key"] == body.get("key")), None)
    if not axis or not any(x["name"] == body.get("name") for x in plot["items"]):
        return "話題か軸が無い"
    v = body.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not axis["min"] <= v <= axis["max"]:
        return "値が範囲の外"
    agent.vault.write_json(f"02 State/requests/{time.time_ns()}.json",
                           {"kind": "plot", "name": body["name"], "key": body["key"], "value": v})
    return None


def app(root):
    async def page(_):
        return web.FileResponse(os.path.join(HERE, "ui.html"))

    async def state(_):
        s = await asyncio.to_thread(read_state, root)
        s["running"] = ((await run("systemctl", "is-active", unit_name(root))) or "").strip() or None
        return web.json_response(s)

    async def chat(req):
        return web.json_response(await asyncio.to_thread(read_chat, root, req.query.get("day")))

    async def log(req):
        n = re.fullmatch(r"\d{1,4}", req.query.get("n", ""))
        n = max(10, min(int(n[0]) if n else 400, LOG_MAX))
        out = await run("journalctl", "-u", unit_name(root), "-n", str(n), "--no-pager", "-o", "short-iso")
        return web.json_response({"lines": (out or "").splitlines()})

    async def plot(req):
        if req.content_type != "application/json":      # 別のページからのフォーム送信を受けない
            raise web.HTTPUnsupportedMediaType()
        try:
            body = await req.json()
        except ValueError:
            body = None
        why = await asyncio.to_thread(add_request, root, body)
        if why:
            raise web.HTTPBadRequest(text=why)
        return web.json_response({"ok": True})

    a = web.Application()
    a.add_routes([web.get("/", page), web.get("/api/state", state), web.get("/api/chat", chat), web.get("/api/log", log),
                  web.post("/api/plot", plot)])
    return a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--env", default=".env")
    a = ap.parse_args()
    load_env(a.env)
    web.run_app(app(a.folder), host=os.environ.get("UI_HOST", "127.0.0.1"), port=int(os.environ.get("UI_PORT", "8090")),
                print=None)


if __name__ == "__main__":
    main()
