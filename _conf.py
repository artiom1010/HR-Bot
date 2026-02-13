import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DO_API_TOKEN    = os.getenv("DO_API_TOKEN")
DO_AGENT_URL    = os.getenv("DO_AGENT_URL")

PUBLIC_BASE     = os.getenv("PUBLIC_BASE")
API_BASE_URL    = os.getenv("API_BASE_URL")
PORTAL_BASE_URL = os.getenv("PORTAL_BASE_URL")
MERCHANT_CODE   = os.getenv("MERCHANT_CODE")
API_USER        = os.getenv("API_USER")
API_PASS        = os.getenv("API_PASS")
FORM_SECRET_KEY = os.getenv("FORM_SECRET_KEY")
CURRENCY        = os.getenv("CURRENCY")
LINKSUCCESS  = os.getenv("LINKSUCCESS")
LINKERROR  = os.getenv("LINKERROR")