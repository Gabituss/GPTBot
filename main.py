import os
import logging
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeDefault
from dotenv import load_dotenv
from handlers import setup_handlers
from db import init_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

setup_handlers(dp, bot)

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить/Перезапустить бота"),
        BotCommand(command="reset", description="Сбросить текущий диалог"),
        BotCommand(command="select", description="Выбрать нейросетевую модель")
    ]
    await bot.set_my_commands(commands, BotCommandScopeDefault())

async def main():
    logger.info("Bot started polling.")
    await init_db()
    await set_bot_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())