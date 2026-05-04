import os
import calendar
import discord
from datetime import timezone, timedelta
from discord import app_commands
from discord.ext import commands
from database.db import DatabaseManager, now_utc, to_kst, to_dev_date, dev_day_start_utc, month_utc_range

DEV_CATEGORY_NAME = os.getenv("DEV_CATEGORY_NAME", "개발실")


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


def secs_to_str(secs: int) -> str:
    h = secs // 3600
    m = (secs % 3600) // 60
    if h > 0 and m > 0:
        return f"{h}시간 {m}분"
    if h > 0:
        return f"{h}시간"
    return f"{m}분"


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: DatabaseManager = bot.db

    def _live_session_secs(self, user_id: str, now_kst) -> tuple[int, int]:
        """진행 중 세션의 오늘/이번달 기여 시간(초) 반환. (today_live, month_live)"""
        tracker = self.bot.cogs.get("TrackerCog")
        if not tracker or user_id not in tracker.active_sessions:
            return 0, 0
        session_id = tracker.active_sessions[user_id]
        join_time = self.db.get_session_join_time(session_id)
        if not join_time:
            return 0, 0
        now = now_utc()
        day_start = dev_day_start_utc(now)
        today_live = max(0, int((now - max(join_time, day_start)).total_seconds()))
        year_month = to_dev_date(now)[:7]
        month_start, _ = month_utc_range(year_month)
        month_live = max(0, int((now - max(join_time, month_start)).total_seconds()))
        return today_live, month_live

    @app_commands.command(name="개발통계", description="개발 활동 통계를 확인합니다")
    @app_commands.describe(유저="통계를 조회할 유저 (생략 시 본인)")
    @in_dev_category()
    async def dev_stats(self, interaction: discord.Interaction, 유저: discord.Member = None):
        target = 유저 or interaction.user
        user_id = str(target.id)
        guild_id = str(interaction.guild_id)

        now_kst = to_kst(now_utc())
        today_str = to_dev_date(now_utc())
        year_month = today_str[:7]

        today_live, month_live = self._live_session_secs(user_id, now_kst)
        today_secs = self.db.get_day_total_secs(user_id, guild_id, today_str) + today_live
        monthly_days = self.db.get_monthly_days(user_id, guild_id, year_month)
        monthly_secs = self.db.get_monthly_secs(user_id, guild_id, year_month) + month_live

        tracker = self.bot.cogs.get("TrackerCog")
        min_dev_secs = tracker.min_dev_secs if tracker else int(os.getenv("MIN_DEV_SECONDS", "5400"))
        include_today = today_secs >= min_dev_secs

        # 진행 중 세션으로 오늘이 개발일 조건 충족 시, 아직 확정되지 않은 경우 개발일 수에 반영
        if include_today:
            monthly_dev_dates = set(self.db.get_monthly_dev_dates(user_id, guild_id, year_month))
            if today_str not in monthly_dev_dates:
                monthly_days += 1

        consecutive = self.db.get_consecutive_days(user_id, guild_id, today_str, include_today=include_today)
        max_streak = self.db.get_max_streak(user_id, guild_id)

        if consecutive > 0 and consecutive >= max_streak:
            streak_value = f"{consecutive}일 🏆 최고기록 갱신 중!"
        else:
            streak_value = f"{consecutive}일 (최고기록: {max_streak}일)"

        embed = discord.Embed(
            title=f"{target.display_name}의 개발 통계",
            color=discord.Color.blue(),
        )
        embed.add_field(name="🔥 연속 개발", value=streak_value, inline=False)
        embed.add_field(name="📅 이번 달 개발일", value=f"{monthly_days}일", inline=True)
        embed.add_field(name="⏱️ 이번 달 개발시간", value=secs_to_str(monthly_secs), inline=True)
        embed.add_field(name="📆 오늘 개발시간", value=secs_to_str(today_secs) if today_secs > 0 else "0분", inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)

        await interaction.response.send_message(embed=embed, )

    @app_commands.command(name="개발달력", description="월별 개발 달력을 확인합니다")
    @app_commands.describe(
        유저="조회할 유저 (생략 시 본인)",
        연월="조회할 연월 (예: 2025-03, 생략 시 이번 달)",
    )
    @in_dev_category()
    async def dev_calendar(
        self,
        interaction: discord.Interaction,
        유저: discord.Member = None,
        연월: str = None,
    ):
        target = 유저 or interaction.user
        user_id = str(target.id)
        guild_id = str(interaction.guild_id)

        now_kst = to_kst(now_utc())

        if 연월 is None:
            year_month = now_kst.strftime("%Y-%m")
        else:
            year_month = 연월

        try:
            year, month = map(int, year_month.split("-"))
        except ValueError:
            await interaction.response.send_message(
                "연월 형식이 올바르지 않습니다. 예: `2025-03`", ephemeral=True
            )
            return

        dev_dates = set(self.db.get_monthly_dev_dates(user_id, guild_id, year_month))

        # 이번 달 조회 시 진행 중 세션 반영
        current_dev_date = to_dev_date(now_utc())
        if year_month == current_dev_date[:7]:
            tracker = self.bot.cogs.get("TrackerCog")
            min_dev_secs = tracker.min_dev_secs if tracker else int(os.getenv("MIN_DEV_SECONDS", "5400"))
            today_str_cal = current_dev_date
            today_live, _ = self._live_session_secs(user_id, now_kst)
            today_total = self.db.get_day_total_secs(user_id, guild_id, today_str_cal) + today_live
            if today_total >= min_dev_secs and today_str_cal not in dev_dates:
                dev_dates.add(today_str_cal)

        _, days_in_month = calendar.monthrange(year, month)
        first_weekday = (calendar.monthrange(year, month)[0] + 1) % 7  # 0=일요일

        # 달력 렌더링
        RED   = "\u001b[31m"
        GREEN = "\u001b[32m"
        BLUE  = "\u001b[34m"
        RESET = "\u001b[0m"

        header = f"{RED}일{RESET}    월   화    수   목    금   {BLUE}토{RESET}\n"
        lines = [header]
        row = "     " * first_weekday
        for day in range(1, days_in_month + 1):
            date_str = f"{year_month}-{day:02d}"
            weekday = (first_weekday + day - 1) % 7
            if date_str in dev_dates:
                cell = f"{GREEN} ✓  {RESET} "
            elif weekday == 0:
                cell = f"{RED}{day:2d}{RESET}   "
            elif weekday == 6:
                cell = f"{BLUE}{day:2d}{RESET}   "
            else:
                cell = f"{day:2d}   "
            row += cell
            if weekday == 6:
                lines.append(row)
                row = ""
        if row.strip():
            lines.append(row)

        total_days = len(dev_dates)
        embed = discord.Embed(
            title=f"{target.display_name}의 {year}년 {month}월 개발 달력",
            description="```ansi\n" + "\n".join(lines) + "\n```",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"총 {total_days}일 개발")

        await interaction.response.send_message(embed=embed, )

    @app_commands.command(name="개발랭킹", description="이번 달 개발 시간 랭킹을 확인합니다")
    @in_dev_category()
    async def dev_ranking(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        now_kst = to_kst(now_utc())
        year_month = to_dev_date(now_utc())[:7]

        tracker = self.bot.cogs.get("TrackerCog")
        min_dev_secs = tracker.min_dev_secs if tracker else int(os.getenv("MIN_DEV_SECONDS", "5400"))

        ranking = self.db.get_monthly_ranking(guild_id, year_month, min_dev_secs)

        # 진행 중 세션 반영
        if tracker and tracker.active_sessions:
            now = now_utc()
            today_str_rank = to_dev_date(now)
            day_start_utc = dev_day_start_utc(now)
            month_start, month_end = month_utc_range(year_month)
            rank_dict = {entry["user_id"]: dict(entry) for entry in ranking}

            active_uids = list(tracker.active_sessions.keys())
            active_sids = list(tracker.active_sessions.values())

            join_times = self.db.get_session_join_times_batch(active_sids)
            day_totals = self.db.get_day_total_secs_batch(active_uids, guild_id, today_str_rank)
            dev_dates_map = self.db.get_monthly_dev_dates_batch(active_uids, guild_id, year_month)
            new_uids = [uid for uid in active_uids if uid not in rank_dict]
            monthly_stats = self.db.get_monthly_stats_batch(new_uids, guild_id, year_month)

            for uid, session_id in tracker.active_sessions.items():
                join_time = join_times.get(session_id)
                if not join_time:
                    continue
                if join_time >= month_end:
                    continue
                month_live = max(0, int((now - max(join_time, month_start)).total_seconds()))
                today_live = max(0, int((now - max(join_time, day_start_utc)).total_seconds()))
                today_total = day_totals.get(uid, 0) + today_live
                today_already = today_str_rank in dev_dates_map.get(uid, set())

                if uid in rank_dict:
                    rank_dict[uid]["secs"] += month_live
                    if not today_already and today_total >= min_dev_secs:
                        rank_dict[uid]["days"] += 1
                else:
                    stats = monthly_stats.get(uid, {"days": 0, "secs": 0})
                    days = stats["days"]
                    if not today_already and today_total >= min_dev_secs:
                        days += 1
                    rank_dict[uid] = {"user_id": uid, "secs": stats["secs"] + month_live, "days": days}

            ranking = list(rank_dict.values())

        if not ranking:
            await interaction.response.send_message("이번 달 개발 기록이 없습니다.", ephemeral=True)
            return

        ranking_by_secs = sorted(ranking, key=lambda x: x["secs"], reverse=True)[:5]
        ranking_by_days = sorted(ranking, key=lambda x: (x["days"], x["secs"]), reverse=True)[:5]

        year, month = map(int, year_month.split("-"))
        embed = discord.Embed(
            title=f"{year}년 {month}월 개발 랭킹",
            color=discord.Color.gold(),
        )

        medals = ["🥇", "🥈", "🥉"]

        secs_lines = []
        for i, entry in enumerate(ranking_by_secs):
            member = interaction.guild.get_member(int(entry["user_id"]))
            name = member.display_name if member else "(알 수 없음)"
            prefix = medals[i] if i < 3 else f"{i+1}위"
            secs_lines.append(f"{prefix} **{name}** — {secs_to_str(entry['secs'])} / {entry['days']}일")

        days_lines = []
        for i, entry in enumerate(ranking_by_days):
            member = interaction.guild.get_member(int(entry["user_id"]))
            name = member.display_name if member else "(알 수 없음)"
            prefix = medals[i] if i < 3 else f"{i+1}위"
            days_lines.append(f"{prefix} **{name}** — {entry['days']}일 / {secs_to_str(entry['secs'])}")

        embed.add_field(name="⏱️ 시간 랭킹", value="\n".join(secs_lines), inline=True)
        embed.add_field(name="📅 일수 랭킹", value="\n".join(days_lines), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="사용법", description="봇 사용법을 확인합니다")
    @in_dev_category()
    async def usage(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="개발 추적 봇 사용법",
            description="개발실 음성채팅방 잔류 시간을 자동으로 기록하는 봇입니다.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="자동 기록",
            value=(
                "개발실 카테고리의 음성채널에 입장하면 자동으로 시간 측정이 시작됩니다.\n"
                "퇴장 시 누적 시간이 자동으로 집계되며, 하루 누적 90분 이상 시 개발일로 인정됩니다.\n"
                "나갔다가 다시 들어와도 당일 누적 시간이 합산됩니다."
            ),
            inline=False,
        )
        embed.add_field(
            name="/개발통계 [@유저]",
            value=(
                "본인 또는 특정 유저의 개발 통계를 확인합니다.\n"
                "• `@유저` 생략 시 본인 통계\n"
                "• 연속 개발일 / 최고기록\n"
                "• 이번 달 개발일 / 개발시간\n"
                "• 오늘 개발시간"
            ),
            inline=False,
        )
        embed.add_field(
            name="/개발달력 [@유저] [연월]",
            value=(
                "월별 개발 달력을 확인합니다.\n"
                "• `@유저` 생략 시 본인 달력\n"
                "• `연월` 예시: `2025-03` (생략 시 이번 달)"
            ),
            inline=False,
        )
        embed.add_field(
            name="/개발랭킹",
            value=(
                "이번 달 서버 TOP 5 랭킹을 확인합니다.\n"
                "• 시간 랭킹: 이번 달 총 개발 시간 기준\n"
                "• 일수 랭킹: 이번 달 개발일 수 기준"
            ),
            inline=False,
        )
        dev_contact = os.getenv("DEV_CONTACT", "")
        footer = "통계 명령어는 개발실 카테고리 채널에서만 사용 가능합니다."
        if dev_contact:
            footer += f"\n| 개발자: {dev_contact}"
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed, )


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))
