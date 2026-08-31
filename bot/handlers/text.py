"""The main natural-language loop: free-text or receipt-photo message in,
LLM extraction, confirmation card out. Nothing is written to the database
until the user taps a button (see callbacks.py)."""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from bot import cards, db, llm, periods, reports, vision
from bot.money import format_brl

logger = logging.getLogger(__name__)

_FALLBACK = {
    "pt": "Não entendi bem 🤔. Tente algo como \"gastei 50 no mercado\". Use /help para ver os comandos.",
    "en": "I didn't quite catch that 🤔. Try something like \"spent 50 on groceries\". Use /help for commands.",
}
_LLM_ERROR = {
    "pt": "⚠️ Não consegui consultar o modelo agora. Tente de novo em instantes.",
    "en": "⚠️ Couldn't reach the model right now. Please try again shortly.",
}
_NO_CATEGORIES = "Você ainda não tem categorias. Crie uma com /newcat Nome 100 (nome, orçamento, emoji opcional)."
_NO_AMOUNT = {
    "pt": "Entendi a categoria mas não consegui ler um valor. Pode reformular?",
    "en": "I got the category but couldn't read an amount. Could you rephrase?",
}


async def _resolve_items(conn, expenses) -> list[dict]:
    items = []
    today_iso = date.today().isoformat()
    for e in expenses:
        amount_cents = round(e.amount_brl * 100) if e.amount_brl else 0
        if amount_cents <= 0:
            continue
        cat_row = db.find_category(conn, e.category) if e.category else None
        occurred_on = e.occurred_on or today_iso
        try:
            date.fromisoformat(occurred_on)
        except ValueError:
            occurred_on = today_iso
        items.append(
            {
                "category_id": cat_row["id"] if cat_row else None,
                "category_name_guess": e.category,
                "amount_cents": amount_cents,
                "description": e.description or "",
                "occurred_on": occurred_on,
            }
        )
    return items


async def _start_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, items: list[dict], source: str, raw_message: str | None) -> None:
    conn = context.bot_data["conn"]
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    token = secrets.token_hex(6)
    payload = {"items": items, "source": source, "raw_message": raw_message}
    db.create_pending(conn, token, "confirm", chat_id, user_id, payload)
    text, kb = cards.render(conn, token, payload)
    sent = await update.message.reply_text(text, reply_markup=kb)
    db.set_pending_message_id(conn, token, sent.message_id)


async def _reply_query(update: Update, context: ContextTypes.DEFAULT_TYPE, result: llm.ExtractionResult) -> None:
    conn = context.bot_data["conn"]
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    cat_row = None
    if result.query and result.query.category:
        cat_row = db.find_category(conn, result.query.category)
    if cat_row:
        text = reports.build_category_status_text(conn, period, cat_row)
    else:
        text = reports.build_status_text(conn, period)
    await update.message.reply_text(text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = context.bot_data["config"]
    conn = context.bot_data["conn"]
    user = update.effective_user
    if not config.is_allowed(user.id):
        await update.message.reply_text(f"🚫 Não autorizado. Seu ID: {user.id}")
        return
    db.set_home_chat_id(conn, update.effective_chat.id)

    text = update.message.text
    if not text:
        return

    categories = db.list_categories_with_aliases(conn)
    if not categories:
        await update.message.reply_text(_NO_CATEGORIES)
        return

    client = context.bot_data["llm_client"]
    model = config.openrouter_model
    today_iso = date.today().isoformat()
    try:
        result = await asyncio.to_thread(llm.extract, client, model, text, categories, today_iso)
    except Exception:
        logger.exception("LLM extraction failed")
        await update.message.reply_text(_LLM_ERROR["pt"])
        return

    if result.intent == "query":
        await _reply_query(update, context, result)
        return

    if result.intent != "log_expense" or not result.expenses:
        await update.message.reply_text(_FALLBACK.get(result.language, _FALLBACK["pt"]))
        return

    items = await _resolve_items(conn, result.expenses)
    if not items:
        await update.message.reply_text(_NO_AMOUNT.get(result.language, _NO_AMOUNT["pt"]))
        return

    await _start_confirmation(update, context, items, source="text", raw_message=text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = context.bot_data["config"]
    conn = context.bot_data["conn"]
    user = update.effective_user
    if not config.is_allowed(user.id):
        await update.message.reply_text(f"🚫 Não autorizado. Seu ID: {user.id}")
        return
    db.set_home_chat_id(conn, update.effective_chat.id)

    categories = db.list_categories_with_aliases(conn)
    if not categories:
        await update.message.reply_text(_NO_CATEGORIES)
        return

    progress = await update.message.reply_text("🔍 Lendo o recibo...")
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    image_bytes = bytes(await tg_file.download_as_bytearray())

    client = context.bot_data["llm_client"]
    model = config.openrouter_vision_model
    today_iso = date.today().isoformat()
    try:
        result = await asyncio.to_thread(
            vision.extract_from_photo, client, model, image_bytes, "image/jpeg", categories, today_iso
        )
    except Exception:
        logger.exception("Vision extraction failed")
        await progress.edit_text("⚠️ Não consegui ler o recibo agora. Tente de novo ou digite o valor manualmente.")
        return

    items = await _resolve_items(conn, result.expenses)
    if not items:
        await progress.edit_text(
            "Não consegui ler o valor do recibo 😕. Pode me dizer o valor e a categoria por texto?"
        )
        return

    await progress.delete()
    caption = update.message.caption
    await _start_confirmation(update, context, items, source="photo", raw_message=caption)
