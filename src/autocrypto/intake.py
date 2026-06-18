from __future__ import annotations

from typing import Any

from .approvals import ApprovalQueue
from .engine import TradingEngine
from .execution import ExecutionResult
from .repository import SQLiteRepository
from .risk import evaluate_signal
from .signals import CryptoSignal


class SignalIntakeService:
    """Owns signal lifecycle decisions between parsing and route responses."""

    def __init__(
        self,
        *,
        engine: TradingEngine,
        approvals: ApprovalQueue,
        repository: SQLiteRepository | None = None,
        require_approval: bool = False,
    ) -> None:
        self.engine = engine
        self.approvals = approvals
        self.repository = repository
        self.require_approval = require_approval

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
            if not self.repository.claim_signal(signal):
                self.repository.record_audit("signal.duplicate", {"signal_id": signal.signal_id})
                return {
                    "status": "duplicate",
                    "reason": "duplicate_signal",
                    "signal_id": signal.signal_id,
                }
            self.repository.record_audit("signal.received", {"signal_id": signal.signal_id})

        if self.require_approval:
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
        signal = (
            self.repository.pop_pending_approval(signal_id)
            if self.repository
            else self.approvals.pop(signal_id)
        )
        if signal is None:
            return None
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

    def _record_result(self, signal: CryptoSignal, result: Any) -> None:
        if not self.repository:
            return
        if result.order:
            self.repository.save_order(result.order)
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
