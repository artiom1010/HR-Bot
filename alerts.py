# alerts.py
import os, time, asyncio, traceback, json, aiohttp
import logging
import aiohttp

def _now() -> float: return time.time()

class AlertClient:
    def __init__(self):
        self.tg_token  = os.getenv("TELEGRAM_ALERT_BOT_TOKEN", "").strip() or None
        ids = os.getenv("TELEGRAM_ALERT_CHAT_IDS", "").strip()
        self.tg_chat_ids = [i.strip() for i in ids.split(",") if i.strip()] if ids else []
        self.prefix = os.getenv("ALERT_PREFIX", "HRBot")
        self._dedupe_ts: dict[str, float] = {}

    def _fmt(self, level: str, text: str) -> str:
        head = f"{self.prefix} {level}"
        return f"{head}\n{text}".strip()[:3900]  # запас под лимиты

    async def send(self, text: str, level: str = "INFO") -> bool:
        msg = self._fmt(level, text)
        ok = False
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            if self.tg_token and self.tg_chat_ids:
                url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
                for chat_id in self.tg_chat_ids:
                    try:
                        logging.info("alert.send -> TG chat=%s, chars=%s", chat_id, len(msg))
                        resp = await s.post(
                            url,
                            data={"chat_id": chat_id, "text": msg, "disable_web_page_preview": True},
                        )
                        if resp.status == 200:
                            ok = True
                        else:
                            body = (await resp.text())[:500]
                            logging.warning("alert.send TG %s: %s", resp.status, body)
                    except Exception as e:
                        logging.exception("alert.send TG failed: %s", e)
                    await asyncio.sleep(0.05)  # микро-антифлуд
        return ok

    async def send_once(self, key: str, text: str, ttl: int = 300, level: str = "ERROR"):
        """Не спамим одинаковыми ошибками чаще, чем раз в ttl секунд."""
        now = _now()
        last = self._dedupe_ts.get(key, 0)
        if now - last >= ttl:
            self._dedupe_ts[key] = now
            await self.send(text, level=level)

    async def send_document(self, file_path: str, caption: str = "") -> bool:
        """Отправляет файл в алерт-каналы."""
        if not self.tg_token or not self.tg_chat_ids:
            return False
        
        ok = False
        timeout = aiohttp.ClientTimeout(total=30)
        url = f"https://api.telegram.org/bot{self.tg_token}/sendDocument"
        
        async with aiohttp.ClientSession(timeout=timeout) as s:
            for chat_id in self.tg_chat_ids:
                try:
                    with open(file_path, "rb") as f:
                        data = aiohttp.FormData()
                        data.add_field("chat_id", chat_id)
                        data.add_field("document", f, filename=os.path.basename(file_path))
                        if caption:
                            data.add_field("caption", caption[:1024])
                        
                        resp = await s.post(url, data=data)
                        if resp.status == 200:
                            ok = True
                        else:
                            body = (await resp.text())[:500]
                            logging.warning("alert.send_document TG %s: %s", resp.status, body)
                except Exception as e:
                    logging.exception("alert.send_document failed: %s", e)
                await asyncio.sleep(0.05)
        return ok

    @staticmethod
    def exc_text(e: BaseException, extra: dict | None = None) -> str:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        if extra:
            try:
                extras = json.dumps(extra, ensure_ascii=False)
            except Exception:
                extras = str(extra)
            return f"{extras}\n{tb[-2500:]}"
        return tb[-3000:]

alert = AlertClient()

def alert_on_exception(ttl: int = 300):
    """Декоратор: при исключении шлёт единичный алёрт в Slack/Telegram и пробрасывает ошибку дальше."""
    def deco(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                key = f"{func.__name__}:{type(e).__name__}:{str(e)[:80]}"
                txt = f"❌ {func.__name__} failed: {e}\n" + AlertClient.exc_text(e, extra={"kwargs": kwargs})
                await alert.send_once(key, txt, ttl=ttl, level="ERROR")
                raise
        return wrapper
    return deco