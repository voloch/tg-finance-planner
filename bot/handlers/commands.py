"""Slash commands. Natural language is the primary interface (see text.py);
these cover the genuinely command-shaped actions: category/budget setup,
on-demand reports, and cycle-day configuration.

Note: because Telegram splits /command arguments on whitespace, category
names used with these commands must be a single word (e.g. "Supermarket").
Multi-word category names still work fine through natural-language logging.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date

from telegram import InputFile, Update
from telegram.ext import ContextTypes

from bot import charts, db, money, periods, reports
from bot.money import format_brl

_NUMERIC_RE = re.compile(r"^-?[\d.,]+$")

HELP_TEXT = """🤖 *Finance Planner*

Fale comigo naturalmente para lançar gastos, em português ou inglês:
• "gastei 50 no mercado"
• "spent 120 on gas"
• "45 na farmácia e 30 no restaurante"

Vou confirmar antes de salvar qualquer coisa. Também aceito fotos de recibo.

*Comandos*
/newcat Nome Orçamento [emoji] — cria categoria (ex: /newcat Supermarket 600 🛒)
/budget Nome Valor — define/atualiza o orçamento de uma categoria
/addalias Nome apelido1 apelido2... — ensina sinônimos para uma categoria
/rename NomeAntigo NomeNovo — renomeia
/delcat Nome — arquiva uma categoria (histórico é mantido)
/categories — lista categorias e orçamentos
/status — resumo do período atual
/month AAAA-MM — resumo de um mês específico
/list [Nome] — últimos lançamentos
/undo — remove seu último lançamento
/chart — gráfico do período atual
/report — resumo + gráfico do período atual
/export — exporta o período atual em CSV
/cycleday [N] — mostra ou define o dia do mês em que o orçamento reinicia
/whoami — mostra seu ID do Telegram (para configurar ALLOWED_USER_IDS)
"""


def _looks_numeric(s: str) -> bool:
    return bool(_NUMERIC_RE.match(s.strip()))


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True and replies if the user isn't allowed to use the bot."""
    config = context.bot_data["config"]
    user = update.effective_user
    if not config.is_allowed(user.id):
        await update.message.reply_text(f"🚫 Não autorizado. Seu ID: {user.id}")
        return False
    db.set_home_chat_id(context.bot_data["conn"], update.effective_chat.id)
    return True


def _resolve_category(conn, name: str):
    return db.find_category(conn, name)


async def _reply_category_not_found(update: Update, name: str) -> None:
    await update.message.reply_text(f"Não encontrei a categoria \"{name}\". Veja /categories.")


# --------------------------------------------------------------- commands --

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.message.reply_text(
        "👋 Oi! Eu sou seu assistente de finanças.\n\n" + HELP_TEXT, parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    await update.message.reply_text(f"Seu user ID: `{user.id}`\nChat ID: `{chat.id}`", parse_mode="Markdown")


async def newcat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    tokens = list(context.args)
    if not tokens:
        await update.message.reply_text("Uso: /newcat Nome Orçamento [emoji]  (ex: /newcat Supermarket 600 🛒)")
        return

    emoji = None
    if tokens and not _looks_numeric(tokens[-1]) and not any(ch.isascii() and ch.isalpha() for ch in tokens[-1]):
        emoji = tokens.pop()
    budget_cents = None
    if tokens and _looks_numeric(tokens[-1]):
        budget_cents = money.parse_amount_brl(tokens.pop())
    name = " ".join(tokens).strip()
    if not name:
        await update.message.reply_text("Uso: /newcat Nome Orçamento [emoji]  (ex: /newcat Supermarket 600 🛒)")
        return

    if db.find_category(conn, name):
        await update.message.reply_text(f"Já existe uma categoria \"{name}\".")
        return

    category_id = db.create_category(conn, name, emoji or "💰", budget_cents)
    budget_txt = f" com orçamento de {format_brl(budget_cents)}" if budget_cents else " sem orçamento definido"
    await update.message.reply_text(f"✅ Categoria \"{emoji or '💰'} {name}\" criada{budget_txt}.")


async def addalias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /addalias Nome apelido1 apelido2...")
        return
    name, *aliases = context.args
    cat = _resolve_category(conn, name)
    if not cat:
        await _reply_category_not_found(update, name)
        return
    for a in aliases:
        db.add_alias(conn, cat["id"], a)
    await update.message.reply_text(f"✅ Apelidos adicionados a {cat['name']}: {', '.join(aliases)}")


async def budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /budget Nome Valor  (ex: /budget Supermarket 700)")
        return
    *name_parts, amount_str = context.args
    name = " ".join(name_parts)
    cat = _resolve_category(conn, name)
    if not cat:
        await _reply_category_not_found(update, name)
        return
    cents = money.parse_amount_brl(amount_str)
    if cents is None:
        await update.message.reply_text("Não entendi o valor.")
        return
    db.set_budget(conn, cat["id"], cents)
    await update.message.reply_text(f"✅ Orçamento de {cat['name']} definido para {format_brl(cents)}.")


async def rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    if len(context.args) != 2:
        await update.message.reply_text("Uso: /rename NomeAntigo NomeNovo")
        return
    old_name, new_name = context.args
    cat = _resolve_category(conn, old_name)
    if not cat:
        await _reply_category_not_found(update, old_name)
        return
    db.rename_category(conn, cat["id"], new_name)
    await update.message.reply_text(f"✅ \"{old_name}\" renomeada para \"{new_name}\".")


async def delcat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    if not context.args:
        await update.message.reply_text("Uso: /delcat Nome")
        return
    name = " ".join(context.args)
    cat = _resolve_category(conn, name)
    if not cat:
        await _reply_category_not_found(update, name)
        return
    db.archive_category(conn, cat["id"])
    await update.message.reply_text(f"🗄 Categoria \"{cat['name']}\" arquivada (histórico mantido).")


async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    rows = db.list_categories(conn)
    if not rows:
        await update.message.reply_text("Nenhuma categoria ainda. Use /newcat Nome Orçamento.")
        return
    lines = []
    for c in rows:
        b = db.get_budget(conn, c["id"])
        aliases = [r["alias"] for r in conn.execute(
            "SELECT alias FROM category_aliases WHERE category_id = ?", (c["id"],)
        ).fetchall()]
        line = f"{c['emoji']} {c['name']} — {format_brl(b) if b else 'sem orçamento'}"
        if aliases:
            line += f" (apelidos: {', '.join(aliases)})"
        lines.append(line)
    await update.message.reply_text("\n".join(lines))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    await update.message.reply_text(reports.build_status_text(conn, period))


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    if not context.args:
        await update.message.reply_text("Uso: /month AAAA-MM  (ex: /month 2026-07)")
        return
    try:
        year, mon = (int(x) for x in context.args[0].split("-"))
    except ValueError:
        await update.message.reply_text("Formato inválido. Use AAAA-MM, ex: /month 2026-07")
        return
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_for_month(year, mon, cycle_day)
    await update.message.reply_text(reports.build_status_text(conn, period))


async def list_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    cat_id = None
    if context.args:
        name = " ".join(context.args)
        cat = _resolve_category(conn, name)
        if not cat:
            await _reply_category_not_found(update, name)
            return
        cat_id = cat["id"]
    rows = db.list_expenses(conn, start=period.start.isoformat(), end=period.end.isoformat(), category_id=cat_id, limit=30)
    if not rows:
        await update.message.reply_text("Nenhum lançamento neste período.")
        return
    lines = [f"📅 {period.label}"]
    for r in rows:
        desc = f" — {r['description']}" if r["description"] else ""
        lines.append(f"{r['occurred_on']} {r['category_emoji']} {r['category_name']}: {format_brl(r['amount_cents'])}{desc}")
    await update.message.reply_text("\n".join(lines))


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    user = update.effective_user
    row = db.get_last_expense(conn, user.id)
    if not row:
        await update.message.reply_text("Nada para desfazer.")
        return
    cat = db.get_category(conn, row["category_id"])
    db.delete_expenses(conn, [row["id"]])
    await update.message.reply_text(f"🗑 Removido: {cat['emoji']} {format_brl(row['amount_cents'])} de {cat['name']}.")


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    rows = reports.category_rows(conn, period)
    if not rows:
        await update.message.reply_text("Nenhuma categoria ainda.")
        return
    png = charts.render_chart(rows, period.label)
    await update.message.reply_photo(photo=InputFile(io.BytesIO(png), filename="chart.png"))


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    await update.message.reply_text(reports.build_status_text(conn, period))
    rows = reports.category_rows(conn, period)
    if rows:
        png = charts.render_chart(rows, period.label)
        await update.message.reply_photo(photo=InputFile(io.BytesIO(png), filename="chart.png"))


async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    rows = db.list_expenses(conn, start=period.start.isoformat(), end=period.end.isoformat(), limit=10_000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "category", "amount_brl", "description"])
    for r in rows:
        writer.writerow([r["occurred_on"], r["category_name"], f"{r['amount_cents'] / 100:.2f}", r["description"]])
    data = buf.getvalue().encode("utf-8")
    filename = f"expenses_{period.start.isoformat()}_{period.end.isoformat()}.csv"
    await update.message.reply_document(document=InputFile(io.BytesIO(data), filename=filename))


async def cycleday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    conn = context.bot_data["conn"]
    if not context.args:
        cd = db.get_cycle_day(conn)
        period = periods.period_containing(date.today(), cd)
        await update.message.reply_text(
            f"Dia de reinício atual: {cd}\nPeríodo atual: {period.label}\n\nPara mudar: /cycleday N (1-31)"
        )
        return
    try:
        n = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Uso: /cycleday N  (N entre 1 e 31)")
        return
    if not 1 <= n <= 31:
        await update.message.reply_text("O dia deve estar entre 1 e 31.")
        return
    db.set_setting(conn, "cycle_day", str(n))
    period = periods.period_containing(date.today(), n)
    await update.message.reply_text(f"✅ Dia de reinício definido para {n}.\nPeríodo atual: {period.label}")
