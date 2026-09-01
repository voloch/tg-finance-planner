"""Covers the pinned-status message: it is created and pinned once, then
edited in place forever after, and it never lets a Telegram failure escape
into the write path that triggered it."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from telegram.error import BadRequest

from bot import db, livestatus


class FakeBot:
    """Records calls; each attribute can be made to raise on demand."""

    def __init__(self, edit_error=None, send_error=None, pin_error=None):
        self.sent, self.edited, self.pinned = [], [], []
        self.edit_error, self.send_error, self.pin_error = edit_error, send_error, pin_error
        self._next_id = 100

    async def send_message(self, chat_id, text):
        if self.send_error:
            raise self.send_error
        self._next_id += 1
        self.sent.append((chat_id, text))
        return type("Msg", (), {"message_id": self._next_id})()

    async def edit_message_text(self, chat_id, message_id, text):
        if self.edit_error:
            raise self.edit_error
        self.edited.append((chat_id, message_id, text))

    async def pin_chat_message(self, chat_id, message_id, disable_notification=False):
        if self.pin_error:
            raise self.pin_error
        self.pinned.append((chat_id, message_id))


@pytest.fixture
def conn(tmp_path):
    c = db.get_conn(tmp_path / "pin.db")
    db.init_db(c)
    db.create_category(c, "Supermarket", "🛒", 75000)
    yield c
    c.close()


def test_first_refresh_sends_and_pins_once(conn):
    bot = FakeBot()
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))

    assert len(bot.sent) == 1 and len(bot.pinned) == 1
    assert bot.pinned[0] == (7, 101)
    assert db.get_setting(conn, "status_message_id") == "101"
    assert "Supermarket" in bot.sent[0][1]


def test_second_refresh_edits_instead_of_sending(conn):
    bot = FakeBot()
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))

    # the whole point: no second message, no second pin
    assert len(bot.sent) == 1
    assert len(bot.pinned) == 1
    assert len(bot.edited) == 1
    assert bot.edited[0][1] == 101


def test_deleted_pin_is_recreated(conn):
    bot = FakeBot()
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))
    bot.edit_error = BadRequest("Message to edit not found")
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))

    assert len(bot.sent) == 2
    assert db.get_setting(conn, "status_message_id") == "102"


def test_unmodified_edit_is_not_an_error(conn):
    bot = FakeBot()
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))
    bot.edit_error = BadRequest("Message is not modified")
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))

    assert len(bot.sent) == 1  # did not fall through to recreating


def test_pin_failure_still_records_the_message(conn):
    # If pinning is refused we must not re-send a new message on every write.
    bot = FakeBot(pin_error=BadRequest("Not enough rights"))
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))

    assert len(bot.sent) == 1
    assert len(bot.edited) == 1


def test_send_failure_is_swallowed(conn):
    bot = FakeBot(send_error=BadRequest("Chat not found"))
    asyncio.run(livestatus.refresh(bot, conn, chat_id=7))  # must not raise
    assert db.get_setting(conn, "status_message_id") is None


def test_no_chat_id_is_a_noop(conn):
    bot = FakeBot()
    asyncio.run(livestatus.refresh(bot, conn))  # no home chat recorded yet
    assert not bot.sent


def test_uses_home_chat_when_no_chat_id_given(conn):
    db.set_home_chat_id(conn, 55)
    bot = FakeBot()
    asyncio.run(livestatus.refresh(bot, conn))
    assert bot.sent[0][0] == 55
