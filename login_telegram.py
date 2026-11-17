# ============================================
#  login_telegram.py - one-time login of the Telegram user account (produces TG_SESSION)
# ============================================
import asyncio
from getpass import getpass
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import BASE_DIR, opt, req, telethon_proxy


async def main() -> None:
    client = TelegramClient(
        StringSession(), int(req("TG_API_ID")), req("TG_API_HASH"),
        proxy=telethon_proxy(opt("TG_PROXY")),
    )
    try:
        await client.start(
            phone=lambda: input("شماره تلفن تلگرام با کد کشور: ").strip(),
            code_callback=lambda: input("کد ارسال‌شده توسط تلگرام: ").strip(),
            password=lambda: getpass("رمز دومرحله‌ای: "),
        )
        me = await client.get_me()
        out: Path = BASE_DIR / ".tg_session.txt"
        out.write_text(client.session.save(), encoding="utf-8")
        print(f"ورود موفق: {me.first_name} (id={me.id})")
        print(f"StringSession در {out} ذخیره شد.")
        print("مقدارش را در TG_SESSION فایل .env بگذارید، سپس این فایل را پاک کنید.")
    except Exception as e:
        print(f"[login] error: {e}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
