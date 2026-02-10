# run.py
import asyncio, logging
from handlers.bot_app import bot, dp
from handlers.user    import router as user_router
from handlers.order   import router as order_router
from handlers.payment import router as payment_router
from _utils import set_bot_commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Отключить verbose логи WeasyPrint и fontTools
logging.getLogger("weasyprint").setLevel(logging.WARNING)
logging.getLogger("fontTools").setLevel(logging.WARNING)

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