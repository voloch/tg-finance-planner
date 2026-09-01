"""Receipt-photo OCR via an OpenRouter vision model. Returns the same
ExtractionResult shape as llm.extract so it can flow into the same
confirmation-card pipeline."""
from __future__ import annotations

import base64
import json

from openai import OpenAI
from pydantic import ValidationError

from bot.llm import ExtractionResult, request_options

# Uploading a base64 photo and OCRing it is slower than plain text extraction,
# so this overrides the shorter client-wide timeout set in llm.make_client.
VISION_TIMEOUT_S = 75.0

_SYSTEM = """You read Brazilian retail receipts (notas fiscais) or informal photos of a purchase and \
extract the total amount. Today's date is {today}.

Known expense categories:
{cats}

Return ONLY a JSON object with exactly this shape (no prose, no markdown fences):
{{"intent": "log_expense", "expenses": [{{"category": "<one of the known category names, or null>", \
"amount_brl": <number>, "description": "<merchant name or short description>", \
"occurred_on": "<YYYY-MM-DD from the receipt if visible, else null>"}}], "query": null, "language": "pt"}}

Rules:
- Use the receipt's TOTAL (total a pagar / valor total), not a subtotal or an individual item price.
- If you cannot confidently read a total amount, set amount_brl to 0.
- Guess "category" from the merchant name using the known list (by name or alias); if nothing matches, null.
- expenses must contain at most one item.
"""


def extract_from_photo(
    client: OpenAI,
    model: str,
    image_bytes: bytes,
    mime_type: str,
    categories: list[dict],
    today_iso: str,
    extra_body: dict | None = None,
) -> ExtractionResult:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    cat_lines = "\n".join(
        f"- {c['name']} (aliases: {', '.join(c['aliases']) if c['aliases'] else 'none'})"
        for c in categories
    ) or "(no categories exist yet)"
    system = _SYSTEM.format(today=today_iso, cats=cat_lines)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract the purchase from this receipt photo."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
        timeout=VISION_TIMEOUT_S,  # overrides the client-wide text timeout
        extra_body=extra_body if extra_body is not None else request_options(),
    )
    raw = resp.choices[0].message.content or ""
    try:
        return ExtractionResult.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"Vision model returned unparseable output: {exc}") from exc
