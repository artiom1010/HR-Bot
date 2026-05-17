import asyncio
import traceback
import logging
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from datetime import datetime
from sqlalchemy import text

from _database import engine, Order, PaynetStatus
from _key import get_download_keyboard, get_cover_letter_keyboard
from handlers.bot_app import bot
from handlers.db_helpers import get_user_lang, get_resume_lang
from i18n import t
from stats_api import router as stats_router
from alerts import alert

from _conf import (LINKSUCCESS, LINKERROR)

app = FastAPI()


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
                resume_lang = get_resume_lang(session, order.user_id)
                print("===> chat_id:", chat_id)

                payment_text = t("💰 Оплата получена!\nНажми кнопку ниже, чтобы скачать резюме.", lang)
                kb = get_download_keyboard(order.id, lang)
                asyncio.create_task(bot.send_message(chat_id, payment_text, reply_markup=kb))

                bonus_text = t("🎁 Поздравляем с покупкой!\n\nВ подарок — шаблон письма для отклика на вакансию.\nСкачай его по кнопке ниже:", lang)
                cover_kb = get_cover_letter_keyboard(order.id, lang)
                asyncio.create_task(bot.send_message(chat_id, bonus_text, reply_markup=cover_kb))

                print("===> MESSAGES SENT TASKS CREATED")
            else:
                print("===> ORDER NOT FOUND for external_id:", external_id)

        return {"ok": True}

    except Exception as e:
        tb = traceback.format_exc()
        print("===> ERROR in paynet_notify:", e)
        print(tb)
        try:
            await alert.send(
                f"🔴 PAYNET WEBHOOK FAILED\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Юзер мог заплатить — проверь вручную!\n\n"
                f"Error: {e}\n\n"
                f"Traceback (tail):\n{tb[-800:]}",
                level="ERROR",
            )
        except Exception as alert_err:
            print("===> alert.send failed:", alert_err)
        return {"ok": False, "error": str(e)}



# @app.get("/paynet/getform", response_class=HTMLResponse)
# async def get_form(
#     operation: str        = Query(...),
#     LinkUrlSuccess: str   = Query(LINKSUCCESS),
#     LinkUrlCancel: str    = Query(LINKERROR),
#     ExpiryDate: str       = Query(...),
#     Signature: str        = Query(...),
#     Lang: str             = Query("ru")
# ):

#     print(f"DEBUG getform:")
#     print(f"  operation: {operation}")
#     print(f"  LinkUrlSuccess: {LinkUrlSuccess}")
#     print(f"  LinkUrlCancel: {LinkUrlCancel}")
#     print(f"  LINKSUCCESS from conf: {LINKSUCCESS}")
#     print(f"  LINKERROR from conf: {LINKERROR}")

#     html = f'''
# <form method="POST" action="https://test.paynet.md/acquiring/getecom">
#     <input type="hidden" name="operation"      value="{operation}"/>
#     <input type="hidden" name="LinkUrlSucces" value="{LinkUrlSuccess}"/>
#     <input type="hidden" name="LinkUrlCancel"  value="{LinkUrlCancel}"/>
#     <input type="hidden" name="ExpiryDate"     value="{ExpiryDate}"/>
#     <input type="hidden" name="Signature"      value="{Signature}"/>
#     <input type="hidden" name="Lang"           value="{Lang}"/>
# </form>

# <script>
#     document.forms[0].submit();
# </script>
# '''
#     return HTMLResponse(html)



app.include_router(stats_router)

@app.head("/stats")
async def stats_head():
    """HEAD запрос для мониторинга — проверяет что база отвечает"""
    with Session(engine) as s:
        s.execute(text("SELECT 1"))  # проверка базы
    return {}