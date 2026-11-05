"""起動: python -m harness <エージェントのフォルダ> [--env .env] [--check]

--check は Discord に繋がずに、フォルダを読めるか（人格、決まり、予定、記憶の表）を確かめて終わる。
鍵は .env に置き、config.yaml には鍵の名前だけを書く（Vault に鍵を置かない）。
"""
import argparse
import asyncio
import logging
import os
from datetime import datetime

from harness import __version__
from harness.agent import Agent
from harness.api import LLM, Jev
from harness.context import lesson_rows
from harness.vault import Vault


def load_env(path):
    if not path or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def check(agent):
    v, t = agent.vault, agent.now()
    print(f"utsuwa {__version__}: {v.root}")
    print(f"  人格の部品: {type(agent.persona).__name__}")
    print(f"  SOUL.md {len(v.text('Agents/SOUL.md'))}字 / AGENTS.md {len(v.text('Agents/AGENTS.md'))}字 / "
          f"RECORDER.md {len(v.text('Agents/RECORDER.md'))}字")
    print(f"  予定: {agent.schedule().week_text(t)}")
    rows = lesson_rows(v)
    print(f"  記憶（現役）: {len(rows)}件")
    for r in rows:
        print(f"    - {r.line()}")
    print(f"  今の様子: {agent.persona.presence(t).label}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    load_env(a.env)
    vault = Vault(a.folder)
    probe = Agent(vault, None, None, None)
    keys = probe.config.get("keys") or {}
    jev = Jev(os.environ.get(keys.get("typesafe", "TYPESAFE_KEY"), ""))
    llm = LLM(os.environ.get(keys.get("openrouter", "OPENROUTER_KEY"), ""), probe.config.get("models") or [])
    if a.check:
        check(probe)
        return
    from harness.gateway import Gateway
    gw = Gateway()
    agent = Agent(vault, jev, llm, gw)
    gw.agent = agent
    try:
        gw.run(os.environ[keys.get("discord", "DISCORD_TOKEN")], log_handler=None)
    finally:
        agent.flush(datetime.now(), force=True)


if __name__ == "__main__":
    main()
