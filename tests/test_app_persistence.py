from decimal import Decimal

from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository
from autocrypto.risk import RiskConfig


def test_app_records_signal_order_and_audit_history(tmp_path):
    repo = SQLiteRepository(tmp_path / "app.sqlite3")
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")

    response = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "30",
            "price": "3000",
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
        },
    )

    assert response.status_code == 200
    assert client.get("/signals").json()["signals"][0]["symbol"] == "ETH/USDT"
    order = client.get("/orders").json()["orders"][0]
    assert order["symbol"] == "ETH/USDT"
    assert order["created_at"]
    audit_types = [event["event_type"] for event in client.get("/audit").json()["events"]]
    assert audit_types == ["signal.received", "order.accepted"]


def test_app_rejects_duplicate_signal_after_restart_with_same_repository(tmp_path):
    db_path = tmp_path / "restart.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    payload = {
        "signal_id": "restart-duplicate",
        "symbol": "ETHUSDT",
        "side": "buy",
        "quote_amount": "30",
        "price": "3000",
        "stop_loss_pct": "2",
        "take_profit_pct": "4",
    }

    first = first_client.post("/webhooks/tradingview", json=payload)
    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    second = second_client.post("/webhooks/tradingview", json=payload)

    repo = SQLiteRepository(db_path)
    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len(repo.list_orders()) == 1
    assert [event.event_type for event in repo.list_audit()] == [
        "signal.received",
        "order.accepted",
        "signal.duplicate",
    ]


def test_app_rehydrates_paper_positions_and_brackets_from_order_history(tmp_path):
    db_path = tmp_path / "rehydrate.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "rehydrate-entry",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "2",
            "take_profit_pct": "5",
        },
    )

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")

    assert second_client.get("/positions").json()["positions"][0]["quantity"] == "1.00000000"
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "105"})

    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "take_profit", "price": "105.00000000", "quantity": "1.00000000"}
    ]
    exit_order = SQLiteRepository(db_path).list_orders()[-1]
    assert exit_order["side"] == "sell"
    assert exit_order["reduce_only"] is True
    assert exit_order["exit_orders"][0]["status"] == "filled"


def test_app_rehydrates_open_notional_for_risk_after_restart(tmp_path):
    db_path = tmp_path / "rehydrate_risk.sqlite3"
    risk = RiskConfig(max_order_notional=Decimal("500"), max_open_notional=Decimal("150"))
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path), risk_config=risk))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "first-risk-rehydrate",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "50000",
            "stop_loss_pct": "2",
        },
    )
    second_client = TestClient(create_app(repository=SQLiteRepository(db_path), risk_config=risk))
    second_client.get("/ui")

    response = second_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "second-risk-rehydrate",
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "75",
            "price": "3000",
            "stop_loss_pct": "2",
        },
    )

    assert response.json()["status"] == "rejected"
    assert "max_open_notional_exceeded" in response.json()["risk"]["reason_codes"]


def test_app_lists_and_amends_bracket_stop_with_audit(tmp_path):
    repo = SQLiteRepository(tmp_path / "bracket_amend.sqlite3")
    client = TestClient(create_app(repository=repo))
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "api-stop-amend",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    listed = client.get("/brackets").json()["brackets"]
    amended = client.post(
        "/brackets/api-stop-amend/stop",
        json={"trigger_price": "99", "reason": "trail manual support"},
    )
    loosened = client.post("/brackets/api-stop-amend/stop", json={"trigger_price": "94"})

    assert listed[0]["signal_id"] == "api-stop-amend"
    assert amended.status_code == 200
    assert amended.json()["status"] == "amended"
    assert amended.json()["active_exits"][0]["trigger_price"] == "99.00"
    assert loosened.status_code == 409
    assert repo.list_orders()[-1]["exit_kind"] == "bracket_stop_amend"
    assert repo.list_audit()[-1].event_type == "bracket.stop_amended"


def test_app_replays_bracket_stop_amendment_after_restart(tmp_path):
    db_path = tmp_path / "bracket_amend_replay.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-stop-amend",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )
    first_client.post("/brackets/replay-stop-amend/stop", json={"trigger_price": "99"})

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    bracket = second_client.get("/brackets/replay-stop-amend").json()
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "99"})

    assert bracket["active_exits"][0]["trigger_price"] == "99.00"
    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "stop_loss", "price": "99.00000000", "quantity": "1.00000000"}
    ]


def test_app_replays_trailing_stop_amendment_after_restart(tmp_path):
    db_path = tmp_path / "trailing_amend_replay.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-trail-amend",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
            "trailing_activation_pct": "3",
        },
    )
    amended = first_client.post(
        "/brackets/replay-trail-amend/trailing-stop",
        json={"trigger_price": "99", "reason": "operator tightened trail"},
    )
    loosened = first_client.post("/brackets/replay-trail-amend/trailing-stop", json={"trigger_price": "98"})

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    bracket = second_client.get("/brackets/replay-trail-amend").json()
    trailing_exit = next(exit_order for exit_order in bracket["active_exits"] if exit_order["kind"] == "trailing_stop")
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "99"})
    repo = SQLiteRepository(db_path)

    assert amended.status_code == 200
    assert amended.json()["order"]["exit_kind"] == "bracket_trailing_stop_amend"
    assert loosened.status_code == 409
    assert trailing_exit["trigger_price"] == "99.00"
    assert trailing_exit["status"] == "open"
    assert trailing_exit["trailing_activated"] == "true"
    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "trailing_stop", "price": "99.00000000", "quantity": "1.00000000"}
    ]
    assert repo.list_orders()[-1]["exit_kind"] == "trailing_stop"
    assert any(event.event_type == "bracket.trailing_stop_amended" for event in repo.list_audit())


def test_app_replays_trailing_stop_mark_amendment_after_restart(tmp_path):
    db_path = tmp_path / "trailing_mark_amend_replay.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-trail-mark-amend",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "20",
            "trailing_stop_pct": "4",
            "trailing_activation_pct": "3",
        },
    )
    rejected = first_client.post("/brackets/replay-trail-mark-amend/trailing-stop/mark", json={"mark_price": "102"})
    amended = first_client.post(
        "/brackets/replay-trail-mark-amend/trailing-stop/mark",
        json={"mark_price": "110", "reason": "operator tightened trail from mark"},
    )

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    bracket = second_client.get("/brackets/replay-trail-mark-amend").json()
    trailing_exit = next(exit_order for exit_order in bracket["active_exits"] if exit_order["kind"] == "trailing_stop")
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "105.60"})
    repo = SQLiteRepository(db_path)

    assert rejected.status_code == 409
    assert amended.status_code == 200
    assert amended.json()["order"]["exit_kind"] == "bracket_trailing_stop_mark_amend"
    assert amended.json()["mark_price"] == "110"
    assert trailing_exit["trigger_price"] == "105.60"
    assert trailing_exit["status"] == "open"
    assert trailing_exit["trailing_activated"] == "true"
    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "trailing_stop", "price": "105.60000000", "quantity": "1.00000000"}
    ]
    assert repo.list_orders()[1]["exit_kind"] == "bracket_trailing_stop_mark_amend"
    assert any(event.event_type == "bracket.trailing_stop_mark_amended" for event in repo.list_audit())


def test_app_replays_take_profit_amendment_after_restart(tmp_path):
    db_path = tmp_path / "take_profit_amend_replay.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-tp-amend",
            "symbol": "SOLUSDT",
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
    reduced_reward = first_client.post(
        "/brackets/replay-tp-amend/take-profit",
        json={"trigger_price": "108", "target_index": 1},
    )
    amended = first_client.post(
        "/brackets/replay-tp-amend/take-profit",
        json={"trigger_price": "115", "target_index": 1, "reason": "operator raised target"},
    )

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    bracket = second_client.get("/brackets/replay-tp-amend").json()
    target_exits = [exit_order for exit_order in bracket["active_exits"] if exit_order["kind"] == "take_profit"]
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "115"})
    repo = SQLiteRepository(db_path)

    assert reduced_reward.status_code == 409
    assert amended.status_code == 200
    assert amended.json()["order"]["exit_kind"] == "bracket_take_profit_amend"
    assert amended.json()["order"]["amend_target_index"] == 1
    assert [target["trigger_price"] for target in target_exits] == ["105.00", "115.00"]
    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "take_profit", "price": "115.00000000", "quantity": "0.50000000"},
        {"symbol": "SOL/USDT", "kind": "take_profit", "price": "115.00000000", "quantity": "0.50000000"},
    ]
    assert repo.list_orders()[1]["exit_kind"] == "bracket_take_profit_amend"
    assert repo.list_orders()[1]["amend_target_index"] == 1
    assert any(event.event_type == "bracket.take_profit_amended" for event in repo.list_audit())


def test_app_replays_exact_initial_trailing_stop_price_after_restart(tmp_path):
    db_path = tmp_path / "auto_crypto.sqlite3"
    repo = SQLiteRepository(db_path)
    app = create_app(repository=repo)
    client = TestClient(app)
    client.get("/ui")
    payload = {
        "signal_id": "exact-trail-replay",
        "symbol": "SOL/USDT",
        "side": "buy",
        "quote_amount": "100",
        "price": "100",
        "stop_loss_pct": "8",
        "take_profit_pct": "20",
        "trailing_stop_pct": "5",
        "trailing_stop_price": "98.25",
    }

    accepted = client.post("/webhooks/tradingview", json=payload)
    restarted = TestClient(create_app(repository=SQLiteRepository(db_path)))
    restarted.get("/ui")
    bracket = restarted.get("/brackets/exact-trail-replay").json()
    trailing_exit = next(exit_order for exit_order in bracket["active_exits"] if exit_order["kind"] == "trailing_stop")

    assert accepted.json()["status"] == "accepted"
    assert trailing_exit["trigger_price"] == "98.25"
    assert trailing_exit["initial_trailing_stop_price"] == "98.25"


def test_app_replays_breakeven_amendment_after_restart(tmp_path):
    db_path = tmp_path / "breakeven_amend_replay.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-breakeven",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
    )
    amended = first_client.post("/brackets/replay-breakeven/breakeven", json={"reason": "operator lock"})

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    bracket = second_client.get("/brackets/replay-breakeven").json()
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "100"})
    stop_exit = next(exit_order for exit_order in bracket["active_exits"] if exit_order["kind"] == "stop_loss")
    trailing_exit = next(exit_order for exit_order in bracket["active_exits"] if exit_order["kind"] == "trailing_stop")

    assert amended.status_code == 200
    assert amended.json()["order"]["exit_kind"] == "bracket_breakeven"
    assert stop_exit["trigger_price"] == "100.00"
    assert trailing_exit["trigger_price"] == "100.00"
    assert bracket["summary"]["worst_case_loss"] == "0.00"
    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "stop_loss", "price": "100.00000000", "quantity": "1.00000000"}
    ]


def test_app_replays_profit_lock_amendment_after_restart(tmp_path):
    db_path = tmp_path / "profit_lock_replay.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-profit-lock",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
    )
    amended = first_client.post("/brackets/replay-profit-lock/lock-profit", json={"lock_profit_pct": "2"})

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    bracket = second_client.get("/brackets/replay-profit-lock").json()
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "102"})
    stop_exit = next(exit_order for exit_order in bracket["active_exits"] if exit_order["kind"] == "stop_loss")
    trailing_exit = next(exit_order for exit_order in bracket["active_exits"] if exit_order["kind"] == "trailing_stop")

    assert amended.status_code == 200
    assert amended.json()["order"]["exit_kind"] == "bracket_profit_lock"
    assert stop_exit["trigger_price"] == "102.00"
    assert trailing_exit["trigger_price"] == "102.00"
    assert bracket["summary"]["worst_case_loss"] == "0"
    assert bracket["summary"]["protective_locked_pnl"] == "2.00"
    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "stop_loss", "price": "102.00000000", "quantity": "1.00000000"}
    ]


def test_app_closes_bracket_with_audit_and_replays_after_restart(tmp_path):
    db_path = tmp_path / "bracket_close_replay.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-close-bracket",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
            "trailing_stop_pct": "4",
        },
    )

    closed = first_client.post(
        "/brackets/replay-close-bracket/close",
        json={"price": "106", "reason": "operator flattened risk"},
    )
    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    brackets = second_client.get("/brackets").json()["brackets"]
    positions = second_client.get("/positions").json()["positions"]
    repo = SQLiteRepository(db_path)

    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["order"]["side"] == "sell"
    assert closed.json()["order"]["reduce_only"] is True
    assert closed.json()["order"]["exit_kind"] == "bracket_manual_close"
    assert closed.json()["realized_pnl_delta"] == "6"
    assert brackets == []
    assert positions[0]["quantity"] == "0.00000000"
    assert positions[0]["realized_pnl"] == "6.00000000"
    assert repo.list_orders()[-1]["exit_kind"] == "bracket_manual_close"
    assert repo.list_audit()[-1].event_type == "bracket.closed"


def test_app_closes_bracket_at_protective_exit_with_audit(tmp_path):
    db_path = tmp_path / "bracket_protective_close.sqlite3"
    client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    client.get("/ui")
    client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "protective-api-close",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "10",
            "take_profit_pct": "20",
            "trailing_stop_pct": "5",
        },
    )
    client.post("/market/price", json={"symbol": "SOLUSDT", "price": "110"})

    closed = client.post(
        "/brackets/protective-api-close/close-protective",
        json={"reason": "operator accepts current protective exit"},
    )
    repo = SQLiteRepository(db_path)

    assert closed.status_code == 200
    assert closed.json()["order"]["price"] == "104.50"
    assert closed.json()["order"]["exit_kind"] == "bracket_manual_close"
    assert closed.json()["realized_pnl_delta"] == "4.50"
    assert repo.list_orders()[-1]["exit_kind"] == "bracket_manual_close"
    assert repo.list_audit()[-1].event_type == "bracket.protective_closed"


def test_app_partially_closes_bracket_and_replays_remaining_exits_after_restart(tmp_path):
    db_path = tmp_path / "bracket_partial_close_replay.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    first_client.get("/ui")
    first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "replay-partial-close",
            "symbol": "SOLUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
            "take_profit_pct": "10",
        },
    )

    reduced = first_client.post(
        "/brackets/replay-partial-close/close",
        json={"price": "106", "close_pct": "40", "reason": "operator scaled out"},
    )
    second_client = TestClient(create_app(repository=SQLiteRepository(db_path)))
    second_client.get("/ui")
    bracket = second_client.get("/brackets/replay-partial-close").json()
    triggered = second_client.post("/market/price", json={"symbol": "SOLUSDT", "price": "110"})
    repo = SQLiteRepository(db_path)

    assert reduced.status_code == 200
    assert reduced.json()["order"]["exit_kind"] == "bracket_manual_reduce"
    assert reduced.json()["order"]["notional"] == "42.4"
    assert reduced.json()["order"]["canceled_exit_orders"] == []
    assert reduced.json()["positions"][0]["quantity"] == "0.60000000"
    assert bracket["summary"]["remaining_notional"] == "60.0"
    assert bracket["active_exits"][0]["remaining_quantity"] == "0.6"
    assert triggered.json()["triggered"] == [
        {"symbol": "SOL/USDT", "kind": "take_profit", "price": "110.00000000", "quantity": "0.60000000"}
    ]
    assert repo.list_orders()[1]["exit_kind"] == "bracket_manual_reduce"
    assert repo.list_audit()[2].data["close_pct"] == "40"
