# Scalper Rebracket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Sentinel Pulse-style tight-loop scalper rebracket planning to Auto-Crypto without adding live exchange execution.

**Architecture:** Add a pure `autocrypto.scalper` module that owns price-band rebracketing, suggested signal payload creation, and re-entry cooldown math. Expose a preview-only FastAPI route that operators and future UI controls can call before any signal submission or bracket amendment.

**Tech Stack:** Python 3.11, dataclasses, `Decimal`, FastAPI, pytest, TestClient.

---

### Task 1: Pure Scalper Domain Module

**Files:**
- Create: `src/autocrypto/scalper.py`
- Test: `tests/test_scalper.py`

- [x] **Step 1: Write failing tests**

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from autocrypto.scalper import (
    PriceBand,
    RebracketRuntimeState,
    ScalperBracketConfig,
    plan_rebracket,
    reentry_cooldown_remaining,
    scalper_signal_payload,
)


def test_rebracket_moves_band_up_from_recent_low():
    now = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
    decision = plan_rebracket(
        symbol="BTC/USDT",
        price=Decimal("103"),
        band=PriceBand(lower=Decimal("99"), upper=Decimal("101")),
        config=ScalperBracketConfig(
            threshold=Decimal("2"),
            min_drift=Decimal("0.50"),
            spread=Decimal("0.80"),
            buffer=Decimal("0.10"),
            lookback=4,
        ),
        state=RebracketRuntimeState(recent_prices=(Decimal("102"), Decimal("101.50"), Decimal("102.40"))),
        now=now,
    )

    assert decision.should_rebracket is True
    assert decision.direction == "up"
    assert decision.new_band == PriceBand(lower=Decimal("101.40"), upper=Decimal("102.20"))
    assert decision.previous_band == PriceBand(lower=Decimal("99"), upper=Decimal("101"))
    assert decision.recent_prices == (Decimal("102"), Decimal("101.50"), Decimal("102.40"), Decimal("103"))


def test_rebracket_is_blocked_by_position_and_cooldown():
    now = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
    band = PriceBand(lower=Decimal("99"), upper=Decimal("101"))
    config = ScalperBracketConfig(threshold=Decimal("1"), min_drift=Decimal("0.25"), cooldown_seconds=60)

    with_position = plan_rebracket(
        symbol="BTC/USDT",
        price=Decimal("103"),
        band=band,
        config=config,
        state=RebracketRuntimeState(),
        now=now,
        position_open=True,
    )
    cooling_down = plan_rebracket(
        symbol="BTC/USDT",
        price=Decimal("103"),
        band=band,
        config=config,
        state=RebracketRuntimeState(last_rebracket_at=now - timedelta(seconds=30)),
        now=now,
    )

    assert with_position.should_rebracket is False
    assert with_position.reason == "position_open"
    assert cooling_down.should_rebracket is False
    assert cooling_down.reason == "cooldown_active"


def test_scalper_signal_payload_maps_long_and_short_bands():
    band = PriceBand(lower=Decimal("100"), upper=Decimal("100.80"))

    long_payload = scalper_signal_payload("BTC/USDT", "buy", band, quote_amount=Decimal("250"), stop_distance=Decimal("0.40"))
    short_payload = scalper_signal_payload("BTC/USDT", "sell", band, quote_amount=Decimal("250"), stop_distance=Decimal("0.40"))

    assert long_payload["price"] == "100"
    assert long_payload["take_profit_price"] == "100.80"
    assert long_payload["stop_loss_price"] == "99.60"
    assert short_payload["price"] == "100.80"
    assert short_payload["take_profit_price"] == "100"
    assert short_payload["stop_loss_price"] == "101.20"


def test_reentry_cooldown_remaining_uses_last_exit_timestamp():
    now = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)

    assert reentry_cooldown_remaining(now - timedelta(seconds=20), cooldown_seconds=60, now=now) == 40
    assert reentry_cooldown_remaining(now - timedelta(seconds=90), cooldown_seconds=60, now=now) == 0
    assert reentry_cooldown_remaining(None, cooldown_seconds=60, now=now) == 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scalper.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'autocrypto.scalper'`.

- [x] **Step 3: Implement minimal module**

Create `src/autocrypto/scalper.py` with dataclasses for `PriceBand`, `ScalperBracketConfig`, `RebracketRuntimeState`, `RebracketDecision`, plus `plan_rebracket`, `reentry_cooldown_remaining`, and `scalper_signal_payload`.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scalper.py -q`

Expected: PASS.

### Task 2: Preview API

**Files:**
- Modify: `src/autocrypto/app.py`
- Test: `tests/test_scalper_api.py`

- [x] **Step 1: Write failing API test**

```python
from fastapi.testclient import TestClient

from autocrypto.app import create_app


def test_scalper_rebracket_preview_returns_decision_and_suggested_signal():
    client = TestClient(create_app())

    response = client.post(
        "/scalper/rebracket/preview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "price": "103",
            "lower_price": "99",
            "upper_price": "101",
            "quote_amount": "250",
            "stop_distance": "0.40",
            "recent_prices": ["102", "101.50", "102.40"],
            "config": {
                "threshold": "2",
                "min_drift": "0.50",
                "spread": "0.80",
                "buffer": "0.10",
                "lookback": 4,
            },
            "now": "2026-06-23T15:00:00+00:00",
        },
    )

    body = response.json()

    assert response.status_code == 200
    assert body["decision"]["should_rebracket"] is True
    assert body["decision"]["new_band"] == {"lower": "101.40", "upper": "102.20"}
    assert body["suggested_signal"]["symbol"] == "BTC/USDT"
    assert body["suggested_signal"]["price"] == "101.40"
    assert body["suggested_signal"]["take_profit_price"] == "102.20"
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scalper_api.py -q`

Expected: FAIL with HTTP 404 for `/scalper/rebracket/preview`.

- [x] **Step 3: Add preview route**

Add a route inside `create_app()` that parses the current price band, config, recent marks, optional cooldown timestamp, and returns the pure module's decision plus a suggested signal payload when a new band is available.

- [x] **Step 4: Run tests**

Run: `pytest tests/test_scalper.py tests/test_scalper_api.py -q`

Expected: PASS.
