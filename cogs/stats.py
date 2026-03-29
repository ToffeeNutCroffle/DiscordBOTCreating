import calendar
import discord
from discord import app_commands
from discord.ext import commands
from database.db import DatabaseManager, now_utc, to_kst


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

    @app_commands.command(name="개발통계", description="개발 활동 통계를 확인합니다")
    @app_commands.describe(유저="통계를 조회할 유저 (생략 시 본인)")
    async def dev_stats(self, interaction: discord.Interaction, 유저: discord.Member = None):
        target = 유저 or interaction.user
        user_id = str(target.id)
        guild_id = str(interaction.guild_id)

        now_kst = to_kst(now_utc())
        today_str = now_kst.strftime("%Y-%m-%d")
        year_month = now_kst.strftime("%Y-%m")

        # 현재 개발실에 체류 중인지 확인
        tracker = self.bot.cogs.get("TrackerCog")
        is_active = tracker is not None and user_id in tracker.active_sessions

        consecutive = self.db.get_consecutive_days(user_id, guild_id, today_str, include_today=is_active)
        max_streak = self.db.get_max_streak(user_id, guild_id)
        monthly_days = self.db.get_monthly_days(user_id, guild_id, year_month)
        monthly_secs = self.db.get_monthly_secs(user_id, guild_id, year_month)
        today_secs = self.db.get_day_total_secs(user_id, guild_id, today_str)

        # 현재 연속일이 최대 기록이면 갱신 중 표시
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
        if today_secs > 0:
            embed.add_field(name="오늘 개발시간", value=secs_to_str(today_secs), inline=False)
        embed.set_thumbnail(url=target.display_avatar.url)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="개발달력", description="월별 개발 달력을 확인합니다")
    @app_commands.describe(
        유저="조회할 유저 (생략 시 본인)",
        연월="조회할 연월 (예: 2025-03, 생략 시 이번 달)",
    )
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
        _, days_in_month = calendar.monthrange(year, month)
        first_weekday = calendar.monthrange(year, month)[0]  # 0=월요일

        # 달력 렌더링
        header = "월  화  수  목  금  토  일\n"
        lines = [header]
        row = "    " * first_weekday
        for day in range(1, days_in_month + 1):
            date_str = f"{year_month}-{day:02d}"
            if date_str in dev_dates:
                cell = "✅ "
            else:
                cell = f"{day:2d}  "
            row += cell
            weekday = (first_weekday + day - 1) % 7
            if weekday == 6:
                lines.append(row)
                row = ""
        if row.strip():
            lines.append(row)

        total_days = len(dev_dates)
        embed = discord.Embed(
            title=f"{target.display_name}의 {year}년 {month}월 개발 달력",
            description="```\n" + "\n".join(lines) + "\n```",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"총 {total_days}일 개발")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="개발랭킹", description="이번 달 개발 시간 랭킹을 확인합니다")
    async def dev_ranking(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        now_kst = to_kst(now_utc())
        year_month = now_kst.strftime("%Y-%m")

        ranking = self.db.get_monthly_ranking(guild_id, year_month)

        if not ranking:
            await interaction.response.send_message("이번 달 개발 기록이 없습니다.", ephemeral=True)
            return

        year, month = now_kst.year, now_kst.month
        embed = discord.Embed(
            title=f"{year}년 {month}월 개발 랭킹",
            color=discord.Color.gold(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, entry in enumerate(ranking):
            member = interaction.guild.get_member(int(entry["user_id"]))
            name = member.display_name if member else f"(알 수 없음)"
            prefix = medals[i] if i < 3 else f"{i+1}위"
            lines.append(
                f"{prefix} **{name}** — {entry['days']}일 / {secs_to_str(entry['secs'])}"
            )

        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))
