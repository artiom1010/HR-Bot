import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DO_API_TOKEN = os.getenv("DO_API_TOKEN")
DO_AGENT_URL = os.getenv("DO_AGENT_URL")