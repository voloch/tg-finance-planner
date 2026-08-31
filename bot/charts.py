"""Renders a spent-vs-budget + share-of-spend chart as PNG bytes. Uses the
non-interactive Agg backend -- nothing is ever written to disk."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def render_chart(rows: list[dict], period_label: str) -> bytes:
    """rows: [{name, emoji, spent (cents), budget (cents|None)}, ...]"""
    # matplotlib's default font (DejaVu Sans) can't render most color emoji,
    # so labels use plain category names only -- the emoji stay in Telegram text.
    names = [r["name"] for r in rows]
    spent = [r["spent"] / 100 for r in rows]
    budget = [r["budget"] / 100 if r["budget"] else 0 for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.8))

    y = list(range(len(names)))
    ax1.barh(y, budget, color="#d9d9d9", label="Orçamento")
    ax1.barh(y, spent, color="#4c72b0", label="Gasto")
    ax1.set_yticks(y)
    ax1.set_yticklabels(names)
    ax1.invert_yaxis()
    ax1.legend(loc="lower right")
    ax1.set_xlabel("R$")
    ax1.set_title(f"Gastos — {period_label}")

    nonzero = [(n, s) for n, s in zip(names, spent) if s > 0]
    if nonzero:
        ax2.pie([s for _, s in nonzero], labels=[n for n, _ in nonzero], autopct="%1.0f%%", startangle=90)
    else:
        ax2.text(0.5, 0.5, "Sem gastos ainda", ha="center", va="center")
        ax2.axis("off")
    ax2.set_title("Distribuição")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
