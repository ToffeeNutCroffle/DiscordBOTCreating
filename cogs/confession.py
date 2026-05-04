import os
import random
import discord
from discord import app_commands
from discord.ext import commands
from database.db import DatabaseManager, now_utc, to_dev_date

DEV_CATEGORY_NAME = os.getenv("DEV_CATEGORY_NAME", "개발실")

INTRO_MESSAGES = [
    "어머, 오늘 힘드셨나요? 저에게 다 털어놓으셔도 괜찮아요. 천천히 말씀해 주세요.",
]

REACTION_MESSAGES = [
    "그랬군요... 많이 힘드셨겠어요. 후회하고 계신다는 것 자체가 이미 용기 있는 일이에요.\n그럼 오늘 기록에서 얼마나 지워드릴까요?",
]

FORGIVE_MESSAGES = [
    "{time}을 용서해드렸어요. 이제 조금 홀가분해지셨으면 좋겠어요. 내일은 더 잘 하실 수 있을 거예요!",
]

FULL_FORGIVE_MESSAGES = [
    "오늘 기록이 전부 지워졌어요. 새로운 내일이 기다리고 있을 거에요!",
]


def _secs_to_str(secs: int) -> str:
    h = secs // 3600
    m = (secs % 3600) // 60
    if h > 0 and m > 0:
        return f"{h}시간 {m}분"
    if h > 0:
        return f"{h}시간"
    return f"{m}분"


def in_dev_category():
    async def predicate(interaction: discord.Interaction) -> bool:
        channel = interaction.channel
        if channel is None or not hasattr(channel, "category") or channel.category is None:
            await interaction.response.send_message(
                f"이 명령어는 **{DEV_CATEGORY_NAME}** 카테고리 채널에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return False
        if channel.category.name != DEV_CATEGORY_NAME:
            await interaction.response.send_message(
                f"이 명령어는 **{DEV_CATEGORY_NAME}** 카테고리 채널에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return False
        return True
    return app_commands.check(predicate)


class ConfessModal(discord.ui.Modal, title="고해성사"):
    confession = discord.ui.TextInput(
        label="오늘 어떤 일이 있었나요?",
        style=discord.TextStyle.paragraph,
        placeholder="저에게 솔직하게 말씀해 주세요.",
        required=True,
        max_length=500,
    )

    def __init__(self, cog: "ConfessionCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        self.cog.pending_confessions[user_id] = self.confession.value
        reaction = random.choice(REACTION_MESSAGES)
        view = TimeButtonView(self.cog)
        await interaction.response.send_message(reaction, view=view, ephemeral=True)


class TimeModal(discord.ui.Modal, title="차감 시간 입력"):
    minutes = discord.ui.TextInput(
        label="지워드릴 시간을 알려주세요 (분 단위)",
        style=discord.TextStyle.short,
        placeholder="예: 30",
        required=True,
    )

    def __init__(self, cog: "ConfessionCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id)

        try:
            mins = int(self.minutes.value)
            if mins <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "올바른 숫자를 입력해주세요.", ephemeral=True
            )
            return

        self.cog.pending_confessions.pop(user_id, None)

        today_str = to_dev_date(now_utc())
        actual_secs = self.cog.db.deduct_today_secs(user_id, guild_id, today_str, mins * 60)

        tracker = self.cog.bot.cogs.get("TrackerCog")
        min_dev_secs = tracker.min_dev_secs if tracker else int(os.getenv("MIN_DEV_SECONDS", "5400"))
        new_total = self.cog.db.get_day_total_secs(user_id, guild_id, today_str)

        if new_total >= min_dev_secs:
            self.cog.db.upsert_dev_day(user_id, guild_id, today_str, new_total)
        else:
            self.cog.db.delete_dev_day(user_id, guild_id, today_str)

        if new_total == 0:
            msg = random.choice(FULL_FORGIVE_MESSAGES)
        else:
            msg = random.choice(FORGIVE_MESSAGES).format(time=_secs_to_str(actual_secs))

        await interaction.response.send_message(msg, ephemeral=True)


class ConfessButtonView(discord.ui.View):
    def __init__(self, cog: "ConfessionCog"):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label="고해하기", style=discord.ButtonStyle.primary)
    async def confess_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ConfessModal(self.cog)
        await interaction.response.send_modal(modal)


class TimeButtonView(discord.ui.View):
    def __init__(self, cog: "ConfessionCog"):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label="시간 입력하기", style=discord.ButtonStyle.primary)
    async def time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TimeModal(self.cog)
        await interaction.response.send_modal(modal)


class ConfessionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: DatabaseManager = bot.db
        self.pending_confessions: dict[str, str] = {}

    @app_commands.command(name="고해성사", description="오늘의 개발 시간 일부를 삭제합니다")
    @in_dev_category()
    async def confession(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(interaction.user.id)
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id)

        if member and member.voice and member.voice.channel:
            category = member.voice.channel.category
            if category and category.name == DEV_CATEGORY_NAME:
                await interaction.response.send_message(
                    "음성채널에 있는 동안은 고해성사를 할 수 없어요. 채널에서 나온 후에 다시 시도해주세요.",
                    ephemeral=True,
                )
                return

        today_str = to_dev_date(now_utc())
        today_secs = self.db.get_day_total_secs(user_id, guild_id, today_str)
        if today_secs == 0:
            await interaction.response.send_message(
                "오늘 개발 기록이 없어요. 지울 시간이 없답니다.",
                ephemeral=True,
            )
            return

        intro = random.choice(INTRO_MESSAGES)
        view = ConfessButtonView(self)
        await interaction.response.send_message(intro, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ConfessionCog(bot))
