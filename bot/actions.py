"""The command core layer: every bot command as a plain function over a DB
connection, decoupled from Telegram. A typed slash command and its
natural-language equivalent both funnel through the same `Action` in
REGISTRY, so they can never drift apart -- see bot/handlers/commands.py
(typed dispatch) and bot/handlers/text.py (LLM-driven dispatch).
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from telegram import InputFile

from bot import charts, db, money, periods, reports
from bot.money import format_brl

_NUMERIC_RE = re.compile(r"^-?[\d.,]+$")


def _looks_numeric(s: str) -> bool:
    return bool(_NUMERIC_RE.match(s.strip()))


def format_command_line(name: str, args: list[str]) -> str:
    """Renders a command + args back into a slash-command line, quoting any
    argument that contains whitespace (so multi-word category names --
    which the LLM path allows but Telegram's own arg-splitting doesn't --
    still display and re-parse correctly)."""
    parts = [f"/{name}"]
    for a in args:
        parts.append(f'"{a}"' if " " in a else a)
    return " ".join(parts)


# --------------------------------------------------------------- dataclasses --

@dataclass
class ActionContext:
    conn: sqlite3.Connection
    user_id: int | None = None
    chat_id: int | None = None


@dataclass
class ActionResult:
    text: str
    photo: bytes | None = None
    document: tuple[bytes, str] | None = None
    ok: bool = True
    parse_mode: str | None = None


@dataclass
class Action:
    name: str
    run: Callable[[ActionContext, list[str]], ActionResult]
    is_write: Callable[[list[str]], bool]
    arg_spec: str
    summary: str
    describe: Optional[Callable[[ActionContext, list[str]], str]] = None
    precheck: Optional[Callable[[ActionContext, list[str]], Optional[str]]] = None


REGISTRY: dict[str, Action] = {}


async def send_result(
    reply_text, reply_photo, reply_document, result: ActionResult, prefix: str | None = None
) -> None:
    """Delivers an ActionResult through duck-typed send callables so this one
    function works whether the caller is a Message (reply_text/reply_photo/
    reply_document) or a bound context.bot.send_* set. reply_text takes
    (text, parse_mode); reply_photo/reply_document take the InputFile only."""
    text = result.text
    if prefix:
        text = f"{prefix}\n\n{text}" if text else prefix
    if text:
        await reply_text(text, result.parse_mode)
    if result.photo is not None:
        await reply_photo(InputFile(io.BytesIO(result.photo), filename="chart.png"))
    if result.document is not None:
        data, filename = result.document
        await reply_document(InputFile(io.BytesIO(data), filename=filename))


HELP_TEXT = """🤖 *Finance Planner*

Fale comigo naturalmente para lançar gastos, em português ou inglês:
• "gastei 50 no mercado"
• "spent 120 on gas"
• "45 na farmácia e 30 no restaurante"

Vou confirmar antes de salvar qualquer coisa. Também aceito fotos de recibo.

Também posso criar/editar categorias, orçamentos e o dia de reinício -- é só pedir
("cria uma categoria Netflix de 55", "muda o dia pro 5"). Sempre confirmo antes de
executar qualquer alteração.

*Comandos*
/newcat Nome Orçamento [emoji] — cria categoria (ex: /newcat Supermarket 600 🛒)
/budget Nome Valor — define/atualiza o orçamento de uma categoria
/addalias Nome apelido1 apelido2... — ensina sinônimos para uma categoria
/rename NomeAntigo NomeNovo — renomeia
/delcat Nome — arquiva uma categoria (histórico é mantido)
/categories — lista categorias e orçamentos
/status [Nome] — resumo do período atual, geral ou de uma categoria
/month AAAA-MM — resumo de um mês específico
/list [Nome] — últimos lançamentos
/undo — remove seu último lançamento
/chart — gráfico do período atual
/report — resumo + gráfico do período atual
/export — exporta o período atual em CSV
/cycleday [N] — mostra ou define o dia do mês em que o orçamento reinicia
/whoami — mostra seu ID do Telegram (para configurar ALLOWED_USER_IDS)
"""


# ------------------------------------------------------------------ newcat --

def _parse_newcat(args: list[str]) -> tuple[str, int | None, str | None]:
    tokens = list(args)
    if not tokens:
        raise ValueError("Uso: /newcat Nome Orçamento [emoji]  (ex: /newcat Supermarket 600 🛒)")
    emoji = None
    if tokens and not _looks_numeric(tokens[-1]) and not any(ch.isascii() and ch.isalpha() for ch in tokens[-1]):
        emoji = tokens.pop()
    budget_cents = None
    if tokens and _looks_numeric(tokens[-1]):
        budget_cents = money.parse_amount_brl(tokens.pop())
    name = " ".join(tokens).strip()
    if not name:
        raise ValueError("Uso: /newcat Nome Orçamento [emoji]  (ex: /newcat Supermarket 600 🛒)")
    return name, budget_cents, emoji


def _newcat_precheck(ctx: ActionContext, args: list[str]) -> str | None:
    try:
        name, _, _ = _parse_newcat(args)
    except ValueError as exc:
        return str(exc)
    if db.find_category(ctx.conn, name):
        return f'Já existe uma categoria "{name}".'
    return None


def _newcat_describe(ctx: ActionContext, args: list[str]) -> str:
    try:
        name, budget_cents, emoji = _parse_newcat(args)
    except ValueError as exc:
        return str(exc)
    budget_txt = f" com orçamento de {format_brl(budget_cents)}" if budget_cents else " sem orçamento definido"
    return f'Criar a categoria "{emoji or "💰"} {name}"{budget_txt}.'


def _newcat_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    try:
        name, budget_cents, emoji = _parse_newcat(args)
    except ValueError as exc:
        return ActionResult(str(exc), ok=False)
    if db.find_category(ctx.conn, name):
        return ActionResult(f'Já existe uma categoria "{name}".', ok=False)
    db.create_category(ctx.conn, name, emoji or "💰", budget_cents)
    budget_txt = f" com orçamento de {format_brl(budget_cents)}" if budget_cents else " sem orçamento definido"
    return ActionResult(f'✅ Categoria "{emoji or "💰"} {name}" criada{budget_txt}.')


REGISTRY["newcat"] = Action(
    name="newcat", run=_newcat_run, is_write=lambda a: True,
    arg_spec="<name> <budget> [emoji]",
    summary="Cria uma nova categoria de gastos, com orçamento e emoji opcionais.",
    describe=_newcat_describe, precheck=_newcat_precheck,
)


# ---------------------------------------------------------------- addalias --

def _parse_addalias(args: list[str]) -> tuple[str, list[str]]:
    if len(args) < 2:
        raise ValueError("Uso: /addalias Nome apelido1 apelido2...")
    name, *aliases = args
    return name, aliases


def _addalias_precheck(ctx: ActionContext, args: list[str]) -> str | None:
    try:
        name, _ = _parse_addalias(args)
    except ValueError as exc:
        return str(exc)
    if not db.find_category(ctx.conn, name):
        return f'Não encontrei a categoria "{name}". Veja /categories.'
    return None


def _addalias_describe(ctx: ActionContext, args: list[str]) -> str:
    try:
        name, aliases = _parse_addalias(args)
    except ValueError as exc:
        return str(exc)
    cat = db.find_category(ctx.conn, name)
    cat_name = cat["name"] if cat else name
    return f"Adicionar os apelidos {', '.join(aliases)} à categoria {cat_name}."


def _addalias_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    try:
        name, aliases = _parse_addalias(args)
    except ValueError as exc:
        return ActionResult(str(exc), ok=False)
    cat = db.find_category(ctx.conn, name)
    if not cat:
        return ActionResult(f'Não encontrei a categoria "{name}". Veja /categories.', ok=False)
    for a in aliases:
        db.add_alias(ctx.conn, cat["id"], a)
    return ActionResult(f"✅ Apelidos adicionados a {cat['name']}: {', '.join(aliases)}")


REGISTRY["addalias"] = Action(
    name="addalias", run=_addalias_run, is_write=lambda a: True,
    arg_spec="<name> <alias1> [alias2 ...]",
    summary="Adiciona apelidos/sinônimos a uma categoria existente.",
    describe=_addalias_describe, precheck=_addalias_precheck,
)


# ------------------------------------------------------------------ budget --

def _parse_budget(args: list[str]) -> tuple[str, int]:
    if len(args) < 2:
        raise ValueError("Uso: /budget Nome Valor  (ex: /budget Supermarket 700)")
    *name_parts, amount_str = args
    name = " ".join(name_parts)
    cents = money.parse_amount_brl(amount_str)
    if cents is None:
        raise ValueError("Não entendi o valor.")
    return name, cents


def _budget_precheck(ctx: ActionContext, args: list[str]) -> str | None:
    try:
        name, _ = _parse_budget(args)
    except ValueError as exc:
        return str(exc)
    if not db.find_category(ctx.conn, name):
        return f'Não encontrei a categoria "{name}". Veja /categories.'
    return None


def _budget_describe(ctx: ActionContext, args: list[str]) -> str:
    try:
        name, cents = _parse_budget(args)
    except ValueError as exc:
        return str(exc)
    cat = db.find_category(ctx.conn, name)
    cat_name = cat["name"] if cat else name
    return f"Definir o orçamento de {cat_name} para {format_brl(cents)}."


def _budget_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    try:
        name, cents = _parse_budget(args)
    except ValueError as exc:
        return ActionResult(str(exc), ok=False)
    cat = db.find_category(ctx.conn, name)
    if not cat:
        return ActionResult(f'Não encontrei a categoria "{name}". Veja /categories.', ok=False)
    db.set_budget(ctx.conn, cat["id"], cents)
    return ActionResult(f"✅ Orçamento de {cat['name']} definido para {format_brl(cents)}.")


REGISTRY["budget"] = Action(
    name="budget", run=_budget_run, is_write=lambda a: True,
    arg_spec="<name> <amount>",
    summary="Define ou atualiza o orçamento mensal de uma categoria.",
    describe=_budget_describe, precheck=_budget_precheck,
)


# ------------------------------------------------------------------ rename --

def _parse_rename(args: list[str]) -> tuple[str, str]:
    if len(args) != 2:
        raise ValueError("Uso: /rename NomeAntigo NomeNovo")
    return args[0], args[1]


def _rename_precheck(ctx: ActionContext, args: list[str]) -> str | None:
    try:
        old_name, _ = _parse_rename(args)
    except ValueError as exc:
        return str(exc)
    if not db.find_category(ctx.conn, old_name):
        return f'Não encontrei a categoria "{old_name}". Veja /categories.'
    return None


def _rename_describe(ctx: ActionContext, args: list[str]) -> str:
    try:
        old_name, new_name = _parse_rename(args)
    except ValueError as exc:
        return str(exc)
    return f'Renomear "{old_name}" para "{new_name}".'


def _rename_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    try:
        old_name, new_name = _parse_rename(args)
    except ValueError as exc:
        return ActionResult(str(exc), ok=False)
    cat = db.find_category(ctx.conn, old_name)
    if not cat:
        return ActionResult(f'Não encontrei a categoria "{old_name}". Veja /categories.', ok=False)
    db.rename_category(ctx.conn, cat["id"], new_name)
    return ActionResult(f'✅ "{old_name}" renomeada para "{new_name}".')


REGISTRY["rename"] = Action(
    name="rename", run=_rename_run, is_write=lambda a: True,
    arg_spec="<old_name> <new_name>",
    summary="Renomeia uma categoria existente.",
    describe=_rename_describe, precheck=_rename_precheck,
)


# ------------------------------------------------------------------ delcat --

def _parse_delcat(args: list[str]) -> str:
    if not args:
        raise ValueError("Uso: /delcat Nome")
    return " ".join(args)


def _delcat_precheck(ctx: ActionContext, args: list[str]) -> str | None:
    try:
        name = _parse_delcat(args)
    except ValueError as exc:
        return str(exc)
    if not db.find_category(ctx.conn, name):
        return f'Não encontrei a categoria "{name}". Veja /categories.'
    return None


def _delcat_describe(ctx: ActionContext, args: list[str]) -> str:
    try:
        name = _parse_delcat(args)
    except ValueError as exc:
        return str(exc)
    cat = db.find_category(ctx.conn, name)
    cat_name = cat["name"] if cat else name
    return f"Arquivar a categoria {cat_name} (o histórico é mantido)."


def _delcat_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    try:
        name = _parse_delcat(args)
    except ValueError as exc:
        return ActionResult(str(exc), ok=False)
    cat = db.find_category(ctx.conn, name)
    if not cat:
        return ActionResult(f'Não encontrei a categoria "{name}". Veja /categories.', ok=False)
    db.archive_category(ctx.conn, cat["id"])
    return ActionResult(f'🗄 Categoria "{cat["name"]}" arquivada (histórico mantido).')


REGISTRY["delcat"] = Action(
    name="delcat", run=_delcat_run, is_write=lambda a: True,
    arg_spec="<name>",
    summary="Arquiva uma categoria (o histórico de gastos é mantido).",
    describe=_delcat_describe, precheck=_delcat_precheck,
)


# -------------------------------------------------------------- categories --

def _categories_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    conn = ctx.conn
    rows = db.list_categories(conn)
    if not rows:
        return ActionResult("Nenhuma categoria ainda. Use /newcat Nome Orçamento.")
    lines = []
    for c in rows:
        b = db.get_budget(conn, c["id"])
        aliases = [
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM category_aliases WHERE category_id = ?", (c["id"],)
            ).fetchall()
        ]
        line = f"{c['emoji']} {c['name']} — {format_brl(b) if b else 'sem orçamento'}"
        if aliases:
            line += f" (apelidos: {', '.join(aliases)})"
        lines.append(line)
    return ActionResult("\n".join(lines))


REGISTRY["categories"] = Action(
    name="categories", run=_categories_run, is_write=lambda a: False,
    arg_spec="", summary="Lista todas as categorias com seus orçamentos e apelidos.",
)


# ------------------------------------------------------------------ status --

def _status_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    conn = ctx.conn
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    if args:
        name = " ".join(args)
        cat = db.find_category(conn, name)
        if not cat:
            return ActionResult(f'Não encontrei a categoria "{name}". Veja /categories.', ok=False)
        return ActionResult(reports.build_category_status_text(conn, period, cat))
    return ActionResult(reports.build_status_text(conn, period))


REGISTRY["status"] = Action(
    name="status", run=_status_run, is_write=lambda a: False,
    arg_spec="[name]",
    summary="Mostra o resumo do período atual, geral ou de uma categoria específica "
             "(use para perguntas como 'quanto já gastei em X').",
)


# ------------------------------------------------------------------- month --

def _parse_month(args: list[str]) -> tuple[int, int]:
    if not args:
        raise ValueError("Uso: /month AAAA-MM  (ex: /month 2026-07)")
    try:
        year, mon = (int(x) for x in args[0].split("-"))
    except ValueError:
        raise ValueError("Formato inválido. Use AAAA-MM, ex: /month 2026-07")
    return year, mon


def _month_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    try:
        year, mon = _parse_month(args)
    except ValueError as exc:
        return ActionResult(str(exc), ok=False)
    conn = ctx.conn
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_for_month(year, mon, cycle_day)
    return ActionResult(reports.build_status_text(conn, period))


REGISTRY["month"] = Action(
    name="month", run=_month_run, is_write=lambda a: False,
    arg_spec="<YYYY-MM>", summary="Mostra o resumo de um mês específico.",
)


# -------------------------------------------------------------------- list --

def _list_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    conn = ctx.conn
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    cat_id = None
    if args:
        name = " ".join(args)
        cat = db.find_category(conn, name)
        if not cat:
            return ActionResult(f'Não encontrei a categoria "{name}". Veja /categories.', ok=False)
        cat_id = cat["id"]
    rows = db.list_expenses(
        conn, start=period.start.isoformat(), end=period.end.isoformat(), category_id=cat_id, limit=30
    )
    if not rows:
        return ActionResult("Nenhum lançamento neste período.")
    lines = [f"📅 {period.label}"]
    for r in rows:
        desc = f" — {r['description']}" if r["description"] else ""
        lines.append(
            f"{r['occurred_on']} {r['category_emoji']} {r['category_name']}: {format_brl(r['amount_cents'])}{desc}"
        )
    return ActionResult("\n".join(lines))


REGISTRY["list"] = Action(
    name="list", run=_list_run, is_write=lambda a: False,
    arg_spec="[name]",
    summary="Lista os últimos lançamentos do período atual, opcionalmente filtrando por categoria.",
)


# -------------------------------------------------------------------- undo --

def _undo_precheck(ctx: ActionContext, args: list[str]) -> str | None:
    row = db.get_last_expense(ctx.conn, ctx.user_id)
    return None if row else "Nada para desfazer."


def _undo_describe(ctx: ActionContext, args: list[str]) -> str:
    row = db.get_last_expense(ctx.conn, ctx.user_id)
    if not row:
        return "Nada para desfazer."
    cat = db.get_category(ctx.conn, row["category_id"])
    return f"Remover o último lançamento: {cat['emoji']} {format_brl(row['amount_cents'])} de {cat['name']}."


def _undo_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    row = db.get_last_expense(ctx.conn, ctx.user_id)
    if not row:
        return ActionResult("Nada para desfazer.", ok=False)
    cat = db.get_category(ctx.conn, row["category_id"])
    db.delete_expenses(ctx.conn, [row["id"]])
    return ActionResult(f"🗑 Removido: {cat['emoji']} {format_brl(row['amount_cents'])} de {cat['name']}.")


REGISTRY["undo"] = Action(
    name="undo", run=_undo_run, is_write=lambda a: True,
    arg_spec="", summary="Remove o último lançamento de gasto do usuário.",
    describe=_undo_describe, precheck=_undo_precheck,
)


# ------------------------------------------------------------------- chart --

def _chart_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    conn = ctx.conn
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    rows = reports.category_rows(conn, period)
    if not rows:
        return ActionResult("Nenhuma categoria ainda.")
    png = charts.render_chart(rows, period.label)
    return ActionResult("", photo=png)


REGISTRY["chart"] = Action(
    name="chart", run=_chart_run, is_write=lambda a: False,
    arg_spec="", summary="Envia um gráfico de gastos vs. orçamento do período atual.",
)


# ------------------------------------------------------------------ report --

def _report_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    conn = ctx.conn
    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)
    text = reports.build_status_text(conn, period)
    rows = reports.category_rows(conn, period)
    photo = charts.render_chart(rows, period.label) if rows else None
    return ActionResult(text, photo=photo)


REGISTRY["report"] = Action(
    name="report", run=_report_run, is_write=lambda a: False,
    arg_spec="", summary="Envia o resumo em texto e o gráfico do período atual.",
)


# ------------------------------------------------------------------ export --

def _export_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    conn = ctx.conn
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
    return ActionResult("", document=(data, filename))


REGISTRY["export"] = Action(
    name="export", run=_export_run, is_write=lambda a: False,
    arg_spec="", summary="Exporta os lançamentos do período atual como um arquivo CSV.",
)


# ---------------------------------------------------------------- cycleday --

def _cycleday_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    conn = ctx.conn
    if not args:
        cd = db.get_cycle_day(conn)
        period = periods.period_containing(date.today(), cd)
        return ActionResult(
            f"Dia de reinício atual: {cd}\nPeríodo atual: {period.label}\n\nPara mudar: /cycleday N (1-31)"
        )
    try:
        n = int(args[0])
    except ValueError:
        return ActionResult("Uso: /cycleday N  (N entre 1 e 31)", ok=False)
    if not 1 <= n <= 31:
        return ActionResult("O dia deve estar entre 1 e 31.", ok=False)
    db.set_setting(conn, "cycle_day", str(n))
    period = periods.period_containing(date.today(), n)
    return ActionResult(f"✅ Dia de reinício definido para {n}.\nPeríodo atual: {period.label}")


def _cycleday_precheck(ctx: ActionContext, args: list[str]) -> str | None:
    if not args:
        return None
    try:
        n = int(args[0])
    except ValueError:
        return "Uso: /cycleday N  (N entre 1 e 31)"
    if not 1 <= n <= 31:
        return "O dia deve estar entre 1 e 31."
    return None


def _cycleday_describe(ctx: ActionContext, args: list[str]) -> str:
    if not args:
        return "Mostrar o dia de reinício atual."
    return f"Definir o dia de reinício do orçamento para {args[0]}."


REGISTRY["cycleday"] = Action(
    name="cycleday", run=_cycleday_run, is_write=lambda a: bool(a),
    arg_spec="[day]",
    summary="Sem argumento, mostra o dia de reinício atual (somente leitura). "
             "Com um número de 1 a 31, define um novo dia de reinício.",
    describe=_cycleday_describe, precheck=_cycleday_precheck,
)


# -------------------------------------------------------------------- help --

def _help_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    return ActionResult(HELP_TEXT, parse_mode="Markdown")


REGISTRY["help"] = Action(
    name="help", run=_help_run, is_write=lambda a: False,
    arg_spec="", summary="Mostra a lista de comandos e exemplos de uso.",
)


# ------------------------------------------------------------------ whoami --

def _whoami_run(ctx: ActionContext, args: list[str]) -> ActionResult:
    return ActionResult(f"Seu user ID: `{ctx.user_id}`\nChat ID: `{ctx.chat_id}`", parse_mode="Markdown")


REGISTRY["whoami"] = Action(
    name="whoami", run=_whoami_run, is_write=lambda a: False,
    arg_spec="", summary="Mostra o ID do Telegram do usuário.",
)


# ------------------------------------------------------------------ prompt --

def command_catalog() -> str:
    """Human-readable command list for the LLM system prompt, generated
    straight from REGISTRY so it can never drift from the real command set."""
    lines = []
    for name in sorted(REGISTRY):
        action = REGISTRY[name]
        spec = f" {action.arg_spec}" if action.arg_spec else ""
        lines.append(f"- {name}{spec}: {action.summary}")
    return "\n".join(lines)
