"""Entry point: `python -m bot` (see run.sh for the full bootstrap)."""
from __future__ import annotations

import logging
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from bot import db, llm
from bot.config import load_config
from bot.handlers import callbacks, commands, jobs, text

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


COMMANDS = [
    BotCommand("start", "Início"),
    BotCommand("help", "Ajuda e exemplos"),
    BotCommand("status", "Resumo do período atual"),
    BotCommand("categories", "Listar categorias"),
    BotCommand("newcat", "Criar categoria"),
    BotCommand("budget", "Definir orçamento"),
    BotCommand("addalias", "Adicionar apelido a categoria"),
    BotCommand("rename", "Renomear categoria"),
    BotCommand("delcat", "Arquivar categoria"),
    BotCommand("month", "Resumo de um mês específico"),
    BotCommand("list", "Últimos lançamentos"),
    BotCommand("undo", "Desfazer último lançamento"),
    BotCommand("chart", "Gráfico do período"),
    BotCommand("report", "Relatório completo"),
    BotCommand("export", "Exportar CSV"),
    BotCommand("cycleday", "Configurar dia de reinício"),
    BotCommand("whoami", "Meu ID do Telegram"),
]


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(COMMANDS)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception while processing update: %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Ocorreu um erro inesperado. Tente novamente.")
        except Exception:
            pass


def main() -> None:
    config = load_config()
    conn = db.get_conn(config.db_path)
    db.init_db(conn)

    llm_client = llm.make_client(config.openrouter_token)

    application = Application.builder().token(config.telegram_token).post_init(_post_init).build()
    application.bot_data["config"] = config
    application.bot_data["conn"] = conn
    application.bot_data["llm_client"] = llm_client

    application.add_handler(CommandHandler("start", commands.start))
    application.add_handler(CommandHandler("help", commands.help_cmd))
    application.add_handler(CommandHandler("whoami", commands.whoami))
    application.add_handler(CommandHandler("newcat", commands.newcat))
    application.add_handler(CommandHandler("addalias", commands.addalias))
    application.add_handler(CommandHandler("budget", commands.budget))
    application.add_handler(CommandHandler("rename", commands.rename))
    application.add_handler(CommandHandler("delcat", commands.delcat))
    application.add_handler(CommandHandler("categories", commands.categories))
    application.add_handler(CommandHandler("status", commands.status))
    application.add_handler(CommandHandler("month", commands.month))
    application.add_handler(CommandHandler("list", commands.list_expenses))
    application.add_handler(CommandHandler("undo", commands.undo))
    application.add_handler(CommandHandler("chart", commands.chart))
    application.add_handler(CommandHandler("report", commands.report))
    application.add_handler(CommandHandler("export", commands.export_csv))
    application.add_handler(CommandHandler("cycleday", commands.cycleday))

    application.add_handler(CallbackQueryHandler(callbacks.handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO, text.handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text.handle_text))

    application.add_error_handler(on_error)

    if application.job_queue is not None:
        application.job_queue.run_daily(
            jobs.daily_check, time=dt_time(hour=9, minute=0, tzinfo=ZoneInfo(config.tz_name))
        )
        application.job_queue.run_repeating(jobs.cleanup_job, interval=6 * 3600, first=60)
    else:
        logger.warning(
            "JobQueue unavailable -- install python-telegram-bot[job-queue]. "
            "The monthly report and pending-card cleanup will not run."
        )

    logger.info(
        "Starting bot (model=%s, vision=%s, db=%s, tz=%s)",
        config.openrouter_model, config.openrouter_vision_model, config.db_path, config.tz_name,
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
