"""Token pricing used for cost accounting.

Prices are USD per one million tokens. They are *nominal* defaults for
budgeting experiments; override them with FAMAS_PRICE_IN_PER_1M /
FAMAS_PRICE_OUT_PER_1M (or edit this table) to match your provider.
"""

from __future__ import annotations

# (input, output) USD per 1M tokens — nominal list-price-style values.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "mock-small": (0.15, 0.60),
    "mock-large": (2.50, 10.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "deepseek-chat": (0.27, 1.10),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

DEFAULT_PRICE = (1.00, 4.00)


def price_for(
    model: str, override_in: float | None = None, override_out: float | None = None
) -> tuple[float, float]:
    base = MODEL_PRICES.get(model)
    if base is None:
        for key, val in MODEL_PRICES.items():
            if model.startswith(key):
                base = val
                break
    base = base or DEFAULT_PRICE
    return (
        override_in if override_in is not None else base[0],
        override_out if override_out is not None else base[1],
    )


def cost_of(
    model: str,
    tokens_in: int,
    tokens_out: int,
    override_in: float | None = None,
    override_out: float | None = None,
) -> float:
    pin, pout = price_for(model, override_in, override_out)
    return (tokens_in * pin + tokens_out * pout) / 1_000_000
