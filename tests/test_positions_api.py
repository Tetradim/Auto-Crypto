from fastapi.testclient import TestClient

from autocrypto.app import create_app


def test_positions_endpoint_returns_paper_portfolio_positions():
    app = create_app()
    client = TestClient(app)

    client.post(
        "/webhooks/tradingview",
        json={
            "symbol": "ETHUSDT",
            "side": "buy",
            "quote_amount": "90",
            "price": "30",
            "stop_loss_pct": "2",
        },
    )

    positions = client.get("/positions").json()["positions"]

    assert positions == [
        {
            "symbol": "ETH/USDT",
            "quantity": "3.00000000",
            "avg_entry": "30.00000000",
            "realized_pnl": "0.00000000",
        }
    ]

