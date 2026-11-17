# ============================================
#  get_chat_id.py - prints chat ids that have messaged the Vibe bot (run after sending /start)
# ============================================
import asyncio

from botapi import VibeBotHTTP
from config import opt, req


async def main() -> None:
    token = req("VIBE_BOT_TOKEN")
    base = opt("VIBE_BASE_URL") or "https://api.vibegram.chat"
    async with VibeBotHTTP(token, base_url=base) as bot:
        updates = await bot.get_updates(limit=50, timeout=0)
        if not updates:
            print("[bot] error: پیامی پیدا نشد. ابتدا به ربات /start بدهید و دوباره اجرا کنید.")
            return
        seen = set()
        for u in updates:
            msg = u.get("message") or u.get("channel_post") or {}
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            name = chat.get("title") or chat.get("first_name") or chat.get("username") or "?"
            print(f"chat_id={cid}    name={name}")


if __name__ == "__main__":
    asyncio.run(main())
