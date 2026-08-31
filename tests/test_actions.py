import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from bot import actions, db


@pytest.fixture
def conn(tmp_path):
    c = db.get_conn(tmp_path / "test.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def ctx(conn):
    return actions.ActionContext(conn=conn, user_id=1, chat_id=1)


def test_newcat_creates_category_with_budget_and_emoji(ctx):
    result = actions.REGISTRY["newcat"].run(ctx, ["Netflix", "55", "🎬"])
    assert result.ok
    cat = db.find_category(ctx.conn, "Netflix")
    assert cat is not None
    assert cat["emoji"] == "🎬"
    assert db.get_budget(ctx.conn, cat["id"]) == 5500


def test_newcat_no_budget_no_emoji(ctx):
    result = actions.REGISTRY["newcat"].run(ctx, ["Restaurants"])
    assert result.ok
    cat = db.find_category(ctx.conn, "Restaurants")
    assert cat["emoji"] == "💰"
    assert db.get_budget(ctx.conn, cat["id"]) is None


def test_newcat_precheck_rejects_duplicate(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Netflix", "55"])
    err = actions.REGISTRY["newcat"].precheck(ctx, ["Netflix", "60"])
    assert err is not None
    assert "Netflix" in err


def test_newcat_precheck_passes_for_new_category(ctx):
    err = actions.REGISTRY["newcat"].precheck(ctx, ["Netflix", "60"])
    assert err is None


def test_budget_precheck_rejects_unknown_category(ctx):
    err = actions.REGISTRY["budget"].precheck(ctx, ["Unknown", "100"])
    assert err is not None


def test_budget_updates_existing_category(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Fuel", "300"])
    result = actions.REGISTRY["budget"].run(ctx, ["Fuel", "350"])
    assert result.ok
    cat = db.find_category(ctx.conn, "Fuel")
    assert db.get_budget(ctx.conn, cat["id"]) == 35000


def test_rename_precheck_rejects_unknown(ctx):
    err = actions.REGISTRY["rename"].precheck(ctx, ["Ghost", "New"])
    assert err is not None


def test_rename_renames_category(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Fuel", "300"])
    result = actions.REGISTRY["rename"].run(ctx, ["Fuel", "Transport"])
    assert result.ok
    assert db.find_category(ctx.conn, "Transport") is not None
    assert db.find_category(ctx.conn, "Fuel") is None


def test_delcat_archives_and_keeps_history(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Fuel", "300"])
    cat = db.find_category(ctx.conn, "Fuel")
    db.add_expense(ctx.conn, cat["id"], 5000, "gas", "2026-08-10", user_id=1)
    result = actions.REGISTRY["delcat"].run(ctx, ["Fuel"])
    assert result.ok
    assert db.find_category(ctx.conn, "Fuel") is None  # archived -> not findable
    rows = ctx.conn.execute("SELECT * FROM expenses").fetchall()
    assert len(rows) == 1  # history preserved


def test_addalias_precheck_rejects_unknown_category(ctx):
    err = actions.REGISTRY["addalias"].precheck(ctx, ["Ghost", "fantasma"])
    assert err is not None


def test_addalias_adds_alias_findable(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Supermarket", "600"])
    actions.REGISTRY["addalias"].run(ctx, ["Supermarket", "mercado", "super"])
    assert db.find_category(ctx.conn, "mercado")["name"] == "Supermarket"
    assert db.find_category(ctx.conn, "super")["name"] == "Supermarket"


def test_cycleday_classification():
    action = actions.REGISTRY["cycleday"]
    assert action.is_write([]) is False
    assert action.is_write(["5"]) is True


def test_cycleday_precheck_rejects_out_of_range():
    action = actions.REGISTRY["cycleday"]
    assert action.precheck(None, ["40"]) is not None
    assert action.precheck(None, ["0"]) is not None
    assert action.precheck(None, ["5"]) is None
    assert action.precheck(None, []) is None


def test_status_with_category_arg(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Supermarket", "600"])
    result = actions.REGISTRY["status"].run(ctx, ["Supermarket"])
    assert result.ok
    assert "Supermarket" in result.text


def test_status_unknown_category(ctx):
    result = actions.REGISTRY["status"].run(ctx, ["Nonexistent"])
    assert not result.ok


def test_status_no_args_gives_overall_summary(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Supermarket", "600"])
    actions.REGISTRY["newcat"].run(ctx, ["Fuel", "300"])
    result = actions.REGISTRY["status"].run(ctx, [])
    assert "Supermarket" in result.text and "Fuel" in result.text


def test_undo_precheck_no_expenses(ctx):
    err = actions.REGISTRY["undo"].precheck(ctx, [])
    assert err == "Nada para desfazer."


def test_undo_removes_last_expense(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Fuel", "300"])
    cat = db.find_category(ctx.conn, "Fuel")
    db.add_expense(ctx.conn, cat["id"], 5000, "gas", "2026-08-10", user_id=1)
    assert actions.REGISTRY["undo"].precheck(ctx, []) is None
    result = actions.REGISTRY["undo"].run(ctx, [])
    assert result.ok
    assert ctx.conn.execute("SELECT COUNT(*) c FROM expenses").fetchone()["c"] == 0


def test_chart_no_categories_returns_ok_message(ctx):
    result = actions.REGISTRY["chart"].run(ctx, [])
    assert result.photo is None
    assert "Nenhuma categoria" in result.text


def test_chart_renders_photo(ctx):
    actions.REGISTRY["newcat"].run(ctx, ["Fuel", "300"])
    result = actions.REGISTRY["chart"].run(ctx, [])
    assert result.photo is not None
    assert len(result.photo) > 1000


def test_export_returns_document(ctx):
    result = actions.REGISTRY["export"].run(ctx, [])
    assert result.document is not None
    data, filename = result.document
    assert filename.endswith(".csv")
    assert b"date,category,amount_brl,description" in data


def test_help_has_markdown_parse_mode(ctx):
    result = actions.REGISTRY["help"].run(ctx, [])
    assert result.parse_mode == "Markdown"


def test_whoami_uses_context_ids(ctx):
    result = actions.REGISTRY["whoami"].run(ctx, [])
    assert "1" in result.text  # ctx.user_id == ctx.chat_id == 1


def test_format_command_line_simple():
    assert actions.format_command_line("newcat", ["Fuel", "300"]) == "/newcat Fuel 300"


def test_format_command_line_quotes_multiword_args():
    line = actions.format_command_line("newcat", ["Health Insurance", "200"])
    assert line == '/newcat "Health Insurance" 200'


def test_command_catalog_lists_every_registered_action():
    catalog = actions.command_catalog()
    for name in actions.REGISTRY:
        assert f"- {name}" in catalog
