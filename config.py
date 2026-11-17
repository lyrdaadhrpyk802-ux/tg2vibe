# ============================================
#  config.py - settings loader from environment variables and .env
# ============================================
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import socks

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)
    except OSError as e:
        print(f"[config] error: {e}")


_load_dotenv(BASE_DIR / ".env")


def opt(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def req(name: str) -> str:
    value = opt(name)
    if not value:
        raise SystemExit(f"[config] متغیر «{name}» تنظیم نشده است.")
    return value


_PROXY_TYPES = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}


def telethon_proxy(url: str):
    if not url:
        return None
    parsed = urlparse(url)
    ptype = _PROXY_TYPES.get(parsed.scheme.lower())
    if ptype is None or not parsed.hostname or not parsed.port:
        raise SystemExit(f"[config] فرمت پروکسی نامعتبر است: {url}")
    if parsed.username:
        return (ptype, parsed.hostname, parsed.port, True,
                unquote(parsed.username), unquote(parsed.password or ""))
    return (ptype, parsed.hostname, parsed.port)


def parse_channels(raw: str) -> list:
    refs: list = []
    for item in raw.replace("\n", ",").split(","):
        item = item.strip()
        if not item:
            continue
        for prefix in ("https://", "http://", "t.me/", "telegram.me/"):
            item = item.replace(prefix, "")
        item = item.strip("/").lstrip("@")
        if not item:
            continue
        refs.append(int(item) if item.lstrip("-").isdigit() else item)
    return refs


def parse_chat_id(raw: str) -> int | str:
    return int(raw) if raw.lstrip("-").isdigit() else raw


@dataclass(frozen=True)
class Settings:
    tg_api_id: int
    tg_api_hash: str
    tg_session: str
    tg_proxy: tuple | None
    channels: list
    auto_join: bool
    vibe_token: str
    vibe_chat_id: int | str
    vibe_base_url: str
    include_source: bool
    media_limit_mb: int
    send_delay: float
    backfill: int


def load_settings() -> Settings:
    api_id = req("TG_API_ID")
    if not api_id.isdigit():
        raise SystemExit("[config] TG_API_ID باید عدد باشد.")
    channels = parse_channels(req("TG_CHANNELS"))
    if not channels:
        raise SystemExit("[config] TG_CHANNELS خالی است.")
    return Settings(
        tg_api_id=int(api_id),
        tg_api_hash=req("TG_API_HASH"),
        tg_session=req("TG_SESSION"),
        tg_proxy=telethon_proxy(opt("TG_PROXY")),
        channels=channels,
        auto_join=opt("TG_AUTO_JOIN", "1") == "1",
        vibe_token=req("VIBE_BOT_TOKEN"),
        vibe_chat_id=parse_chat_id(req("VIBE_CHAT_ID")),
        vibe_base_url=opt("VIBE_BASE_URL") or "https://api.vibegram.chat",
        include_source=opt("INCLUDE_SOURCE", "1") == "1",
        media_limit_mb=int(opt("MEDIA_LIMIT_MB", "50")),
        send_delay=float(opt("SEND_DELAY", "1.0")),
        backfill=int(opt("BACKFILL", "0")),
    )
