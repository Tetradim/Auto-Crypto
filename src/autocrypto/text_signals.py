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
        raise SignalValidationError("text signal must match: BUY BTCUSDT $100 @ 50000 SL 2% TP 5% TRAIL 3% STEP 1% ACT 2% BE 2% BEAFTERTP TRAILAFTERTP")

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
    stop_pct = re.search(r"\bSL\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    stop_price = re.search(r"\bSL\s*@\s*(\d+(?:\.\d+)?)", rest, flags=re.IGNORECASE)
    take_profit = re.search(r"\bTP\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    take_profit_price = re.search(r"\bTP\s*@\s*(\d+(?:\.\d+)?)", rest, flags=re.IGNORECASE)
    take_profit_targets = _take_profit_targets_from_text(rest)
    trailing_stop = re.search(r"\b(?:TRAIL|TS)\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    trailing_step = re.search(r"\b(?:STEP|TRAILSTEP|TRAIL-STEP)\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    trailing_activation = re.search(r"\b(?:ACT|TRAILACT|TRAIL-ACT)\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    breakeven = re.search(r"\b(?:BE|BREAKEVEN|BREAK-EVEN)\s*(\d+(?:\.\d+)?)\s*%", rest, flags=re.IGNORECASE)
    breakeven_after_take_profit = re.search(
        r"\b(?:BEAFTERTP|BE-AFTER-TP|BREAKEVEN-AFTER-TP|MOVE-BE-AFTER-TP)\b",
        rest,
        flags=re.IGNORECASE,
    )
    trail_after_take_profit = re.search(
        r"\b(?:TRAILAFTERTP|TRAIL-AFTER-TP|TRAILING-AFTER-TP|TRAIL-AFTER-TAKE-PROFIT)\b",
        rest,
        flags=re.IGNORECASE,
    )
    if stop_pct:
        payload["stop_loss_pct"] = stop_pct.group(1)
    if stop_price:
        payload["stop_loss_price"] = stop_price.group(1)
    if take_profit_targets:
        payload["take_profit_targets"] = take_profit_targets
    elif take_profit:
        payload["take_profit_pct"] = take_profit.group(1)
    elif take_profit_price:
        payload["take_profit_price"] = take_profit_price.group(1)
    if trailing_stop:
        payload["trailing_stop_pct"] = trailing_stop.group(1)
    if trailing_step:
        payload["trailing_step_pct"] = trailing_step.group(1)
    if trailing_activation:
        payload["trailing_activation_pct"] = trailing_activation.group(1)
    if breakeven:
        payload["breakeven_trigger_pct"] = breakeven.group(1)
    if breakeven_after_take_profit:
        payload["breakeven_after_take_profit"] = True
    if trail_after_take_profit:
        payload["trail_after_take_profit"] = True

    return normalize_signal(payload, source=source)


def _take_profit_targets_from_text(rest: str) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for match in re.finditer(
        r"\bTP(?P<label>\d+)\s*(?:(?P<pct>\d+(?:\.\d+)?)\s*%|@\s*(?P<price>\d+(?:\.\d+)?))\s*(?P<close>\d+(?:\.\d+)?)\s*%",
        rest,
        flags=re.IGNORECASE,
    ):
        target: dict[str, str] = {"close_pct": match.group("close")}
        if match.group("pct") is not None:
            target["pct"] = match.group("pct")
        else:
            target["trigger_price"] = match.group("price")
        targets.append(target)
    return targets
