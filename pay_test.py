import os, json, httpx, time, random
from sqlalchemy.orm import Session
import urllib
from _database import engine, Order

from datetime import datetime, timedelta
from _utils import logger

from _conf import (
    PUBLIC_BASE,
    API_BASE_URL,
    PORTAL_BASE_URL,
    MERCHANT_CODE,
    API_USER,
    API_PASS,
    FORM_SECRET_KEY,
    CURRENCY,
    NOTIFY_LINK_OK,
    NOTIFY_LINK_NG
)

# --- Константы ----------------------------------------------------

# --- Сервисные функции -------------------------------------------
def new_invoice() -> str:
    ts = int(time.time() * 1000) % 10**9
    rnd = random.randint(100, 999)
    return f"{ts:09}{rnd}"

def get_token() -> str:
    r = httpx.post(
        f"{API_BASE_URL}/auth",
        data={"username": API_USER, "password": API_PASS, "grant_type": "password"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15, verify=False
    )
    r.raise_for_status()
    return r.json()["access_token"]

def register_payment(order_id: int):
    
    now = datetime.now()
    external_dt = now
    expiry_dt = now + timedelta(hours=1)

    with Session(engine) as s:
        order = s.get(Order, order_id)
        if not order:
            raise RuntimeError(f"Order {order_id} not found")

    body = {
        "Invoice": order.invoice,
        "MerchantCode": MERCHANT_CODE,
        "LinkUrlSuccess": NOTIFY_LINK_OK,
        "LinkUrlCancel":  NOTIFY_LINK_NG,
        "Signature": "null",
        "SignVersion": "v01",
        "Customer": {
            "Code":"sary.artema@mail.ru",
            "Name":"Artiom",
            "NameFirst":"Sary",
            "NameLast":"Name last ",
            "email":"sary.artema@mail.ru",
            "Country":"Moldova",
            "City": "Chisinau",
            "Address":"www.paynet.md",
            "PhoneNumber":"67309067"
            },
        "Payer": "null",
        "Currency": CURRENCY,
        "ExternalDate": external_dt.isoformat(timespec="seconds"),
        "ExpiryDate": expiry_dt.isoformat(timespec="seconds"),
        "Services": [{
            "Name": "Cart of products",
            "Description": "Cart of products",
            "Amount": order.amount_bani,
            "Products": [{
                "LineNo": 1,
                "Code": "RESUME",
                "Name": "Resume",
                "Amount": order.amount_bani, 
                "TotalAmount": order.amount_bani
            }]
        }]
    }

    token = get_token()
    with httpx.Client(timeout=15, verify=False) as client:
        r = client.post(
            f"{API_BASE_URL}/api/Payments/Send",
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8"
            }
        )

    print("Status code:", r.status_code)
    print("Full response text:", r.text)

    resp = r.json()

    payment_id = str(resp["PaymentId"])
    signature = resp["Signature"]
    expirydate = resp["ExpiryDate"]

    with Session(engine) as session:
        order = session.get(Order, order_id)
        if not order:
            raise RuntimeError(f"Order {order_id} not found for saving payment_id")
        order.payment_id = payment_id
        session.commit()

    params = {
        "operation":      str(payment_id),
        "LinkUrlSuccess": NOTIFY_LINK_OK,
        "LinkUrlCancel":  NOTIFY_LINK_NG,
        "ExpiryDate":     expirydate,
        "Signature":      signature,
        "Lang":           "ru"
    }
    query = urllib.parse.urlencode(params)
    return f"{PUBLIC_BASE}/paynet/getform?{query}"

# ***REMOVED***paynet/getform?payment_id=123&signature=abc&expirydate=3423424