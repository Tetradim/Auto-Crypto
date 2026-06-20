from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.risk import RiskConfig


def test_webhook_test_mode_returns_paper_order_without_live_execution():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "50000",
            "stop_loss_pct": "2",
            "take_profit_pct": "3",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["order"]["mode"] == "paper"
    assert body["order"]["symbol"] == "BTC/USDT"


def test_market_price_response_includes_updated_active_exits():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
        },
    )

    response = client.post("/market/price", json={"symbol": "BTCUSDT", "price": "110"})

    assert response.status_code == 200
    body = response.json()
    trailing_exit = next(exit_order for exit_order in body["active_exits"] if exit_order["kind"] == "trailing_stop")
    stop_exit = next(exit_order for exit_order in body["active_exits"] if exit_order["kind"] == "stop_loss")
    assert body["triggered"] == []
    assert stop_exit["status"] == "open"
    assert stop_exit["oca_group"]
    assert trailing_exit["status"] == "open"
    assert trailing_exit["trigger_price"] == "104.50"
    assert trailing_exit["high_water_mark"] == "110"


def test_signal_preview_includes_synthetic_bracket_plan_for_short_trailing_order():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/preview",
        json={
            "symbol": "ETHUSDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "bracket": {
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
                "trailing_stop_pct": "3",
                "trailing_activation_pct": "2",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["execution"]["next_status"] == "accepted"
    assert body["bracket_plan"]["entry_side"] == "sell"
    assert body["bracket_plan"]["exit_side"] == "buy"
    assert body["bracket_plan"]["trailing_starts_armed"] is False
    assert body["bracket_plan"]["trailing_activation_price"] == "98.00"
    assert [(item["kind"], item["trigger_price"]) for item in body["bracket_plan"]["exits"]] == [
        ("stop_loss", "105.00"),
        ("take_profit", "90.00"),
        ("trailing_stop", "103.00"),
    ]
    assert body["bracket_plan"]["exits"][2]["status"] == "pending_activation"


def test_signal_preview_includes_fixed_amount_trailing_activation_price():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/preview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_amount": "4",
            "trailing_activation_price": "106",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["signal"]["trailing_stop_amount"] == "4"
    assert body["signal"]["trailing_activation_price"] == "106"
    assert body["bracket_plan"]["trailing_starts_armed"] is False
    assert body["bracket_plan"]["trailing_activation_price"] == "106"
    assert body["bracket_plan"]["exits"][2]["trigger_price"] == "96.00"
    assert body["bracket_plan"]["exits"][2]["status"] == "pending_activation"


def test_signal_preview_includes_trailing_step_controls():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/preview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "trailing_stop_pct": "4",
            "trailing_step_pct": "1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    trailing_exit = next(exit_order for exit_order in body["bracket_plan"]["exits"] if exit_order["kind"] == "trailing_stop")
    assert body["signal"]["trailing_step_pct"] == "1"
    assert trailing_exit["trailing_step_pct"] == "1"


def test_signal_preview_includes_time_stop_mark_plan():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/preview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "max_hold_marks": 3,
        },
    )

    assert response.status_code == 200
    body = response.json()
    time_exit = next(exit_order for exit_order in body["bracket_plan"]["exits"] if exit_order["kind"] == "time_exit")
    assert body["signal"]["max_hold_marks"] == 3
    assert body["bracket_plan"]["max_hold_marks"] == 3
    assert time_exit["status"] == "waiting"
    assert time_exit["max_hold_marks"] == 3


def test_market_price_response_reports_time_stop_marks_remaining():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "max_hold_marks": 2,
        },
    )

    response = client.post("/market/price", json={"symbol": "BTCUSDT", "price": "101"})

    assert response.status_code == 200
    body = response.json()
    time_exit = next(exit_order for exit_order in body["active_exits"] if exit_order["kind"] == "time_exit")
    assert body["triggered"] == []
    assert time_exit["max_hold_marks"] == 2
    assert time_exit["marks_seen"] == 1
    assert time_exit["marks_remaining"] == 1


def test_signal_preview_reports_risk_sized_bracket_metrics():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/preview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "risk_pct": "1",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["risk"]["order_notional"] == "2000"
    assert body["bracket_plan"]["estimated_quantity"] == "20"
    assert body["bracket_plan"]["worst_case_loss"] == "100"
    assert body["bracket_plan"]["risk_pct_of_equity"] == "1.00"
    assert body["bracket_plan"]["first_target_reward"] == "200"
    assert body["bracket_plan"]["first_target_reward_risk_ratio"] == "2"
    assert body["bracket_plan"]["total_target_reward"] == "200"
    assert body["bracket_plan"]["total_target_reward_risk_ratio"] == "2"


def test_signal_preview_reports_weighted_total_reward_for_staged_targets():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/preview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "50"},
                {"pct": "10", "close_pct": "50"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bracket_plan"]["worst_case_loss"] == "5.00"
    assert body["bracket_plan"]["first_target_reward"] == "2.50"
    assert body["bracket_plan"]["first_target_reward_risk_ratio"] == "0.5"
    assert body["bracket_plan"]["total_target_reward"] == "7.50"
    assert body["bracket_plan"]["total_target_reward_risk_ratio"] == "1.5"


def test_backtest_signal_replays_price_path_without_mutating_live_engine():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/backtest/signal",
        json={
            "signal": {
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
            },
            "prices": ["104", "110"],
        },
    )
    positions_after = client.get("/positions")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["total_triggers"] == 1
    assert body["marks"][0]["active_exits"]
    assert body["final_daily_pnl"] == "10"
    assert body["final_open_notional"] == "0"
    assert positions_after.json()["positions"] == []


def test_backtest_signal_can_trigger_time_stop_without_mutating_live_engine():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/backtest/signal",
        json={
            "signal": {
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
                "max_hold_marks": 2,
            },
            "prices": ["101", "102"],
        },
    )
    positions_after = client.get("/positions")

    assert response.status_code == 200
    body = response.json()
    assert body["total_triggers"] == 1
    assert body["marks"][0]["active_exits"][-1]["marks_remaining"] == 1
    assert body["marks"][1]["triggered"] == [
        {"symbol": "BTC/USDT", "kind": "time_exit", "price": "102.00000000", "quantity": "1.00000000"}
    ]
    assert body["final_daily_pnl"] == "2"
    assert positions_after.json()["positions"] == []


def test_backtest_signal_can_include_fee_costs_in_paper_pnl():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/backtest/signal",
        json={
            "signal": {
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
            },
            "prices": ["110"],
            "costs": {"fee_bps": "100", "slippage_bps": "0"},
        },
    )
    positions_after = client.get("/positions")

    assert response.status_code == 200
    body = response.json()
    assert body["costs"] == {"fee_bps": "100", "slippage_bps": "0"}
    assert body["marks"][0]["triggered"] == [
        {
            "symbol": "BTC/USDT",
            "kind": "take_profit",
            "price": "110.00000000",
            "quantity": "1.00000000",
            "mark_price": "110.00000000",
            "fee": "1.10000000",
        }
    ]
    assert body["final_daily_pnl"] == "7.90"
    assert body["final_positions"][0]["realized_pnl"] == "7.90000000"
    assert body["final_positions"][0]["fees_paid"] == "2.10000000"
    assert positions_after.json()["positions"] == []


def test_backtest_candles_use_conservative_adverse_first_path_and_report_excursion():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/backtest/signal",
        json={
            "signal": {
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
            },
            "candles": [
                {"label": "bar-1", "high": "112", "low": "94", "close": "108"},
            ],
        },
    )
    positions_after = client.get("/positions")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["total_triggers"] == 1
    assert body["marks"][0]["label"] == "bar-1"
    assert body["marks"][0]["price"] == "108"
    assert body["marks"][0]["triggered"] == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "94.00000000", "quantity": "1.00000000"}
    ]
    assert body["marks"][0]["mfe"] == "12.00"
    assert body["marks"][0]["mae"] == "-6.00"
    assert body["final_daily_pnl"] == "-6"
    assert body["risk_summary"] == {"max_drawdown": "6", "max_runup": "0"}
    assert positions_after.json()["positions"] == []


def test_backtest_stress_runs_named_price_and_cost_scenarios():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/backtest/stress",
        json={
            "signal": {
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
                "stop_loss_pct": "5",
                "take_profit_pct": "10",
            },
            "scenarios": [
                {"name": "calm-target", "prices": ["104", "110"]},
                {"name": "fee-shock", "prices": ["110"], "costs": {"fee_bps": "100", "slippage_bps": "0"}},
            ],
        },
    )
    positions_after = client.get("/positions")

    assert response.status_code == 200
    body = response.json()
    assert body["scenario_count"] == 2
    assert body["accepted_count"] == 2
    assert body["worst_final_daily_pnl"] == "7.90"
    assert body["total_triggers"] == 2
    assert [scenario["name"] for scenario in body["scenarios"]] == ["calm-target", "fee-shock"]
    assert positions_after.json()["positions"] == []


def test_symbol_concentration_cap_uses_current_paper_position():
    app = create_app(risk_config=RiskConfig(max_order_notional=1000, max_symbol_open_notional=150))
    client = TestClient(app)

    first = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
        },
    )
    second = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "60",
            "price": "100",
            "stop_loss_pct": "5",
        },
    )

    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert second.status_code == 200
    assert second.json()["status"] == "rejected"
    assert "max_symbol_open_notional_exceeded" in second.json()["risk"]["reason_codes"]


def test_bracket_summary_reports_protective_distance_and_locked_pnl_after_tighten():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "risk-snapshot",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )
    amend = client.post("/brackets/risk-snapshot/stop", json={"trigger_price": "102"})
    response = client.get("/brackets/risk-snapshot")

    assert amend.status_code == 200
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["protective_trigger_price"] == "102.00"
    assert summary["protective_distance_pct"] == "-2.00"
    assert summary["worst_case_loss"] == "0"
    assert summary["protective_locked_pnl"] == "2.00"


def test_bracket_summary_reports_total_target_reward_for_staged_targets():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "staged-risk-summary",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "50"},
                {"pct": "10", "close_pct": "50"},
            ],
        },
    )
    response = client.get("/brackets/staged-risk-summary")

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["worst_case_loss"] == "5.00"
    assert summary["first_target_reward"] == "2.500"
    assert summary["first_target_reward_risk_ratio"] == "0.5"
    assert summary["total_target_reward"] == "7.500"
    assert summary["total_target_reward_risk_ratio"] == "1.5"


def test_bracket_risk_summary_aggregates_long_short_and_trailing_counts():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "summary-long",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
            "trailing_activation_pct": "2",
        },
    )
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "summary-short",
            "symbol": "ETHUSDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "20",
            "max_hold_marks": 2,
        },
    )

    response = client.get("/brackets/risk-summary")

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["bracket_count"] == 2
    assert summary["exit_count"] == 6
    assert summary["trailing_stop_count"] == 1
    assert summary["pending_trailing_stop_count"] == 1
    assert summary["time_stop_count"] == 1
    assert summary["totals"] == {
        "bracket_count": 2,
        "remaining_notional": "200",
        "worst_case_loss": "15.00",
        "protective_locked_pnl": "-15.00",
        "first_target_reward": "30.00",
        "total_target_reward": "30.00",
    }
    assert [row["symbol"] for row in summary["by_symbol"]] == ["BTC/USDT", "ETH/USDT"]
