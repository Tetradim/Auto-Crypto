from autocrypto.repository import SQLiteRepository


def test_sqlite_repository_persists_runtime_state(tmp_path):
    db_path = tmp_path / "runtime.sqlite3"
    repo = SQLiteRepository(db_path)

    repo.set_runtime_state(
        "scalper:BTC/USDT",
        {"band": {"lower": "100", "upper": "101"}, "recent_prices": ["100.5"]},
    )

    reopened = SQLiteRepository(db_path)

    assert reopened.get_runtime_state("scalper:BTC/USDT") == {
        "band": {"lower": "100", "upper": "101"},
        "recent_prices": ["100.5"],
    }
    assert reopened.list_runtime_state("scalper:") == {
        "scalper:BTC/USDT": {"band": {"lower": "100", "upper": "101"}, "recent_prices": ["100.5"]}
    }

    reopened.delete_runtime_state("scalper:BTC/USDT")

    assert SQLiteRepository(db_path).get_runtime_state("scalper:BTC/USDT") is None
