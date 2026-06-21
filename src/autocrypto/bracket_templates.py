from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BracketTemplate:
    name: str
    description: str
    fields: dict[str, Any]
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "fields": deepcopy(self.fields),
            "notes": list(self.notes),
        }


BRACKET_TEMPLATES: dict[str, BracketTemplate] = {
    "fixed_bracket": BracketTemplate(
        name="fixed_bracket",
        description="Simple fixed stop-loss and take-profit paper bracket.",
        fields={
            "stop_loss_pct": "2",
            "take_profit_pct": "4",
        },
        notes=(
            "Defines initial risk with a fixed stop.",
            "No trailing leg is included.",
        ),
    ),
    "activation_trailer": BracketTemplate(
        name="activation_trailer",
        description="Fixed initial stop plus activation-gated stepped trailing stop.",
        fields={
            "stop_loss_pct": "4",
            "take_profit_pct": "8",
            "trailing_stop_pct": "3",
            "trailing_activation_pct": "2",
            "trailing_step_pct": "0.5",
        },
        notes=(
            "Keeps a fixed stop in place before the trailing stop activates.",
            "Trailing ratchets only after the configured step threshold.",
        ),
    ),
    "staged_runner": BracketTemplate(
        name="staged_runner",
        description="Staged take-profit paper bracket that lets a residual runner trail after target one.",
        fields={
            "stop_loss_pct": "3",
            "take_profit_targets": [
                {"pct": "4", "close_pct": "50"},
                {"pct": "8", "close_pct": "50"},
            ],
            "trailing_stop_pct": "3",
            "trailing_stop_close_pct": "50",
            "trail_after_take_profit": True,
            "breakeven_after_take_profit": True,
        },
        notes=(
            "The trailing stop is dormant until a paper take-profit fills.",
            "Remaining protective exits move to breakeven after the first target.",
        ),
    ),
}


def list_bracket_templates() -> list[dict[str, Any]]:
    return [template.to_dict() for template in BRACKET_TEMPLATES.values()]


def get_bracket_template(name: str) -> BracketTemplate:
    normalized = name.strip().lower()
    try:
        return BRACKET_TEMPLATES[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown bracket template: {name}") from exc


def apply_bracket_template(
    signal_payload: dict[str, Any],
    template_name: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(signal_payload, dict):
        raise ValueError("signal must be an object")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("overrides must be an object")

    template = get_bracket_template(template_name)
    merged = deepcopy(template.fields)
    merged.update(deepcopy(signal_payload))
    if overrides:
        merged.update(deepcopy(overrides))
    merged["bracket_template"] = template.name
    return merged

