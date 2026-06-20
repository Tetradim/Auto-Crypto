from fastapi.testclient import TestClient

from autocrypto.app import create_app


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
    assert positions_after.json()["positions"] == []


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
