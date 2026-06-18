from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.repository import SQLiteRepository


def test_approval_required_mode_queues_signal_until_operator_approves(tmp_path):
    repo = SQLiteRepository(tmp_path / "approvals.sqlite3")
    app = create_app(repository=repo, require_approval=True)
    client = TestClient(app)

    response = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "40",
            "price": "50000",
            "stop_loss_pct": "2",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approval_required"
    assert client.get("/orders").json()["orders"] == []
    pending = client.get("/approvals").json()["pending"][0]
    assert pending["signal_id"] == body["signal_id"]
    assert pending["created_at"]

    approved = client.post(f"/approvals/{body['signal_id']}/approve")

    assert approved.status_code == 200
    assert approved.json()["status"] == "accepted"
    assert client.get("/orders").json()["orders"][0]["symbol"] == "BTC/USDT"
    audit_types = [event["event_type"] for event in client.get("/audit").json()["events"]]
    assert audit_types == ["signal.received", "approval.requested", "order.accepted"]


def test_operator_can_reject_pending_signal(tmp_path):
    repo = SQLiteRepository(tmp_path / "reject.sqlite3")
    app = create_app(repository=repo, require_approval=True)
    client = TestClient(app)
    response = client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "40",
            "price": "3000",
            "stop_loss_pct": "2",
        },
    )
    signal_id = response.json()["signal_id"]

    rejected = client.post(f"/approvals/{signal_id}/reject", json={"reason": "bad setup"})

    assert rejected.status_code == 200
    assert rejected.json() == {"status": "rejected", "signal_id": signal_id}
    assert client.get("/orders").json()["orders"] == []
    assert client.get("/approvals").json()["pending"] == []
    events = client.get("/audit").json()["events"]
    audit_types = [event["event_type"] for event in events]
    assert audit_types == ["signal.received", "approval.requested", "approval.rejected"]
    assert events[-1]["data"] == {"signal_id": signal_id, "reason": "bad setup"}


def test_pending_approval_survives_restart_and_can_be_approved(tmp_path):
    db_path = tmp_path / "approval_restart.sqlite3"
    first_client = TestClient(create_app(repository=SQLiteRepository(db_path), require_approval=True))
    response = first_client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "restart-approval",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "40",
            "price": "50000",
            "stop_loss_pct": "2",
        },
    )
    assert response.json()["status"] == "approval_required"

    second_client = TestClient(create_app(repository=SQLiteRepository(db_path), require_approval=True))

    assert second_client.get("/approvals").json()["pending"][0]["signal_id"] == "restart-approval"
    approved = second_client.post("/approvals/restart-approval/approve")

    assert approved.status_code == 200
    assert approved.json()["status"] == "accepted"
    assert SQLiteRepository(db_path).list_orders()[0]["signal_id"] == "restart-approval"


def test_rejected_pending_approval_is_removed_from_repository(tmp_path):
    db_path = tmp_path / "approval_reject_restart.sqlite3"
    client = TestClient(create_app(repository=SQLiteRepository(db_path), require_approval=True))
    response = client.post(
        "/webhooks/tradingview",
        json={
            "signal_id": "reject-persisted",
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "40",
            "price": "3000",
            "stop_loss_pct": "2",
        },
    )
    signal_id = response.json()["signal_id"]

    rejected = client.post(f"/approvals/{signal_id}/reject", json={"reason": "skip"})

    assert rejected.status_code == 200
    assert SQLiteRepository(db_path).list_pending_approvals() == []


def test_approval_mode_rejects_risk_invalid_signal_without_queueing(tmp_path):
    db_path = tmp_path / "approval_risk_reject.sqlite3"
    client = TestClient(create_app(repository=SQLiteRepository(db_path), require_approval=True))

    response = client.post(
        "/signals/submit",
        json={
            "signal_id": "approval-risk-reject",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "40",
            "price": "50000",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "risk_rejected"
    assert body["risk"]["approved"] is False
    assert body["risk"]["reason_codes"] == ["stop_loss_required"]
    assert client.get("/orders").json()["orders"] == []
    assert client.get("/approvals").json()["pending"] == []
    audit_types = [event["event_type"] for event in client.get("/audit").json()["events"]]
    assert audit_types == ["signal.received", "order.rejected"]


def test_approval_preview_rejects_risk_invalid_signal(tmp_path):
    client = TestClient(
        create_app(repository=SQLiteRepository(tmp_path / "approval_preview_reject.sqlite3"), require_approval=True)
    )

    response = client.post(
        "/signals/preview",
        json={
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "60",
            "price": "3000",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["risk"]["approved"] is False
    assert body["risk"]["reason_codes"] == ["stop_loss_required"]
    assert body["execution"]["next_status"] == "rejected"
    assert body["execution"]["would_place_order"] is False
    assert client.get("/approvals").json()["pending"] == []
