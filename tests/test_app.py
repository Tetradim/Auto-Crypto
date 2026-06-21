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
    assert trailing_exit["next_trailing_trigger"] is None
    assert trailing_exit["trailing_ratchet_ready_at_mark"] == "false"


def test_market_price_preview_reports_next_trailing_ratchet_without_mutating_state():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "ratchet-preview",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
            "trailing_step_pct": "1",
        },
    )

    response = client.post("/market/price/preview", json={"symbol": "BTCUSDT", "price": "110"})

    assert response.status_code == 200
    body = response.json()
    live_trail = next(exit_order for exit_order in body["active_exits"] if exit_order["kind"] == "trailing_stop")
    preview_trail = next(
        exit_order for exit_order in body["preview_active_exits"] if exit_order["kind"] == "trailing_stop"
    )
    assert live_trail["trigger_price"] == "95.00"
    assert live_trail["next_trailing_trigger"] == "104.50"
    assert live_trail["next_trailing_trigger_change"] == "9.50"
    assert live_trail["trailing_step_required"] == "0.95"
    assert live_trail["trailing_ratchet_ready_at_mark"] == "true"
    assert preview_trail["trigger_price"] == "104.50"


def test_bracket_decision_support_sequences_exits_and_trailing_context():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "decision-support",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
            "trailing_activation_pct": "4",
        },
    )

    response = client.get("/brackets/decision-support/decision-support?mark_price=104")

    assert response.status_code == 200
    body = response.json()
    summary = body["summaries"][0]
    assert body["mutates_state"] is False
    assert [row["kind"] for row in summary["trigger_sequence"]] == ["stop_loss", "trailing_stop", "take_profit"]
    trailing = summary["trailing"][0]
    assert trailing["status"] == "pending_activation"
    assert trailing["trailing_activation_ready_at_mark"] == "true"
    assert trailing["paper_only"] is True
    assert summary["health"]["issues"] == ["protective_exit_still_at_risk", "trailing_stop_pending"]


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


def test_signal_preview_marks_trailing_stop_waiting_for_take_profit():
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
            "trailing_stop_pct": "4",
            "trail_after_take_profit": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    trailing_exit = next(exit_order for exit_order in body["bracket_plan"]["exits"] if exit_order["kind"] == "trailing_stop")
    assert body["signal"]["trail_after_take_profit"] is True
    assert body["bracket_plan"]["trailing_starts_armed"] is False
    assert body["bracket_plan"]["trail_after_take_profit"] is True
    assert trailing_exit["status"] == "pending_take_profit"
    assert trailing_exit["trail_after_take_profit"] is True


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


def test_bracket_health_flags_pending_trailing_and_missing_take_profit():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "health-pending-trail",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "trailing_stop_pct": "4",
            "trailing_activation_pct": "2",
        },
    )

    response = client.get("/brackets/health")

    assert response.status_code == 200
    health = response.json()["health"]
    assert health["attention_count"] == 1
    assert health["issue_counts"] == {
        "protective_exit_still_at_risk": 1,
        "trailing_stop_pending": 1,
        "no_open_take_profit_exit": 1,
    }
    assert health["brackets"][0]["status"] == "attention"
    assert health["brackets"][0]["protective_trigger_price"] == "95.00"


def test_lock_profit_moves_protective_exits_beyond_entry_without_live_execution():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "lock-profit-long",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "4",
        },
    )
    response = client.post(
        "/brackets/lock-profit-long/lock-profit",
        json={"lock_profit_pct": "2", "reason": "paper lock after breakout"},
    )
    loosen = client.post("/brackets/lock-profit-long/lock-profit", json={"lock_profit_pct": "1"})

    assert response.status_code == 200
    body = response.json()
    assert body["order"]["mode"] == "paper"
    assert body["order"]["exit_kind"] == "bracket_profit_lock"
    assert body["order"]["reduce_only"] is False
    protective = [exit_order for exit_order in body["active_exits"] if exit_order["kind"] in {"stop_loss", "trailing_stop"}]
    assert [(exit_order["kind"], exit_order["trigger_price"], exit_order["status"]) for exit_order in protective] == [
        ("stop_loss", "102.00", "open"),
        ("trailing_stop", "102.00", "open"),
    ]
    assert loosen.status_code == 409


def test_bracket_exit_ladder_reports_staged_and_partial_trailing_quantities():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "ladder-long",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_targets": [
                {"pct": "5", "close_pct": "50"},
                {"pct": "10", "close_pct": "50"},
            ],
            "trailing_stop_pct": "4",
            "trailing_stop_close_pct": "25",
        },
    )

    response = client.get("/brackets/ladder-long/exit-ladder?mark_price=101")

    assert response.status_code == 200
    body = response.json()
    ladder = body["ladders"][0]
    assert ladder["remaining_notional"] == "100"
    assert ladder["full_close_count"] == 1
    assert ladder["partial_close_count"] == 3
    assert [(row["kind"], row["trigger_price"], row["estimated_exit_quantity"]) for row in ladder["rows"]] == [
        ("stop_loss", "95.00", "1"),
        ("trailing_stop", "96.00", "0.25"),
        ("take_profit", "105.00", "0.5"),
        ("take_profit", "110.00", "0.5"),
    ]
    assert ladder["rows"][0]["intent"] == "protective_exit"
    assert ladder["rows"][0]["estimated_pnl"] == "-5.00"
    assert ladder["rows"][2]["intent"] == "profit_exit"
    assert ladder["rows"][2]["estimated_pnl"] == "2.500"
    assert ladder["rows"][2]["distance_to_trigger"] == "4.00"


def test_short_bracket_exit_ladder_uses_buyback_pnl_direction():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "ladder-short",
            "symbol": "ETHUSDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "max_hold_marks": 2,
        },
    )

    response = client.get("/brackets/ladder-short/exit-ladder?mark_price=98")

    assert response.status_code == 200
    rows = response.json()["ladders"][0]["rows"]
    assert [(row["kind"], row["trigger_price"], row["intent"]) for row in rows] == [
        ("stop_loss", "105.00", "protective_exit"),
        ("take_profit", "90.00", "profit_exit"),
        ("time_exit", "100.00", "staleness_exit"),
    ]
    assert rows[0]["estimated_pnl"] == "-5.00"
    assert rows[1]["estimated_pnl"] == "10.00"
    assert rows[1]["distance_to_trigger"] == "8.00"
    assert rows[2]["marks_remaining"] == 2


def test_bracket_preview_path_replays_trailing_marks_without_mutating_active_bracket():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "preview-path-trail",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "8",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
        },
    )

    response = client.post("/brackets/preview-path-trail/preview-path", json={"prices": ["110", "104.50"]})
    active_after = client.get("/brackets/preview-path-trail").json()["active_exits"]
    live_trailing = next(exit_order for exit_order in active_after if exit_order["kind"] == "trailing_stop")

    assert response.status_code == 200
    body = response.json()
    assert body["mutates_state"] is False
    assert body["marks"][0]["would_trigger"] == []
    preview_trailing = next(
        exit_order
        for exit_order in body["marks"][0]["preview_active_exits"]
        if exit_order["kind"] == "trailing_stop"
    )
    assert preview_trailing["trigger_price"] == "104.50"
    assert body["marks"][1]["would_trigger"] == [
        {"symbol": "BTC/USDT", "kind": "trailing_stop", "price": "104.50000000", "quantity": "1.00000000"}
    ]
    assert body["marks"][1]["preview_active_exits"] == []
    assert body["final_preview_positions"][0]["quantity"] == "0.00000000"
    assert live_trailing["trigger_price"] == "95.00"
    assert client.get("/positions").json()["positions"][0]["quantity"] == "1.00000000"


def test_bracket_preview_path_rejects_empty_mark_list():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "empty-preview-path",
            "symbol": "ETHUSDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    response = client.post("/brackets/empty-preview-path/preview-path", json={"prices": []})

    assert response.status_code == 400
    assert response.json()["detail"] == "prices or marks must be a non-empty list"


def test_bracket_preview_candle_uses_conservative_long_intrabar_order_without_mutating_state():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "long-candle-preview",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    response = client.post(
        "/brackets/long-candle-preview/preview-candle",
        json={"high": "110", "low": "95", "close": "108"},
    )
    active_after = client.get("/brackets/long-candle-preview").json()["active_exits"]

    assert response.status_code == 200
    body = response.json()
    assert body["mutates_state"] is False
    assert body["intrabar_policy"] == "conservative_adverse_first"
    assert body["ambiguous_intrabar"] is True
    assert body["ambiguity"]["protective_touched_count"] == 1
    assert body["ambiguity"]["profit_touched_count"] == 1
    assert body["direction"] == "long"
    assert body["prices"] == ["95", "110", "108"]
    assert body["marks"][0]["phase"] == "adverse"
    assert body["marks"][0]["would_trigger"] == [
        {"symbol": "BTC/USDT", "kind": "stop_loss", "price": "95.00000000", "quantity": "1.00000000"}
    ]
    assert body["marks"][1]["would_trigger"] == []
    assert body["final_preview_positions"][0]["realized_pnl"] == "-5.00000000"
    assert [exit_order["kind"] for exit_order in active_after] == ["stop_loss", "take_profit"]
    assert client.get("/positions").json()["positions"][0]["quantity"] == "1.00000000"


def test_bracket_preview_candle_can_compare_favorable_first_path_with_open_mark():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "long-candle-favorable-preview",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    response = client.post(
        "/brackets/long-candle-favorable-preview/preview-candle",
        json={
            "open": "101",
            "high": "110",
            "low": "95",
            "close": "98",
            "intrabar_policy": "favorable_first",
        },
    )
    active_after = client.get("/brackets/long-candle-favorable-preview").json()["active_exits"]

    assert response.status_code == 200
    body = response.json()
    assert body["intrabar_policy"] == "favorable_first"
    assert body["open"] == "101"
    assert body["prices"] == ["101", "110", "95", "98"]
    assert [mark["phase"] for mark in body["marks"]] == ["open", "favorable", "adverse", "close"]
    assert body["ambiguous_intrabar"] is True
    assert body["marks"][1]["would_trigger"] == [
        {"symbol": "BTC/USDT", "kind": "take_profit", "price": "110.00000000", "quantity": "1.00000000"}
    ]
    assert body["marks"][2]["would_trigger"] == []
    assert body["final_preview_positions"][0]["realized_pnl"] == "10.00000000"
    assert [exit_order["kind"] for exit_order in active_after] == ["stop_loss", "take_profit"]
    assert client.get("/positions").json()["positions"][0]["quantity"] == "1.00000000"


def test_bracket_preview_candle_rejects_open_outside_range_and_unknown_policy():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "invalid-candle-preview",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    invalid_open = client.post(
        "/brackets/invalid-candle-preview/preview-candle",
        json={"open": "111", "high": "110", "low": "95", "close": "100"},
    )
    invalid_policy = client.post(
        "/brackets/invalid-candle-preview/preview-candle",
        json={"high": "110", "low": "95", "close": "100", "intrabar_policy": "random"},
    )

    assert invalid_open.status_code == 400
    assert invalid_open.json()["detail"] == "open must be inside the high/low range"
    assert invalid_policy.status_code == 400
    assert invalid_policy.json()["detail"] == "unsupported intrabar_policy"


def test_bracket_preview_candle_uses_conservative_short_intrabar_order_without_mutating_state():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "short-candle-preview",
            "symbol": "ETHUSDT",
            "side": "short",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    response = client.post(
        "/brackets/short-candle-preview/preview-candle",
        json={"high": "105", "low": "90", "close": "92"},
    )
    active_after = client.get("/brackets/short-candle-preview").json()["active_exits"]

    assert response.status_code == 200
    body = response.json()
    assert body["direction"] == "short"
    assert body["prices"] == ["105", "90", "92"]
    assert body["marks"][0]["would_trigger"] == [
        {"symbol": "ETH/USDT", "kind": "stop_loss", "price": "105.00000000", "quantity": "1.00000000"}
    ]
    assert body["marks"][1]["would_trigger"] == []
    assert body["final_preview_positions"][0]["realized_pnl"] == "-5.00000000"
    assert [exit_order["kind"] for exit_order in active_after] == ["stop_loss", "take_profit"]
    assert client.get("/positions").json()["positions"][0]["quantity"] == "-1.00000000"
