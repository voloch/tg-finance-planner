"""Slash commands. Each one is a thin wrapper: authorize, then dispatch into
the shared command core in bot/actions.py. That core is also what the
natural-language path (bot/handlers/text.py) calls, so a typed command and
its natural-language equivalent can never drift apart.

Note: because Telegram splits /command arguments on whitespace, category
names used with these commands must be a single word (e.g. "Supermarket").
Multi-word category names still work fine through natural-language logging.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot import actions, db, livestatus

HELP_TEXT = actions.HELP_TEXT


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True and replies if the user isn't allowed to use the bot."""
    config = context.bot_data["config"]
    user = update.effective_user
    if not config.is_allowed(user.id):
        await update.message.reply_text(f"🚫 Não autorizado. Seu ID: {user.id}")
        return False
    db.set_home_chat_id(context.bot_data["conn"], update.effective_chat.id)
    return True


async def _send(update: Update, result: actions.ActionResult, prefix: str | None = None) -> None:
    await actions.send_result(
        lambda t, pm: update.message.reply_text(t, parse_mode=pm),
        lambda f: update.message.reply_photo(photo=f),
        lambda f: update.message.reply_document(document=f),
        result,
        prefix=prefix,
    )


async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str, args: list[str]) -> None:
    conn = context.bot_data["conn"]
    ctx = actions.ActionContext(
        conn=conn, user_id=update.effective_user.id, chat_id=update.effective_chat.id
    )
    action = actions.REGISTRY[name]
    result = action.run(ctx, args)
    await _send(update, result)
    if result.ok and action.is_write(args):
        await livestatus.refresh(context.bot, conn, update.effective_chat.id)


# --------------------------------------------------------------- commands --

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.message.reply_text(
        "👋 Oi! Eu sou seu assistente de finanças.\n\n" + actions.HELP_TEXT, parse_mode="Markdown"
    )
    await livestatus.refresh(context.bot, context.bot_data["conn"], update.effective_chat.id)


async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-creates the pinned status message, for when it's been unpinned or
    deleted by hand."""
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    db.set_setting(conn, "status_message_id", "")
    await livestatus.refresh(context.bot, conn, update.effective_chat.id)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "help", [])


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Deliberately not gated by _guard: this is how a new user learns the ID
    # to put in ALLOWED_USER_IDS in the first place.
    conn = context.bot_data["conn"]
    ctx = actions.ActionContext(
        conn=conn, user_id=update.effective_user.id, chat_id=update.effective_chat.id
    )
    result = actions.REGISTRY["whoami"].run(ctx, [])
    await _send(update, result)


async def newcat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "newcat", list(context.args))


async def addalias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "addalias", list(context.args))


async def budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "budget", list(context.args))


async def rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "rename", list(context.args))


async def delcat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "delcat", list(context.args))


async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "categories", [])


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "status", list(context.args))


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "month", list(context.args))


async def list_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "list", list(context.args))


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "undo", [])


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "chart", [])


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "report", [])


async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "export", [])


async def cycleday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await _dispatch(update, context, "cycleday", list(context.args))
