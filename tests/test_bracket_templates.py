from fastapi.testclient import TestClient

from autocrypto.app import create_app
from autocrypto.bracket_templates import apply_bracket_template, list_bracket_templates


def test_bracket_template_catalog_exposes_paper_only_presets():
    app = create_app()
    client = TestClient(app)

    response = client.get("/bracket-templates")

    assert response.status_code == 200
    body = response.json()
    assert body["paper_only"] is True
    assert body["live_submission_enabled"] is False
    assert {template["name"] for template in body["templates"]} >= {
        "fixed_bracket",
        "activation_trailer",
        "staged_runner",
    }
    staged = next(template for template in body["templates"] if template["name"] == "staged_runner")
    assert staged["fields"]["trail_after_take_profit"] is True
    assert staged["fields"]["breakeven_after_take_profit"] is True


def test_apply_bracket_template_keeps_explicit_signal_fields_and_overrides_last():
    payload = apply_bracket_template(
        {
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "100",
            "price": "100",
            "stop_loss_pct": "5",
        },
        "fixed_bracket",
        overrides={"take_profit_pct": "12"},
    )

    assert payload["stop_loss_pct"] == "5"
    assert payload["take_profit_pct"] == "12"
    assert payload["bracket_template"] == "fixed_bracket"


def test_preview_template_signal_returns_normalized_bracket_plan_without_order():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/preview-template",
        json={
            "template": "activation_trailer",
            "signal": {
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
            },
        },
    )
    positions_after = client.get("/positions").json()["positions"]

    assert response.status_code == 200
    body = response.json()
    assert body["template"]["name"] == "activation_trailer"
    assert body["paper_only"] is True
    assert body["signal"]["stop_loss_pct"] == "4"
    assert body["signal"]["trailing_stop_pct"] == "3"
    assert body["bracket_plan"]["trailing_starts_armed"] is False
    assert body["bracket_plan"]["trailing_activation_price"] == "102.00"
    assert body["execution"]["would_place_order"] is True
    assert positions_after == []


def test_submit_template_signal_uses_existing_paper_intake_path():
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/submit-template",
        json={
            "template": "staged_runner",
            "signal": {
                "signal_id": "templated-runner",
                "symbol": "ETHUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["template"]["name"] == "staged_runner"
    assert body["paper_only"] is True
    assert body["order"]["mode"] == "paper"
    assert [exit_order["kind"] for exit_order in body["order"]["exit_orders"]] == [
        "stop_loss",
        "take_profit",
        "take_profit",
        "trailing_stop",
    ]
    assert body["order"]["exit_orders"][-1]["status"] == "pending_take_profit"


def test_unknown_bracket_template_is_rejected():
    assert "fixed_bracket" in {template["name"] for template in list_bracket_templates()}

    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/signals/preview-template",
        json={
            "template": "does-not-exist",
            "signal": {
                "symbol": "BTCUSDT",
                "side": "buy",
                "quote_amount": "100",
                "price": "100",
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown bracket template: does-not-exist"

