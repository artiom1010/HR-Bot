import httpx, random
from sqlalchemy.orm import Session
import urllib
from _database import engine, Order

from datetime import datetime, timedelta

from _conf import (
    PUBLIC_BASE,
    API_BASE_URL,
    MERCHANT_CODE,
    API_USER,
    API_PASS,
    CURRENCY,
    LINKSUCCESS,
    LINKERROR
)

# --- Сервисные функции -------------------------------------------
def new_invoice() -> str:
    rnd = random.randint(1000000, 9999999)
    return f"{rnd}"

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
    # external_dt = now
    # expiry_dt = now + timedelta(hours=5)
    external_dt = int(now.timestamp())
    expiry_dt = int((now + timedelta(hours=5)).timestamp())

    with Session(engine) as s:
        order = s.get(Order, order_id)
        if not order:
            raise RuntimeError(f"Order {order_id} not found")

    body = {
        "Invoice": order.invoice,
        "MerchantCode": MERCHANT_CODE,
        "LinkUrlSuccess": LINKSUCCESS,
        "LinkUrlCancel":  LINKERROR,
        "Signature": "null",
        "SignVersion": "v01",
        "Customer": {
            "Code":"sary.artema@mail.ru",
            "Name":"",
            "NameFirst":"",
            "NameLast":"",
            "email":"",
            "Country":"Moldova",
            "City": "Chisinau",
            "Address":"www.paynet.md",
            "PhoneNumber":""
            },
        "Payer": "null",
        "Currency": CURRENCY,
        "ExternalDate": external_dt,
        "ExpiryDate": expiry_dt,
        "Services": [{
            "Name": "HR-Bot Resume",
            "Description": "Professional CV",
            "Amount": order.amount_bani,
            "Products": [{
                "LineNo": 1,
                "Code": "RESUME",
                "Name": "Professional Resume",
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
    print("Response headers:", r.headers)
    print("Full response text:", r.text)

    r.raise_for_status()
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
        "LinkUrlSuccess": LINKSUCCESS,
        "LinkUrlCancel":  LINKERROR,
        "ExpiryDate":     expirydate,
        "Signature":      signature,
        "Lang":           "ru"
    }
    query = urllib.parse.urlencode(params)
    return f"{PUBLIC_BASE}/paynet/proxy?{query}"
