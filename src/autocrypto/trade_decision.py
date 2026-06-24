from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeControlDecision:
    reason_codes: list[str] = field(default_factory=list)
    approval_required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def rejected(self) -> bool:
        return bool(self.reason_codes)

