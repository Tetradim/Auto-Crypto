from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .approvals import ApprovalQueue
from .engine import TradingEngine
from .execution import ExecutionResult
from .order_recorder import save_order_with_runtime_state
from .repository import SQLiteRepository
from .risk import RiskDecision, evaluate_signal
from .signals import CryptoSignal
from .trade_decision import RuntimeControlDecision


class SignalIntakeService:
    """Owns signal lifecycle decisions between parsing and route responses."""

    def __init__(
        self,
        *,
        engine: TradingEngine,
        approvals: ApprovalQueue,
        repository: SQLiteRepository | None = None,
        require_approval: bool = False,
        pre_trade_decision: Callable[[CryptoSignal], RuntimeControlDecision] | None = None,
    ) -> None:
        self.engine = engine
        self.approvals = approvals
        self.repository = repository
        self.require_approval = require_approval
        self.pre_trade_decision = pre_trade_decision

    def handle(self, signal: CryptoSignal) -> dict[str, Any]:
        if self.engine.halted:
            if self.repository:
                self.repository.save_signal(signal)
                self.repository.record_audit("signal.received", {"signal_id": signal.signal_id})
            result = self.engine.process_signal(signal)
            if self.repository:
                self.repository.record_audit(
                    "order.halted",
                    {"signal_id": signal.signal_id, "reason": result.reason},
            )
            return result.to_dict()

        if self.repository:
            if self.repository.signal_claimed(signal.signal_id):
                self.repository.record_audit("signal.duplicate", {"signal_id": signal.signal_id})
                return {
                    "status": "duplicate",
                    "reason": "duplicate_signal",
                    "signal_id": signal.signal_id,
                }

        runtime_decision = self._pre_trade_decision(signal)
        if runtime_decision.rejected:
            if self.repository:
                self.repository.save_signal(signal)
                self.repository.record_audit("signal.received", {"signal_id": signal.signal_id})
            decision = self._combined_rejection_decision(signal, runtime_decision.reason_codes)
            result = ExecutionResult(status="rejected", decision=decision, reason="runtime_controls_rejected")
            self._record_result(signal, result)
            return result.to_dict()

        if self.repository:
            if not self.repository.claim_signal(signal):
                self.repository.record_audit("signal.duplicate", {"signal_id": signal.signal_id})
                return {
                    "status": "duplicate",
                    "reason": "duplicate_signal",
                    "signal_id": signal.signal_id,
                }
            self.repository.record_audit("signal.received", {"signal_id": signal.signal_id})

        if self.require_approval or runtime_decision.approval_required:
            decision = evaluate_signal(signal, self.engine.risk_config, self.engine.account_state)
            if not decision.approved:
                result = ExecutionResult(status="rejected", decision=decision, reason="risk_rejected")
                self._record_result(signal, result)
                return result.to_dict()

            if self.repository:
                self.repository.save_pending_approval(signal)
                self.repository.record_audit("approval.requested", {"signal_id": signal.signal_id})
            else:
                self.approvals.add(signal)
            return {"status": "approval_required", "signal_id": signal.signal_id}

        result = self.engine.process_signal(signal)
        self._record_result(signal, result)
        return result.to_dict()

    def list_approvals(self) -> list[dict[str, Any]]:
        if self.repository:
            return self.repository.list_pending_approvals()
        return self.approvals.list_pending()

    def approve(self, signal_id: str) -> dict[str, Any] | None:
        if self.engine.halted:
            signal = (
                self.repository.get_pending_approval(signal_id)
                if self.repository
                else self.approvals.get(signal_id)
            )
            if signal is None:
                return None
            result = self.engine.process_signal(signal)
            self._record_result(signal, result)
            return result.to_dict()

        signal = (
            self.repository.pop_pending_approval(signal_id)
            if self.repository
            else self.approvals.pop(signal_id)
        )
        if signal is None:
            return None
        runtime_decision = self._pre_trade_decision(signal)
        if runtime_decision.rejected:
            decision = self._combined_rejection_decision(signal, runtime_decision.reason_codes)
            result = ExecutionResult(status="rejected", decision=decision, reason="runtime_controls_rejected")
            self._record_result(signal, result)
            return result.to_dict()
        result = self.engine.process_signal(signal)
        self._record_result(signal, result)
        return result.to_dict()

    def reject(self, signal_id: str, reason: str = "") -> dict[str, Any] | None:
        signal = (
            self.repository.pop_pending_approval(signal_id)
            if self.repository
            else self.approvals.pop(signal_id)
        )
        if signal is None:
            return None
        if self.repository:
            self.repository.record_audit(
                "approval.rejected",
                {"signal_id": signal.signal_id, "reason": reason},
            )
        return {"status": "rejected", "signal_id": signal.signal_id}

    def _pre_trade_decision(self, signal: CryptoSignal) -> RuntimeControlDecision:
        if self.pre_trade_decision is None:
            return RuntimeControlDecision()
        return self.pre_trade_decision(signal)

    def _combined_rejection_decision(
        self,
        signal: CryptoSignal,
        control_reasons: list[str],
    ) -> RiskDecision:
        self.engine.account_state.open_notional = self.engine.exchange.open_notional()
        self.engine.account_state.symbol_open_notional = self.engine.exchange.symbol_open_notional(signal.symbol)
        self.engine.account_state.open_risk_amount = self.engine.exchange.open_risk_amount()
        decision = evaluate_signal(signal, self.engine.risk_config, self.engine.account_state)
        return RiskDecision(
            approved=False,
            reason_codes=[*decision.reason_codes, *control_reasons],
            order_notional=decision.order_notional,
        )

    def _record_result(self, signal: CryptoSignal, result: Any) -> None:
        if not self.repository:
            return
        if result.order:
            save_order_with_runtime_state(self.repository, result.order)
            self.repository.record_audit("order.accepted", {"order_id": result.order.order_id})
        elif result.status == "halted":
            self.repository.record_audit(
                "order.halted",
                {"signal_id": signal.signal_id, "reason": result.reason},
            )
        elif result.status == "rejected":
            self.repository.record_audit(
                "order.rejected",
                {"signal_id": signal.signal_id, "reason_codes": result.decision.reason_codes},
            )
