from decimal import Decimal

from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.exchanges.ccxt_adapter import ExchangeCapabilities
from autocrypto.exchanges.order_planner import plan_bracket_execution
from autocrypto.signals import normalize_signal


def test_order_planner_keeps_paper_bracket_synthetic_and_not_live_safe():
    signal = normalize_signal(
        {
            "signal_id": "paper-plan",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="paper",
        spot=True,
        margin=False,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=False,
        fetch_balance=False,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)

    assert plan.strategy == "paper_synthetic_bracket"
    assert plan.live_order_safe is False
    assert [exit_leg.role for exit_leg in plan.exits] == ["stop_loss", "take_profit", "trailing_stop"]
    assert [exit_leg.side for exit_leg in plan.exits] == ["sell", "sell", "sell"]


def test_order_planner_uses_attached_strategy_when_venue_advertises_brackets_and_trailing():
    signal = normalize_signal(
        {
            "signal_id": "attached-plan",
            "symbol": "ETH/USDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_price": "105",
            "take_profit_price": "90",
            "trailing_stop_amount": "3",
            "exchange": "okx",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="okx",
        spot=True,
        margin=True,
        swap=True,
        future=True,
        option=False,
        create_order=True,
        cancel_order=True,
        fetch_balance=True,
        attached_stop_loss_take_profit=True,
        oco_order=True,
        trailing_order=True,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)

    assert plan.strategy == "attached_bracket_with_trailing"
    assert plan.exits[0].side == "buy"
    assert plan.exits[0].reduce_only is True
    assert plan.exits[0].params["reduceOnly"] is True


def test_order_planner_requires_paper_when_native_trailing_is_not_advertised():
    signal = normalize_signal(
        {
            "signal_id": "fallback-plan",
            "symbol": "SOL/USDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "3",
            "exchange": "kraken",
        },
        source="test",
    )
    capabilities = ExchangeCapabilities(
        exchange_id="kraken",
        spot=True,
        margin=True,
        swap=False,
        future=False,
        option=False,
        create_order=True,
        cancel_order=True,
        fetch_balance=True,
        attached_stop_loss_take_profit=False,
        oco_order=False,
        trailing_order=False,
        reduce_only=True,
    )

    plan = plan_bracket_execution(signal, capabilities)

    assert plan.strategy == "paper_required_for_mixed_bracket_trailing"
    assert "trailing_order_not_advertised" in plan.warnings
    assert "native_bracket_not_advertised" in plan.warnings


def test_signal_exchange_plan_endpoint_returns_non_executing_paper_plan():
    client = TestClient(create_app())

    response = client.post(
        "/signals/exchange-plan",
        json={
            "signal_id": "endpoint-plan",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50000",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
            "trailing_stop_pct": "3",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capabilities"]["exchange_id"] == "paper"
    assert body["plan"]["strategy"] == "paper_synthetic_bracket"
    assert body["plan"]["live_order_safe"] is False
    assert body["plan"]["exits"][0]["trigger_price"] == str(Decimal("49000.00"))
