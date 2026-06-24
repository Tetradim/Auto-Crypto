from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .signals import CryptoSignal, SignalValidationError, normalize_symbol


PROTECTION_MODES = {"no_new_entries", "close_only", "hard_block"}
PROTECTION_SCOPES = {"global", "exchange", "symbol", "strategy"}
MODE_PRIORITY = {"none": 0, "no_new_entries": 1, "close_only": 2, "hard_block": 3}


@dataclass(frozen=True)
class ProtectionRule:
    rule_id: str
    mode: str
    scope: str = "global"
    target: str = "*"
    reason: str = ""
    expires_at: datetime | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        rule_id = self.rule_id.strip()
        mode = self.mode.strip().lower()
        scope = self.scope.strip().lower()
        if not rule_id:
            raise ValueError("rule_id is required")
        if mode not in PROTECTION_MODES:
            raise ValueError(f"unsupported protection mode: {self.mode}")
        if scope not in PROTECTION_SCOPES:
            raise ValueError(f"unsupported protection scope: {self.scope}")
        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "target", _normalize_target(scope, self.target))
        object.__setattr__(self, "expires_at", _aware_utc(self.expires_at) if self.expires_at else None)
        object.__setattr__(self, "created_at", _aware_utc(self.created_at) if self.created_at else None)

    def matches(self, signal: CryptoSignal) -> bool:
        if self.scope == "global":
            return True
        if self.scope == "exchange":
            return self.target == signal.exchange.strip().lower()
        if self.scope == "symbol":
            return self.target == signal.symbol
        if self.scope == "strategy":
            return self.target == signal.strategy_id.strip().lower()
        return False

    def active(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return True
        return _aware_utc(now or datetime.now(timezone.utc)) < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "mode": self.mode,
            "scope": self.scope,
            "target": self.target,
            "reason": self.reason,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class ProtectionState:
    rules: tuple[ProtectionRule, ...] = field(default_factory=tuple)

    def active_rules(self, now: datetime | None = None) -> tuple[ProtectionRule, ...]:
        return tuple(rule for rule in self.rules if rule.active(now))

    def with_rule(self, rule: ProtectionRule) -> ProtectionState:
        remaining = tuple(existing for existing in self.rules if existing.rule_id != rule.rule_id)
        return ProtectionState(rules=(*remaining, rule))

    def without_rule(self, rule_id: str) -> ProtectionState:
        return ProtectionState(rules=tuple(rule for rule in self.rules if rule.rule_id != rule_id))

    def to_dict(self) -> dict[str, Any]:
        return {"rules": [rule.to_dict() for rule in self.rules]}


@dataclass(frozen=True)
class ProtectionDecision:
    allowed: bool
    mode: str
    reason_codes: list[str] = field(default_factory=list)
    matched_rules: tuple[ProtectionRule, ...] = field(default_factory=tuple)

    @property
    def matched_rule_ids(self) -> list[str]:
        return [rule.rule_id for rule in self.matched_rules]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "reason_codes": self.reason_codes,
            "matched_rule_ids": self.matched_rule_ids,
            "matched_rules": [rule.to_dict() for rule in self.matched_rules],
        }


def evaluate_protections(
    signal: CryptoSignal,
    state: ProtectionState | None = None,
    *,
    now: datetime | None = None,
) -> ProtectionDecision:
    state = state or ProtectionState()
    matched = tuple(rule for rule in state.active_rules(now) if rule.matches(signal))
    if not matched:
        return ProtectionDecision(allowed=True, mode="none")

    mode = max((rule.mode for rule in matched), key=lambda candidate: MODE_PRIORITY[candidate])
    opens_position = not signal.reduce_only
    if mode == "hard_block":
        return ProtectionDecision(
            allowed=False,
            mode=mode,
            reason_codes=["protection_hard_block", *_rule_reason_codes(matched)],
            matched_rules=matched,
        )
    if mode == "close_only" and opens_position:
        return ProtectionDecision(
            allowed=False,
            mode=mode,
            reason_codes=["protection_close_only", *_rule_reason_codes(matched)],
            matched_rules=matched,
        )
    if mode == "no_new_entries" and opens_position:
        return ProtectionDecision(
            allowed=False,
            mode=mode,
            reason_codes=["protection_no_new_entries", *_rule_reason_codes(matched)],
            matched_rules=matched,
        )
    return ProtectionDecision(allowed=True, mode=mode, matched_rules=matched)


def protection_state_from_dict(payload: dict[str, Any] | None) -> ProtectionState:
    if not payload:
        return ProtectionState()
    rules_payload = payload.get("rules") or []
    if not isinstance(rules_payload, list):
        raise ValueError("protection rules must be a list")
    return ProtectionState(rules=tuple(protection_rule_from_dict(item) for item in rules_payload))


def protection_rule_from_dict(payload: dict[str, Any]) -> ProtectionRule:
    if not isinstance(payload, dict):
        raise ValueError("protection rule must be an object")
    return ProtectionRule(
        rule_id=str(payload.get("rule_id") or payload.get("id") or ""),
        mode=str(payload.get("mode") or ""),
        scope=str(payload.get("scope") or "global"),
        target=str(payload.get("target") or "*"),
        reason=str(payload.get("reason") or ""),
        expires_at=_optional_datetime(payload.get("expires_at")),
        created_at=_optional_datetime(payload.get("created_at")),
    )


def _rule_reason_codes(rules: tuple[ProtectionRule, ...]) -> list[str]:
    return [f"protection_rule:{rule.rule_id}" for rule in rules]


def _normalize_target(scope: str, target: str) -> str:
    raw = str(target or "*").strip()
    if scope == "global":
        return "*"
    if scope == "exchange":
        return raw.lower()
    if scope == "strategy":
        return raw.lower()
    if scope == "symbol":
        try:
            return normalize_symbol(raw)
        except SignalValidationError as exc:
            raise ValueError(str(exc)) from exc
    return raw


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _aware_utc(value)
    return _aware_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
