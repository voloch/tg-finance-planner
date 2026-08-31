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

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ExpenseItem(BaseModel):
    category: Optional[str] = None
    amount_brl: float
    description: str = ""
    occurred_on: Optional[str] = None  # YYYY-MM-DD, or None for "today"


class QueryInfo(BaseModel):
    category: Optional[str] = None
    period: str = "current"


class ExtractionResult(BaseModel):
    intent: Literal["log_expense", "query", "unknown"] = "unknown"
    expenses: list[ExpenseItem] = Field(default_factory=list)
    query: Optional[QueryInfo] = None
    language: Literal["pt", "en"] = "pt"


def make_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
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
    return f"""You are a bilingual (Portuguese/English) personal-finance parsing assistant for a \
Telegram bot used in Brazil (currency: BRL). Today's date is {today_iso}.

Known expense categories:
{cat_lines}

Read the user's message and return ONLY a JSON object with exactly this shape (no prose, no markdown fences):
{{"intent": "log_expense" | "query" | "unknown",
  "expenses": [{{"category": "<one of the known category names EXACTLY as listed, or null if none match>",
                 "amount_brl": <number>, "description": "<short description>",
                 "occurred_on": "<YYYY-MM-DD or null for today>"}}],
  "query": {{"category": "<name or null>", "period": "current"}} | null,
  "language": "pt" | "en"}}

Rules:
- If the message reports one or more purchases/expenses, set intent="log_expense" and fill "expenses" \
(one entry per distinct purchase mentioned).
- Match "category" against the known list by name OR alias, case-insensitively, including Portuguese \
synonyms (e.g. "mercado"/"supermercado" -> Supermarket, "posto"/"gasolina" -> Fuel). If nothing matches \
reasonably well, set category to null -- never invent a category name that isn't in the list.
- Amounts are in Brazilian reais (BRL). Parse pt-BR number formats (comma as decimal separator) as well \
as plain numbers.
- If the message is a question about spending or budget status (not a new purchase), set intent="query".
- Otherwise (greetings, unrelated chat) set intent="unknown" with an empty "expenses" list.
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
        )
        raw = resp.choices[0].message.content or ""
        try:
            return ExtractionResult.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"LLM returned unparseable output: {last_error}")
