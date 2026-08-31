"""Text rendering: progress bars, status summaries, alert-threshold crossing."""
from __future__ import annotations

from bot import db, periods
from bot.money import format_brl

ALERT_THRESHOLDS = [(1.0, "🔴 estourou o orçamento de"), (0.8, "🟡 atingiu 80% do orçamento de")]


def progress_bar(spent_cents: int, budget_cents: int | None, width: int = 10) -> str:
    if not budget_cents:
        return ""
    ratio = spent_cents / budget_cents
    filled = min(width, round(min(ratio, 1.0) * width))
    bar = "▓" * filled + "░" * (width - filled)
    pct = round(ratio * 100)
    return f"{bar} {pct}%"


def alert_lines(spent_before: int, spent_after: int, budget_cents: int | None, category_name: str) -> list[str]:
    if not budget_cents:
        return []
    lines = []
    for threshold, label in ALERT_THRESHOLDS:
        t = budget_cents * threshold
        if spent_before < t <= spent_after:
            lines.append(f"{label} {category_name}!")
    return lines


def category_rows(conn, period: periods.Period) -> list[dict]:
    rows = []
    for c in db.list_categories(conn):
        spent = db.sum_spent(conn, c["id"], period.start.isoformat(), period.end.isoformat())
        budget = db.get_budget(conn, c["id"])
        rows.append({"id": c["id"], "name": c["name"], "emoji": c["emoji"], "spent": spent, "budget": budget})
    return rows


def build_status_text(conn, period: periods.Period) -> str:
    rows = category_rows(conn, period)
    if not rows:
        return "Você ainda não tem categorias. Use /newcat Nome 100 para criar uma."

    lines = [f"📅 Período: {period.label}\n"]
    total_spent = 0
    total_budget = 0
    for r in rows:
        total_spent += r["spent"]
        bar = progress_bar(r["spent"], r["budget"])
        budget_txt = format_brl(r["budget"]) if r["budget"] else "sem orçamento"
        line = f"{r['emoji']} {r['name']}: {format_brl(r['spent'])} / {budget_txt}"
        if bar:
            line += f"\n   {bar}"
        lines.append(line)
        if r["budget"]:
            total_budget += r["budget"]

    lines.append("")
    lines.append(f"Total gasto: {format_brl(total_spent)}" + (f" de {format_brl(total_budget)}" if total_budget else ""))
    return "\n".join(lines)


def build_category_status_text(conn, period: periods.Period, category_row) -> str:
    spent = db.sum_spent(conn, category_row["id"], period.start.isoformat(), period.end.isoformat())
    budget = db.get_budget(conn, category_row["id"])
    budget_txt = format_brl(budget) if budget else "sem orçamento"
    bar = progress_bar(spent, budget)
    text = f"📅 {period.label}\n{category_row['emoji']} {category_row['name']}: {format_brl(spent)} / {budget_txt}"
    if bar:
        text += f"\n{bar}"
    return text
