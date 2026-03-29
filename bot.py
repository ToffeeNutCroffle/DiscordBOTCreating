import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from database.db import DatabaseManager

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DB_PATH", "./database/dev_tracker.db")

# DB 디렉터리가 없으면 생성
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True
intents.members = True


class DevTrackerBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.db = DatabaseManager(DB_PATH)

    async def setup_hook(self):
        await self.load_extension("cogs.tracker")
        await self.load_extension("cogs.stats")
        # 슬래시 커맨드 글로벌 동기화
        await self.tree.sync()
        print("[Bot] Cog 로드 및 커맨드 동기화 완료")

    async def on_ready(self):
        print(f"[Bot] {self.user} 로그인 완료")
        print(f"[Bot] 참여 서버: {[g.name for g in self.guilds]}")

    async def close(self):
        self.db.close()
        await super().close()


def main():
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
    bot = DevTrackerBot()
    asyncio.run(bot.start(TOKEN))


if __name__ == "__main__":
    main()
