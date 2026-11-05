"""Web UI。フォルダを読むだけで、何も書かないこと。"""
import asyncio
import json
import os
import unittest
from datetime import datetime

from aiohttp.test_utils import TestClient, TestServer

from harness import ui
from harness.agent import Agent
from harness.vault import Vault
from tests.fakes import copy_template


def snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for name in files:
            p = os.path.join(d, name)
            out[p] = (os.stat(p).st_mtime_ns, os.path.getsize(p))
    return out


def as_nagi(root):
    p = f"{root}/Agents/config.yaml"
    text = open(p, encoding="utf-8").read().replace("persona: basic", "persona: nagi")
    open(p, "w", encoding="utf-8").write(text)


def fetch(root, *paths):
    async def go():
        async with TestClient(TestServer(ui.app(root))) as c:
            return [await (await c.get(x)).json() for x in paths]
    return asyncio.run(go())


class UiTest(unittest.TestCase):
    def test_basic_persona_shows_presence(self):
        s = fetch(copy_template(), "/api/state")[0]
        self.assertTrue(s["view"]["say"])
        self.assertEqual(s["view"]["people"], [])

    def test_nagi_shows_body_and_people(self):
        root = copy_template()
        as_nagi(root)
        os.makedirs(f"{root}/02 State", exist_ok=True)
        with open(f"{root}/02 State/likes.json", "w", encoding="utf-8") as f:
            json.dump({"1": {"v": 30.0, "display": "ポン酢", "events": []}}, f, ensure_ascii=False)
        v = fetch(root, "/api/state")[0]["view"]
        self.assertEqual([m["name"] for m in v["meters"]], ["眠気", "空腹", "気力", "残高"])
        self.assertEqual(v["people"][0]["items"][0]["name"], "ポン酢")

    def test_reading_writes_nothing(self):
        root = copy_template()
        as_nagi(root)
        before = snapshot(root)
        fetch(root, "/api/state", "/api/chat")
        self.assertEqual(snapshot(root), before)

    def test_chat_reads_the_day_and_refuses_paths(self):
        root = copy_template()
        os.makedirs(f"{root}/03 Logs/chat", exist_ok=True)
        with open(f"{root}/03 Logs/chat/2026-11-05.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"at": "2026-11-05T16:00:00", "author": "ポン酢", "text": "起きてる？",
                                "channel": "1"}, ensure_ascii=False) + "\n")
        got, bad = fetch(root, "/api/chat", "/api/chat?day=..%2F..%2Fsecret")
        self.assertEqual(got["days"][0], "2026-11-05")
        self.assertEqual(got["rows"][0]["text"], "起きてる？")
        self.assertEqual(bad["rows"], [])

    def test_unit_name_is_escaped(self):
        self.assertEqual(ui.unit_name("/x/ナギ"), r"utsuwa@\xe3\x83\x8a\xe3\x82\xae.service")

    def test_mood_history_is_kept_every_ten_minutes(self):
        root = copy_template()
        as_nagi(root)
        a = Agent(Vault(root), None, None, None)
        t = datetime(2026, 11, 9, 20, 0)
        a.persona.tick(t)
        a.persona.tick(t.replace(minute=5))
        a.persona.tick(t.replace(minute=11))
        self.assertEqual(len(a.persona.mem.data["mood_hist"]), 2)
