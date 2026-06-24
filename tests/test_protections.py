from datetime import datetime, timedelta, timezone

from autocrypto.protections import ProtectionRule, ProtectionState, evaluate_protections
from autocrypto.signals import normalize_signal


def test_symbol_no_new_entries_allows_reduce_only_but_blocks_fresh_entry():
    state = ProtectionState(
        rules=(
            ProtectionRule(
                rule_id="btc-cooldown",
                mode="no_new_entries",
                scope="symbol",
                target="BTC/USDT",
                reason="cooldown after stop",
            ),
        )
    )
    entry = normalize_signal(
        {
            "symbol": "BTCUSDT",
            "side": "buy",
            "quote_amount": "25",
            "price": "50000",
            "stop_loss_pct": "2",
        },
        source="test",
    )
    exit_signal = normalize_signal(
        {
            "symbol": "BTCUSDT",
            "side": "close_long",
            "quote_amount": "25",
            "price": "50000",
        },
        source="test",
    )

    blocked = evaluate_protections(entry, state)
    allowed = evaluate_protections(exit_signal, state)

    assert blocked.allowed is False
    assert blocked.mode == "no_new_entries"
    assert "protection_no_new_entries" in blocked.reason_codes
    assert allowed.allowed is True


def test_hard_block_overrides_close_only_and_expired_rules_are_ignored():
    now = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
    state = ProtectionState(
        rules=(
            ProtectionRule(
                rule_id="old",
                mode="hard_block",
                scope="global",
                target="*",
                expires_at=now - timedelta(seconds=1),
            ),
            ProtectionRule(rule_id="close-only", mode="close_only", scope="global", target="*"),
            ProtectionRule(rule_id="exchange-down", mode="hard_block", scope="exchange", target="paper"),
        )
    )
    signal = normalize_signal(
        {
            "symbol": "ETHUSDT",
            "side": "close_long",
            "quote_amount": "30",
            "price": "3000",
        },
        source="test",
    )

    decision = evaluate_protections(signal, state, now=now)

    assert decision.allowed is False
    assert decision.mode == "hard_block"
    assert decision.matched_rule_ids == ["close-only", "exchange-down"]
    assert "protection_hard_block" in decision.reason_codes
