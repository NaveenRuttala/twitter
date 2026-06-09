import httpx

from .config import get_settings


async def notify(text: str) -> None:
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                url,
                json={
                    "chat_id": s.telegram_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except Exception:
        # notifications are best-effort; never let them break the poller
        pass
