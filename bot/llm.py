"""OpenRouter-backed natural-language expense extraction.

Uses the OpenAI-compatible chat completions API (OpenRouter is a drop-in
proxy) with JSON-object mode, validated against a pydantic schema. Runs
synchronously -- callers should wrap with asyncio.to_thread so the bot's
event loop isn't blocked on the network call.
"""
from __future__ import annotations

import json
from typing import Literal, Optional

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from bot.actions import command_catalog

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# The SDK defaults to a 10-minute timeout and 2 internal retries, which on a
# stalled OpenRouter request means the user waits half an hour for an answer.
# Text extraction is a small prompt against a fast model: if it hasn't come
# back in 30s it isn't coming back. Vision passes its own longer per-request
# timeout (see vision.py).
REQUEST_TIMEOUT_S = 30.0
MAX_RETRIES = 1

# deepseek-v4-flash thinks by default, and on a prompt this small it burns
# ~3000 reasoning tokens deliberating over a one-line extraction: measured
# 101s with thinking vs 2.3s without, for a strictly worse answer ("284-150"
# came back as 284.15 instead of 134). Every task here is schema-filling, so
# turn it off. OpenRouter ignores this for models that don't support it.
NO_REASONING = {"reasoning": {"enabled": False}}


class ExpenseItem(BaseModel):
    category: Optional[str] = None
    amount_brl: float
    description: str = ""
    occurred_on: Optional[str] = None  # YYYY-MM-DD, or None for "today"


class CommandCall(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    intent: Literal["log_expense", "command", "unknown"] = "unknown"
    expenses: list[ExpenseItem] = Field(default_factory=list)
    commands: list[CommandCall] = Field(default_factory=list)
    language: Literal["pt", "en"] = "pt"


def make_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_S,
        max_retries=MAX_RETRIES,
        default_headers={
            "HTTP-Referer": "https://github.com/local/tg-finance-planner",
            "X-Title": "tg-finance-planner",
        },
    )


def _system_prompt(categories: list[dict], today_iso: str) -> str:
    if categories:
        cat_lines = "\n".join(
            f"- {c['name']} (aliases: {', '.join(c['aliases']) if c['aliases'] else 'none'})"
            for c in categories
        )
    else:
        cat_lines = "(no categories exist yet)"
    commands_catalog = command_catalog()
    return f"""You are a bilingual (Portuguese/English) personal-finance parsing assistant for a \
Telegram bot used in Brazil (currency: BRL). Today's date is {today_iso}.

Known expense categories:
{cat_lines}

Available bot commands (for intent="command"):
{commands_catalog}

Read the user's message and return ONLY a JSON object with exactly this shape (no prose, no markdown fences):
{{"intent": "log_expense" | "command" | "unknown",
  "expenses": [{{"category": "<one of the known category names EXACTLY as listed, or null if none match>",
                 "amount_brl": <number>, "description": "<short description>",
                 "occurred_on": "<YYYY-MM-DD or null for today>"}}],
  "commands": [{{"command": "<one of the command names from the catalog above, WITHOUT a leading slash>",
                 "args": ["<arg1>", "<arg2>", ...]}}],
  "language": "pt" | "en"}}

Rules:
- If the message reports one or more purchases/expenses, set intent="log_expense" and fill "expenses" \
(one entry per distinct purchase mentioned). Leave "commands" empty.
- Match "category" against the known list by name OR alias, case-insensitively, including Portuguese \
synonyms (e.g. "mercado"/"supermercado" -> Supermarket, "posto"/"gasolina" -> Fuel). If nothing matches \
reasonably well, set category to null -- never invent a category name that isn't in the list.
- Amounts are in Brazilian reais (BRL). Parse pt-BR number formats (comma as decimal separator) as well \
as plain numbers.
- If the message asks the bot to do something other than logging a purchase -- create/rename/archive a \
category, change a budget, change the reset day, ask about spending or budget status, list past entries, \
undo the last entry, export data, etc. -- set intent="command" and fill "commands" with one entry per \
distinct action requested, using EXACTLY one of the command names from the catalog above (never invent a \
command name that isn't listed -- if nothing in the catalog fits, use intent="unknown" instead). Each \
argument is a separate string in "args", in the order the command expects; never include a leading slash \
in "command"; write monetary amounts as plain numbers ("600", not "R$ 600"); a category argument must \
match a known category name exactly as listed above. Leave "expenses" empty.
- Otherwise (greetings, unrelated chat) set intent="unknown" with empty "expenses" and "commands".
- Detect "language" as the language the user wrote in.
"""


def extract(
    client: OpenAI, model: str, message_text: str, categories: list[dict], today_iso: str
) -> ExtractionResult:
    system = _system_prompt(categories, today_iso)
    last_error: Exception | None = None
    for _ in range(2):  # one retry on a malformed response
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message_text},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            extra_body=NO_REASONING,
        )
        raw = resp.choices[0].message.content or ""
        try:
            return ExtractionResult.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"LLM returned unparseable output: {last_error}")
