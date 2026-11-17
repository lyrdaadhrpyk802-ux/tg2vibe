# ============================================
#  poller.py - polls public Telegram channels once per run and forwards new posts to a Vibe bot chat
# ============================================
from __future__ import annotations

import asyncio
import io
import json
import os
import traceback
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import MessageMediaWebPage

from botapi import VibeBotHTTP
from config import BASE_DIR, Settings, load_settings

STATE_FILE = BASE_DIR / "state.json"
TEXT_LIMIT = 4000          # max characters per text message
CAPTION_LIMIT = 1024       # max characters in a media caption
MEDIA_GROUP_MAX = 10       # max items in one album
MAX_PER_RUN = 50           # max new posts per channel in one run
SEND_ATTEMPTS = 3          # attempts per Vibe call
MAX_JOB_FAILURES = 6       # runs before a permanently failing post is skipped
EMPTY_TEXT = "\u200b"      # zero-width space, used when only buttons must be sent
GROUP_TYPES = {"photo": "photo", "video": "video", "audio": "audio"}


def say(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


@dataclass
class Job:
    title: str
    messages: list


def split_text(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        # Stop instead of resetting: a reset would re-send old posts.
        raise SystemExit(f"[state] error: state.json خراب است، اجرا متوقف شد: {e}")


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def group_jobs(messages: list, title: str) -> list[Job]:
    jobs: list[Job] = []
    for m in messages:
        if jobs and m.grouped_id is not None and jobs[-1].messages[-1].grouped_id == m.grouped_id:
            jobs[-1].messages.append(m)
        else:
            jobs.append(Job(title, [m]))
    return jobs


def is_file(m) -> bool:
    if isinstance(m.media, MessageMediaWebPage):
        return False  # link previews are not files
    return bool(m.photo or m.document)


def media_kind(m) -> str:
    if m.video_note:
        return "video_note"
    if m.voice:
        return "voice"
    if m.gif:
        return "animation"
    if m.video:
        return "video"
    if m.audio:
        return "audio"
    if m.photo and not m.document:
        return "photo"
    return "document"


def inline_markup(messages: list) -> dict | None:
    # msg.buttons is Telethon's high-level view of the inline keyboard:
    # a list of rows, each a list of Button objects with .text and .url.
    for m in messages:
        rows_src = m.buttons
        if not rows_src:
            continue
        rows, dropped = [], 0
        for row in rows_src:
            buttons = []
            for b in row:
                if b.url:
                    buttons.append({"text": b.text, "url": b.url})
                else:
                    dropped += 1  # callback buttons belong to the source bot
            if buttons:
                rows.append(buttons)
        if dropped:
            say("forwarder", f"{dropped} دکمه غیرلینکی حذف شد.")
        if rows:
            return {"inline_keyboard": rows}
    return None


class Poller:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.tg = TelegramClient(
            StringSession(settings.tg_session),
            settings.tg_api_id,
            settings.tg_api_hash,
            proxy=settings.tg_proxy,
            connection_retries=5,
            retry_delay=3,
        )
        self.bot = VibeBotHTTP(settings.vibe_token, base_url=settings.vibe_base_url)
        self.state = load_state()

    # ---------- lifecycle ----------
    async def run(self) -> None:
        try:
            await self.tg.connect()
            if not await self.tg.is_user_authorized():
                raise SystemExit("[tg] error: TG_SESSION معتبر نیست.")
            me = await self.bot.get_me()
            say("vibe", f"ربات متصل شد: {me.get('username', '?')}")

            for ref in self.s.channels:
                entity = await self.resolve(ref)
                if entity is None:
                    continue
                try:
                    await self.process(entity)
                except Exception as e:
                    print(f"[poller] error: کانال «{ref}»: {e}")
                    traceback.print_exc()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        try:
            await self.bot.close()
        except Exception as e:
            print(f"[vibe] error: close: {e}")
        try:
            await self.tg.disconnect()
        except Exception as e:
            print(f"[tg] error: disconnect: {e}")

    async def resolve(self, ref):
        try:
            entity = await self.tg.get_entity(ref)
        except Exception as e:
            print(f"[tg] error: کانال «{ref}» پیدا نشد: {e}")
            return None
        if self.s.auto_join and getattr(entity, "left", False):
            try:
                await self.tg(JoinChannelRequest(entity))
                say("tg", f"عضو «{ref}» شد.")
            except Exception as e:
                print(f"[tg] error: عضویت در «{ref}»: {e}")
        return entity

    # ---------- per channel ----------
    async def process(self, entity) -> None:
        key = str(entity.id)
        title = getattr(entity, "title", "") or ""
        last = self.state.get(key)

        if last is None:
            if self.s.backfill <= 0:
                newest = await self.tg.get_messages(entity, limit=1)
                self.state[key] = newest[0].id if newest else 0
                save_state(self.state)
                say("poller", f"«{title}» ثبت شد؛ از پست بعدی ارسال می‌شود.")
                return
            recent = [m async for m in self.tg.iter_messages(entity, limit=self.s.backfill)]
            recent.reverse()
            messages = recent
        else:
            messages = [
                m async for m in self.tg.iter_messages(
                    entity, min_id=last, reverse=True, limit=MAX_PER_RUN
                )
            ]

        if not messages:
            if key not in self.state:
                self.state[key] = 0
                save_state(self.state)
            return

        for job in group_jobs(messages, title):
            try:
                await self.deliver(job)
            except Exception as e:
                self.record_failure(key, job, e)
                return
            self.state[key] = job.messages[-1].id
            save_state(self.state)
            await asyncio.sleep(self.s.send_delay)

    def record_failure(self, key: str, job: Job, exc: Exception) -> None:
        failures = self.state.setdefault("failures", {})
        fk = f"{key}:{job.messages[-1].id}"
        failures[fk] = failures.get(fk, 0) + 1
        if failures[fk] >= MAX_JOB_FAILURES:
            say("poller", f"پیام {fk} بعد از {MAX_JOB_FAILURES} شکست رد شد.")
            self.state[key] = job.messages[-1].id
            failures.pop(fk, None)
        else:
            print(f"[vibe] error: ارسال «{job.title}» ناموفق ({failures[fk]}/{MAX_JOB_FAILURES}): {exc}")
        save_state(self.state)

    # ---------- delivery ----------
    async def download(self, m, notes: list[str]):
        limit = self.s.media_limit_mb * 1024 * 1024
        size = getattr(m.file, "size", None) if m.file else None
        if size and size > limit:
            notes.append(f"[فایل بزرگ‌تر از {self.s.media_limit_mb}MB بود و ارسال نشد]")
            return None
        buf = io.BytesIO()
        try:
            await self.tg.download_media(m, file=buf)
        except Exception as e:
            print(f"[tg] error: دانلود پیام {m.id}: {e}")
            notes.append("[یک رسانه دریافت نشد]")
            return None
        if buf.getbuffer().nbytes == 0:
            return None
        ext = m.file.ext if m.file and m.file.ext else ""
        buf.name = m.file.name if m.file and m.file.name else f"media_{m.id}{ext}"
        return (media_kind(m), buf)

    async def retry(self, factory):
        last: Exception | None = None
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                return await factory()
            except Exception as e:
                last = e
                print(f"[vibe] error: تلاش {attempt}/{SEND_ATTEMPTS}: {e}")
                await asyncio.sleep(2 * attempt)
        raise last  # type: ignore[misc]

    async def send_single(self, kind: str, buf: io.BytesIO, caption, markup):
        buf.seek(0)
        chat = self.s.vibe_chat_id
        if kind == "photo":
            return await self.bot.send_photo(chat_id=chat, photo=buf, caption=caption,
                                             reply_markup=markup)
        if kind == "video":
            return await self.bot.send_video(chat_id=chat, video=buf, caption=caption,
                                             supports_streaming=True, reply_markup=markup)
        if kind == "video_note":
            return await self.bot.send_video_note(chat_id=chat, video_note=buf,
                                                  reply_markup=markup)
        if kind == "voice":
            return await self.bot.send_voice(chat_id=chat, voice=buf, caption=caption,
                                             reply_markup=markup)
        if kind == "audio":
            return await self.bot.send_audio(chat_id=chat, audio=buf, caption=caption,
                                             reply_markup=markup)
        if kind == "animation":
            return await self.bot.send_animation(chat_id=chat, animation=buf, caption=caption,
                                                 reply_markup=markup)
        return await self.bot.send_document(chat_id=chat, document=buf, caption=caption,
                                            reply_markup=markup)

    async def send_group(self, batch: list, caption):
        media = []
        for i, (kind, buf) in enumerate(batch):
            buf.seek(0)
            item = {"type": GROUP_TYPES.get(kind, "document"), "media": buf}
            if i == 0 and caption:
                item["caption"] = caption
            media.append(item)
        return await self.bot.send_media_group(chat_id=self.s.vibe_chat_id, media=media)

    async def send_text(self, text: str, markup):
        return await self.bot.send_message(chat_id=self.s.vibe_chat_id, text=text,
                                           reply_markup=markup)

    async def deliver(self, job: Job) -> None:
        msgs = job.messages
        notes: list[str] = []
        markup = inline_markup(msgs)

        items = []
        for m in msgs:
            if is_file(m):
                got = await self.download(m, notes)
                if got is not None:
                    items.append(got)

        text = "\n".join(
            m.raw_text.strip() for m in msgs if m.raw_text and m.raw_text.strip()
        ).strip()
        if notes:
            text = (text + "\n\n" + "\n".join(notes)).strip()
        if self.s.include_source and job.title and text:
            text = f"«{job.title}»\n\n{text}"

        if not items and not text and not markup:
            say("poller", "پیام بدون محتوای قابل ارسال بود.")
            return

        if not items:
            parts = split_text(text, TEXT_LIMIT) or [EMPTY_TEXT]
            for i, part in enumerate(parts):
                mk = markup if i == len(parts) - 1 else None
                await self.retry(lambda p=part, k=mk: self.send_text(p, k))
            say("vibe", f"پیام از «{job.title}» ارسال شد.")
            return

        single_video_note = len(items) == 1 and items[0][0] == "video_note"
        caption = None
        if text and not single_video_note and len(text) <= CAPTION_LIMIT:
            caption = text
        rest = split_text(text, TEXT_LIMIT) if (text and caption is None) else []

        markup_sent = False
        if len(items) == 1:
            kind, buf = items[0]
            mk = markup if not rest else None
            await self.retry(lambda: self.send_single(kind, buf, caption, mk))
            markup_sent = mk is not None
        else:
            # Telegram albums cannot carry reply_markup, so buttons go in a follow-up message.
            for start in range(0, len(items), MEDIA_GROUP_MAX):
                batch = items[start:start + MEDIA_GROUP_MAX]
                cap = caption if start == 0 else None
                if len(batch) == 1:
                    kind, buf = batch[0]
                    await self.retry(lambda k=kind, b=buf, c=cap: self.send_single(k, b, c, None))
                else:
                    await self.retry(lambda b=batch, c=cap: self.send_group(b, c))

        if rest or (markup and not markup_sent):
            parts = rest or [EMPTY_TEXT]
            for i, part in enumerate(parts):
                mk = markup if (i == len(parts) - 1 and not markup_sent) else None
                await self.retry(lambda p=part, k=mk: self.send_text(p, k))

        say("vibe", f"{len(items)} فایل از «{job.title}» ارسال شد.")


def main() -> None:
    settings = load_settings()
    asyncio.run(Poller(settings).run())


if __name__ == "__main__":
    main()
