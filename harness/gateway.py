"""Discord との繋ぎ口（discord.py）。発言を dict に直して本体に渡し、本体の「送る」を Discord で行う。

本体（agent.py）は Discord を知らない。ここを差し替えれば、ほかのチャットにも乗せられる。
書き方は minato の `app/client.py` と同じ（スタンプや画像を言葉にする、返信先の古さ、評価の右クリックメニュー）。
"""
import asyncio
import logging

import discord

from harness.schedule import ago

log = logging.getLogger("utsuwa")

LABELS = ["自然", "流れとズレてる", "キャラじゃない", "バグ"]


class Gateway(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.agent = None
        self.tree = discord.app_commands.CommandTree(self)
        self.tree.add_command(discord.app_commands.ContextMenu(name="評価", callback=self.rate))
        self.synced = False

    def describe(self, m):
        """本文＋スタンプ・添付の説明。スタンプや画像だけの発言は本文が空になるので、何が送られたかを言葉で渡す。"""
        parts = []
        ref = m.reference.resolved if m.reference else None
        if isinstance(ref, discord.Message):
            age = (m.created_at - ref.created_at).total_seconds()
            parts.append(f"（{ref.author.display_name}の{ago(age)}の発言への返信）" if age >= 3600
                         else f"（{ref.author.display_name}への返信）")
        elif m.mentions:
            parts.append(f"（{'・'.join(u.display_name for u in m.mentions)}宛て）")
        if m.clean_content:
            parts.append(m.clean_content)
        parts += [f"［スタンプ「{s.name}」］" for s in m.stickers]
        for a in m.attachments:
            kind = (a.content_type or "").split("/")[0]
            parts.append({"image": "［画像］", "video": "［動画］", "audio": "［音声］"}.get(kind, "［ファイル］"))
        return " ".join(parts)[:400]

    def entry(self, m):
        ref = m.reference.resolved if m.reference else None
        # 誰に向けた発言か（返信先、無ければ最初のメンション先）。好感度と呼び名を読む時に使う
        to = ref.author if isinstance(ref, discord.Message) else next((u for u in m.mentions if u != m.author), None)
        return {"id": str(m.id), "at": m.created_at.astimezone().replace(tzinfo=None).isoformat(timespec="seconds"),
                "target_author": to.display_name if to else None, "target_id": str(to.id) if to else None,
                "channel": str(m.channel.id), "author": m.author.display_name, "author_id": str(m.author.id),
                "bot": m.author.bot, "me": m.author == self.user, "text": self.describe(m), "url": m.jump_url,
                "reply_to": str(m.reference.message_id) if m.reference else None,
                "to_me": bool(isinstance(ref, discord.Message) and ref.author == self.user) or self.user in m.mentions,
                "to_other": bool((isinstance(ref, discord.Message) and ref.author != self.user)
                                 or (m.mentions and self.user not in m.mentions))}

    async def on_ready(self):
        if not self.synced:
            for g in self.guilds:
                self.tree.copy_global_to(guild=g)
                try:
                    await self.tree.sync(guild=g)
                except discord.HTTPException as e:
                    log.error("右クリックメニューを登録できない: %s", e)
            self.synced = True
        if not hasattr(self, "_tick"):
            self._tick = asyncio.create_task(self.tick_loop())
        log.info("起動: %s", self.user)

    async def on_message(self, m):
        if m.flags.ephemeral or m.interaction_metadata:   # 評価の確認など、本人にしか見えない応答は会話ではない
            return
        await self.agent.on_message(self.entry(m))

    async def tick_loop(self):
        while not self.is_closed():
            await asyncio.sleep(60)
            try:
                await self.agent.tick()
            except Exception:
                log.exception("1分ごとの処理に失敗")

    # --- 本体から呼ばれる「送る」 ------------------------------------------------------
    async def send(self, channel, texts, reply_to=None, typing=()):
        ch = self.get_channel(int(channel))
        if ch is None:
            return
        target = None
        if reply_to:
            try:
                target = await ch.fetch_message(int(reply_to))
            except discord.HTTPException:
                target = None
        for i, text in enumerate(texts):
            secs = typing[i] if i < len(typing) else 0
            if secs:
                async with ch.typing():
                    await asyncio.sleep(secs)
            if i == 0 and target:
                await target.reply(text, mention_author=False)
            else:
                await ch.send(text)

    async def react(self, channel, message_id, emoji):
        ch = self.get_channel(int(channel))
        if ch:
            msg = await ch.fetch_message(int(message_id))
            await msg.add_reaction(emoji)

    async def notify(self, text):
        where = self.agent.config.get("notify_channel")
        ch = where and self.get_channel(int(where))
        if ch:
            await ch.send(text)

    # --- 評価（右クリックメニュー → 本人にしか見えない画面）-----------------------------------
    async def rate(self, interaction, message: discord.Message):
        gw = self

        class Form(discord.ui.Modal, title="評価"):
            label = discord.ui.TextInput(label="自然／流れとズレてる／キャラじゃない／バグ", max_length=20)
            comment = discord.ui.TextInput(label="ひと言（空欄でよい）", style=discord.TextStyle.paragraph,
                                           required=False, max_length=300)

            async def on_submit(self, inter):
                await inter.response.send_message("記録しました", ephemeral=True)
                rec = {"message_id": str(message.id), "channel": str(message.channel.id), "text": gw.describe(message),
                       "is_me": message.author == gw.user, "label": str(self.label), "comment": str(self.comment),
                       "rater": inter.user.display_name,
                       "at": discord.utils.utcnow().astimezone().replace(tzinfo=None).isoformat(timespec="seconds"),
                       "msg_at": message.created_at.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")}
                await gw.agent.rate(rec)

        await interaction.response.send_modal(Form())
