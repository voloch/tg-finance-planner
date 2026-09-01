"""A single pinned message showing the current period's status, edited in
place instead of re-sent.

Alex wanted the status to be there whenever he opens the bot, without it
being reprinted after every message. A pinned message is the one place
Telegram will show text unconditionally at the top of a chat, so the bot
keeps exactly one of them and rewrites its contents after every write. The
message id lives in settings so the pin survives a restart.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from telegram.error import TelegramError

from bot import db, periods, reports

logger = logging.getLogger(__name__)

_SETTING = "status_message_id"


def build_text(conn) -> str:
    period = periods.period_containing(date.today(), db.get_cycle_day(conn))
    stamp = datetime.now().strftime("%d/%m %H:%M")
    # Plain text, no parse_mode -- category names may contain _ or *,
    # and the same reasoning keeps the confirmation cards unformatted.
    return f"{reports.build_status_text(conn, period)}\n\n🔄 atualizado {stamp}"


async def refresh(bot, conn, chat_id: int | None = None) -> None:
    """Rewrites the pinned status, creating and pinning it if it's missing.

    Called for its side effect from write paths, so it swallows every
    Telegram error -- a chat-decoration failure must never turn a successful
    expense into an error reply.
    """
    chat_id = chat_id or db.get_home_chat_id(conn)
    if not chat_id:
        return  # bot hasn't been talked to yet; nothing to pin to

    try:
        text = build_text(conn)
    except Exception:
        logger.exception("Could not build the pinned status text")
        return

    raw_id = db.get_setting(conn, _SETTING)
    if raw_id:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=int(raw_id), text=text)
            return
        except TelegramError as exc:
            if "not modified" in str(exc).lower():
                return  # nothing changed since the last write
            # Deleted, unpinned-and-cleared, or too old to edit -- fall
            # through and start a fresh one.
            logger.info("Pinned status %s no longer editable (%s); recreating", raw_id, exc)

    await _create(bot, conn, chat_id, text)


async def _create(bot, conn, chat_id: int, text: str) -> None:
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text)
    except TelegramError:
        logger.exception("Could not send the status message")
        return
    # Record it before pinning: if the pin fails we still want to edit this
    # message next time rather than pile up a new one on every write.
    db.set_setting(conn, _SETTING, str(msg.message_id))
    try:
        await bot.pin_chat_message(
            chat_id=chat_id, message_id=msg.message_id, disable_notification=True
        )
    except TelegramError:
        logger.warning("Status message sent but could not be pinned", exc_info=True)
