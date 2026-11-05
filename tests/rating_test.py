"""評価の画面：ボタンで選ぶ → 必要なら中身を選ぶ → ひと言を書く（minato と同じ順）。"""
import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import discord

from harness.gateway import LABELS, Gateway


class FakeResponse:
    def __init__(self):
        self.sent, self.edited, self.modal = None, None, None

    async def send_message(self, content, view=None, ephemeral=False):
        self.sent = SimpleNamespace(content=content, view=view, ephemeral=ephemeral)

    async def edit_message(self, content=None, view=None):
        self.edited = SimpleNamespace(content=content, view=view)

    async def send_modal(self, modal):
        self.modal = modal


def inter():
    return SimpleNamespace(response=FakeResponse(), user=SimpleNamespace(display_name="ポン酢"))


def press(view, name, i):
    b = next(x for x in view.children if x.label == name)
    return b.callback(i)


class RatingTest(unittest.TestCase):
    def _rate(self, labels):
        """labels の順にボタンを押し、ひと言を書いて送るまで。agent に渡った rec を返す。"""
        async def go():
            got = []
            gw = Gateway()
            gw.describe = lambda m: m.content
            gw.agent = SimpleNamespace(rate=lambda r: _keep(got, r))
            me = SimpleNamespace(id=1)
            gw._connection.user = me
            msg = SimpleNamespace(id=5, channel=SimpleNamespace(id=7), author=me, content="本文", stickers=[],
                                  attachments=[], embeds=[], reference=None, created_at=datetime(2026, 11, 5, tzinfo=timezone.utc))
            first = inter()
            await gw.rate(first, msg)
            self.assertTrue(first.response.sent.ephemeral)
            self.assertIsNone(first.response.modal)              # 最初は入力欄ではなくボタン
            self.assertEqual([b.label for b in first.response.sent.view.children], LABELS)
            view = first.response.sent.view
            for name in labels:
                i = inter()
                await press(view, name, i)
                view = i.response.edited.view if i.response.edited else None
            modal = i.response.modal
            modal.comment._value = "ひと言"
            done = inter()
            await modal.on_submit(done)
            return got[0]
        return asyncio.run(go())

    def test_pick_then_comment(self):
        r = self._rate(["キャラじゃない"])
        self.assertEqual((r["label"], r["comment"], r["rater"]), ("キャラじゃない", "ひと言", "ポン酢"))
        self.assertNotIn("detail", r)

    def test_detail_step(self):
        r = self._rate(["感情がズレてる", "強すぎ"])
        self.assertEqual((r["label"], r["detail"]), ("感情がズレてる", "強すぎ"))


async def _keep(got, r):
    got.append(r)
