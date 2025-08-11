# OLD run.py
# from aiogram import Bot, Dispatcher
# from aiogram.fsm.storage.memory import MemoryStorage
# import asyncio
# import logging

# from _conf import TELEGRAM_BOT_TOKEN
# # from oooldv2.utils import set_bot_commands
# from _handl import router

# # Настройка логов
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
# )

# # FSM storage
# storage = MemoryStorage()

# # Инициализация бота
# bot = Bot(token=TELEGRAM_BOT_TOKEN)
# dp = Dispatcher(storage=storage)


# # Запуск
# async def main():
#     dp.include_router(router)
#     # await set_bot_commands(bot)
#     try:
#         await dp.start_polling(bot)
#     finally:
#         await bot.session.close()


# if __name__ == "__main__":
#     asyncio.run(main())









# NEW run.py
# run.py
import asyncio, logging
from handlers.bot_app import bot, dp
from handlers.user    import router as user_router
from handlers.order   import router as order_router
from handlers.payment import router as payment_router
from _utils import set_bot_commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

async def main():
    dp.include_router(user_router)
    dp.include_router(order_router)
    dp.include_router(payment_router)
    await set_bot_commands(bot)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())