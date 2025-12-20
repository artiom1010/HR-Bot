import asyncio
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from datetime import datetime
from _database import engine, Order, PaynetStatus
from sqlalchemy.orm import Session
from _key import get_download_keyboard
from handlers.bot_app import bot
from handlers.db_helpers import get_user_lang

app = FastAPI()

# @app.post("/paynet/notify")
# async def paynet_notify(req: Request):
#     body = await req.json()
#     external_id = str(body["Payment"]["ExternalId"])
#     event_date = body["EventDate"]  # строка, например "2025-08-07T11:52:32.953"

#     # Парсим в datetime
#     paid_dt = datetime.fromisoformat(event_date)

#     with Session(engine) as session:
#         order = session.query(Order).filter(Order.invoice == external_id).first()
#         if order:
#             order.paynet_status = PaynetStatus.paid
#             order.paid_at = paid_dt
#             session.commit()

#             # Отправляем сообщение юзеру асинхронно
#             chat_id = order.user.telegram_id
#             text = "💰 Оплата получена! Нажми кнопку ниже, чтобы скачать резюме."
#             kb = get_download_keyboard(order.id)
#             import asyncio
#             asyncio.create_task(bot.send_message(chat_id, text, reply_markup=kb))
#     print("PAYNET NOTIFY:", body)
#     return {"ok": True}
import traceback
import logging

logging.basicConfig(level=logging.INFO)

@app.post("/paynet/notify")
async def paynet_notify(req: Request):
    try:
        body = await req.json()
        print("===> PAYNET BODY:", body)

        external_id = str(body["Payment"]["ExternalId"])
        event_date = body["EventDate"]
        paid_dt = datetime.fromisoformat(event_date)
        print(f"===> external_id: {external_id}, paid_dt: {paid_dt}")

        with Session(engine) as session:
            order = session.query(Order).filter(Order.invoice == external_id).first()
            print("===> ORDER FOUND:", order)

            if order:
                order.paynet_status = PaynetStatus.paid
                order.paid_at = paid_dt
                session.commit()
                print("===> STATUS UPDATED & COMMITTED")

                chat_id = order.user.telegram_id
                lang = get_user_lang(session, order.user_id)
                print("===> chat_id:", chat_id)
                text = "💰 Payment received!\nClick the button below to download the resume."
                kb = get_download_keyboard(order.id, lang)

                asyncio.create_task(bot.send_message(chat_id, text, reply_markup=kb))
                print("===> MESSAGE SENT TASK CREATED")
            else:
                print("===> ORDER NOT FOUND for external_id:", external_id)

        return {"ok": True}

    except Exception as e:
        print("===> ERROR in paynet_notify:", e)
        print(traceback.format_exc())
        return {"ok": False, "error": str(e)}



@app.get("/paynet/getform", response_class=HTMLResponse)
async def get_form(
    operation: str        = Query(...),
    LinkUrlSuccess: str   = Query("https://t.me/hr_md_bot"),
    LinkUrlCancel: str    = Query("https://google.com"),
    ExpiryDate: str       = Query(...),
    Signature: str        = Query(...),
    Lang: str             = Query("ru")
):

    html = f'''
<form method="POST" action="https://test.paynet.md/acquiring/getecom">
    <input type="hidden" name="operation"      value="{operation}"/>
    <input type="hidden" name="LinkUrlSuccess" value="{LinkUrlSuccess}"/>
    <input type="hidden" name="LinkUrlCancel"  value="{LinkUrlCancel}"/>
    <input type="hidden" name="ExpiryDate"     value="{ExpiryDate}"/>
    <input type="hidden" name="Signature"      value="{Signature}"/>
    <input type="hidden" name="Lang"           value="{Lang}"/>
</form>

<script>
    document.forms[0].submit();
</script>
'''
    return HTMLResponse(html)
