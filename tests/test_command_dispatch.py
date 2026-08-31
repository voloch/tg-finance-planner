"""Exercises bot/handlers/text.py._handle_commands end to end against a real
sqlite DB, but with lightweight fake Telegram objects instead of a live bot
-- no network, no Application. This is the highest-risk new code path (LLM
output -> write-confirmation-card vs. read-now), so it's worth covering
directly rather than only through the Action-level unit tests."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import db
from bot.handlers import text as text_handler
from bot.llm import CommandCall, ExtractionResult


class FakeSentMessage:
    message_id = 1


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.caption = None
        self.sent: list[tuple] = []  # (kind, text_or_None)

    async def reply_text(self, text, parse_mode=None, reply_markup=None):
        self.sent.append(("text", text))
        return FakeSentMessage()

    async def reply_photo(self, photo=None):
        self.sent.append(("photo", None))
        return FakeSentMessage()

    async def reply_document(self, document=None):
        self.sent.append(("document", None))
        return FakeSentMessage()


class FakeUser:
    id = 42


class FakeChat:
    id = 100


class FakeUpdate:
    def __init__(self, text=""):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


class FakeContext:
    def __init__(self, conn):
        self.bot_data = {"conn": conn}


def _conn(tmp_path):
    c = db.get_conn(tmp_path / "dispatch.db")
    db.init_db(c)
    return c


def test_write_command_creates_pending_card_without_executing(tmp_path):
    conn = _conn(tmp_path)
    update = FakeUpdate("cria categoria Netflix de 55")
    context = FakeContext(conn)
    result = ExtractionResult(
        intent="command", commands=[CommandCall(command="newcat", args=["Netflix", "55"])], language="pt"
    )

    asyncio.run(text_handler._handle_commands(update, context, result))

    assert db.find_category(conn, "Netflix") is None, "should not execute before confirmation"
    rows = conn.execute("SELECT * FROM pending WHERE kind = 'command'").fetchall()
    assert len(rows) == 1
    assert len(update.message.sent) == 1
    assert update.message.sent[0][0] == "text"
    assert "/newcat" in update.message.sent[0][1]


def test_readonly_command_executes_immediately_no_pending(tmp_path):
    conn = _conn(tmp_path)
    update = FakeUpdate("quais categorias eu tenho?")
    context = FakeContext(conn)
    result = ExtractionResult(
        intent="command", commands=[CommandCall(command="categories", args=[])], language="pt"
    )

    asyncio.run(text_handler._handle_commands(update, context, result))

    assert conn.execute("SELECT COUNT(*) c FROM pending").fetchone()["c"] == 0
    assert len(update.message.sent) == 1
    assert update.message.sent[0][1].startswith("› /categories")


def test_hallucinated_command_falls_back_without_pending(tmp_path):
    conn = _conn(tmp_path)
    update = FakeUpdate("delete all my data")
    context = FakeContext(conn)
    result = ExtractionResult(
        intent="command", commands=[CommandCall(command="destroy_everything", args=[])], language="en"
    )

    asyncio.run(text_handler._handle_commands(update, context, result))

    assert conn.execute("SELECT COUNT(*) c FROM pending").fetchone()["c"] == 0
    assert len(update.message.sent) == 1
    assert "catch" in update.message.sent[0][1].lower()


def test_precheck_failure_blocks_card_and_reports_error(tmp_path):
    conn = _conn(tmp_path)
    update = FakeUpdate("muda o orçamento do Netflix pra 50")
    context = FakeContext(conn)
    result = ExtractionResult(
        intent="command", commands=[CommandCall(command="budget", args=["Netflix", "50"])], language="pt"
    )

    asyncio.run(text_handler._handle_commands(update, context, result))

    assert conn.execute("SELECT COUNT(*) c FROM pending").fetchone()["c"] == 0
    assert len(update.message.sent) == 1
    assert "Netflix" in update.message.sent[0][1]


def test_multi_command_batch_with_one_write_becomes_single_card(tmp_path):
    conn = _conn(tmp_path)
    db.create_category(conn, "Supermarket", "🛒", 60000)
    update = FakeUpdate("qual meu status e muda o dia pro 5")
    context = FakeContext(conn)
    result = ExtractionResult(
        intent="command",
        commands=[
            CommandCall(command="status", args=[]),
            CommandCall(command="cycleday", args=["5"]),
        ],
        language="pt",
    )

    asyncio.run(text_handler._handle_commands(update, context, result))

    # a mixed read+write batch confirms as a single unit -- nothing runs yet
    assert db.get_setting(conn, "cycle_day") is None
    rows = conn.execute("SELECT * FROM pending WHERE kind = 'command'").fetchall()
    assert len(rows) == 1
    assert len(update.message.sent) == 1
