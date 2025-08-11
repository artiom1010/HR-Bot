import logging
from aiogram.types import BotCommand
from aiogram import Bot

# Включаем логирование
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="/start", description="📄 Старт"),
        BotCommand(command="/new_interview", description="🔄 Новое интервью")
    ]
    await bot.set_my_commands(commands)