from __future__ import annotations

import re
from typing import Any

from .signals import CryptoSignal, SignalValidationError, normalize_signal


TEXT_SIGNAL_RE = re.compile(
    r"""
    ^\s*
    (?P<side>BUY|SELL|LONG|SHORT)\s+
    (?P<symbol>[A-Z0-9/_-]+)\s+
    (?P<size>\$?\d+(?:\.\d+)?)\s+
    @\s*(?P<price>\d+(?:\.\d+)?)
    (?P<rest>.*)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_text_signal(message: str, *, source: str) -> CryptoSignal:
    match = TEXT_SIGNAL_RE.match(message)
    if not match:
        raise SignalValidationError("text signal must match: BUY BTCUSDT $100 @ 50000 SL 2% TP 5% TRAIL 3%")

    size = match.group("size")
    payload: dict[str, Any] = {
        "symbol": match.group("symbol"),
        "side": match.group("side"),
        "price": match.group("price"),
    }
    if size.startswith("$"):
        payload["quote_amount"] = size.removeprefix("$")
    else:
        payload["base_amount"] = size

    rest = match.group("rest") or ""
    stop = re.search(r"\bSL\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    take_profit = re.search(r"\bTP\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    trailing_stop = re.search(r"\b(?:TRAIL|TS)\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    if stop:
        payload["stop_loss_pct"] = stop.group(1)
    if take_profit:
        payload["take_profit_pct"] = take_profit.group(1)
    if trailing_stop:
        payload["trailing_stop_pct"] = trailing_stop.group(1)

    return normalize_signal(payload, source=source)
