from fastapi.testclient import TestClient

from autocrypto.app import create_app


def _preview(payload):
    client = TestClient(create_app())
    response = client.post("/signals/preview", json=payload)
    assert response.status_code == 200
    return response.json()


def _exit_prices(body):
    return {
        item["kind"]: item["trigger_price"]
        for item in body["bracket_plan"]["exits"]
    }


def test_tight_and_loose_long_short_brackets_preview_with_correct_exit_geometry():
    scenarios = [
        {
            "name": "tight-long",
            "side": "buy",
            "stop_loss_pct": "1",
            "take_profit_pct": "2",
            "trailing_stop_pct": "0.75",
            "exit_side": "sell",
            "stop": "99.00",
            "take_profit": "102.00",
            "trailing": "99.25",
        },
        {
            "name": "loose-long",
            "side": "buy",
            "stop_loss_pct": "8",
            "take_profit_pct": "16",
            "trailing_stop_pct": "4",
            "exit_side": "sell",
            "stop": "92.00",
            "take_profit": "116.00",
            "trailing": "96.00",
        },
        {
            "name": "tight-short",
            "side": "short",
            "stop_loss_pct": "1",
            "take_profit_pct": "2",
            "trailing_stop_pct": "0.75",
            "exit_side": "buy",
            "stop": "101.00",
            "take_profit": "98.00",
            "trailing": "100.75",
        },
        {
            "name": "loose-short",
            "side": "short",
            "stop_loss_pct": "8",
            "take_profit_pct": "16",
            "trailing_stop_pct": "4",
            "exit_side": "buy",
            "stop": "108.00",
            "take_profit": "84.00",
            "trailing": "104.00",
        },
    ]

    for scenario in scenarios:
        body = _preview(
            {
                "signal_id": scenario["name"],
                "symbol": "BTCUSDT",
                "side": scenario["side"],
                "quote_amount": "100",
                "price": "100",
                "stop_loss_pct": scenario["stop_loss_pct"],
                "take_profit_pct": scenario["take_profit_pct"],
                "trailing_stop_pct": scenario["trailing_stop_pct"],
            }
        )

        exits = _exit_prices(body)
        assert body["execution"]["next_status"] == "accepted"
        assert body["risk"]["approved"] is True
        assert body["bracket_plan"]["exit_side"] == scenario["exit_side"]
        assert exits["stop_loss"] == scenario["stop"]
        assert exits["take_profit"] == scenario["take_profit"]
        assert exits["trailing_stop"] == scenario["trailing"]
        assert body["bracket_plan"]["first_target_reward_risk_ratio"] == "2"
