"""Daily job: on the configured cycle day, post a closing-period report to
whichever chat the bot has last talked in (there's only one user)."""
from __future__ import annotations

import calendar
import io
import logging
from datetime import date, timedelta

from telegram import InputFile
from telegram.ext import ContextTypes

from bot import charts, db, livestatus, periods, reports

logger = logging.getLogger(__name__)


def _is_cycle_day(today: date, cycle_day: int) -> bool:
    if today.day == cycle_day:
        return True
    # cycle_day was clamped this month (e.g. 31 in a 30-day month) -- treat
    # the last day of the month as the trigger in that case.
    last_day = calendar.monthrange(today.year, today.month)[1]
    return cycle_day > last_day and today.day == last_day


async def daily_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    cycle_day = db.get_cycle_day(conn)
    today = date.today()

    if not _is_cycle_day(today, cycle_day):
        return
    if db.get_setting(conn, "last_report_date") == today.isoformat():
        return  # already posted today (e.g. bot restarted)

    home_chat_id = db.get_home_chat_id(conn)
    if not home_chat_id:
        return

    period = periods.period_containing(today - timedelta(days=1), cycle_day)
    rows = reports.category_rows(conn, period)
    if not rows:
        db.set_setting(conn, "last_report_date", today.isoformat())
        return

    try:
        text = "📊 Fechamento do período!\n\n" + reports.build_status_text(conn, period)
        await context.bot.send_message(chat_id=home_chat_id, text=text)
        png = charts.render_chart(rows, period.label)
        await context.bot.send_photo(chat_id=home_chat_id, photo=InputFile(io.BytesIO(png), filename="chart.png"))
    except Exception:
        logger.exception("Failed to send monthly report")
        return

    db.set_setting(conn, "last_report_date", today.isoformat())
    await livestatus.refresh(context.bot, conn, home_chat_id)


async def cleanup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    db.cleanup_old_pending(conn)
    # Nothing may have been written for days, but the period label and the
    # "atualizado" stamp still go stale, so refresh the pin on this tick too.
    await livestatus.refresh(context.bot, conn)
