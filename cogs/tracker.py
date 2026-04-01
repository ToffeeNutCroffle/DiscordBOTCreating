import os
import discord
from discord.ext import commands
from database.db import DatabaseManager, now_utc, to_kst


class TrackerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: DatabaseManager = bot.db
        self.dev_category: str = os.getenv("DEV_CATEGORY_NAME", "개발실")
        self.min_dev_secs: int = int(os.getenv("MIN_DEV_SECONDS", "5400"))
        # user_id -> session_id
        self.active_sessions: dict[str, int] = {}

    def _is_dev_channel(self, channel) -> bool:
        if channel is None:
            return False
        if channel.category is None:
            return False
        return channel.category.name == self.dev_category

    @commands.Cog.listener()
    async def on_ready(self):
        """봇 재시작 시 진행 중이던 세션 복구."""
        for guild in self.bot.guilds:
            orphans = self.db.close_orphan_sessions(str(guild.id))
            for orphan in orphans:
                user_id = orphan["user_id"]
                session_id = orphan["session_id"]
                join_time = orphan["join_time"]

                member = guild.get_member(int(user_id))
                if member and self._is_dev_channel(member.voice.channel if member.voice else None):
                    self.active_sessions[user_id] = session_id
                else:
                    leave_time = now_utc()
                    self.db.close_session(session_id, leave_time)
                    self._try_confirm_dev_day(user_id, str(guild.id), join_time)

        print(f"[Tracker] 복구 완료. 진행 중인 세션: {len(self.active_sessions)}개")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        was_in_dev = self._is_dev_channel(before.channel)
        is_in_dev = self._is_dev_channel(after.channel)

        if was_in_dev and is_in_dev:
            return

        user_id = str(member.id)
        guild_id = str(member.guild.id)

        if not was_in_dev and is_in_dev:
            self._handle_join(user_id, guild_id)

        elif was_in_dev and not is_in_dev:
            self._handle_leave(user_id, guild_id)

    def _handle_join(self, user_id: str, guild_id: str):
        join_time = now_utc()
        session_id = self.db.open_session(user_id, guild_id, join_time)
        self.active_sessions[user_id] = session_id

    def _handle_leave(self, user_id: str, guild_id: str):
        session_id = self.active_sessions.pop(user_id, None)
        if session_id is None:
            return

        join_time = self.db.get_session_join_time(session_id)
        leave_time = now_utc()
        self.db.close_session(session_id, leave_time)

        ref = join_time if join_time else leave_time
        self._try_confirm_dev_day(user_id, guild_id, ref)

        # 자정을 넘긴 세션이면 leave_time 날짜도 별도로 체크
        if join_time and to_kst(join_time).date() < to_kst(leave_time).date():
            self._try_confirm_dev_day(user_id, guild_id, leave_time)

    def _try_confirm_dev_day(self, user_id: str, guild_id: str, reference_time):
        """해당 날짜(KST) 누적 시간이 임계값 이상이면 개발일로 확정."""
        date_kst = to_kst(reference_time).strftime("%Y-%m-%d")
        total_secs = self.db.get_day_total_secs(user_id, guild_id, date_kst)
        if total_secs >= self.min_dev_secs:
            self.db.upsert_dev_day(user_id, guild_id, date_kst, total_secs)


async def setup(bot: commands.Bot):
    await bot.add_cog(TrackerCog(bot))
