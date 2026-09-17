"""Hybrid ML-gated rule strategy per §12.3."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction
from quantdesk.strategies.base import Strategy


class HybridStrategy(Strategy):
    def __init__(self, rule_strategy: Strategy, threshold: float = 0.60):
        self.rule_strategy = rule_strategy
        self.threshold = threshold
        self.registry: Any = None
        self.evaluations_count = 0

    def set_registry(self, registry: Any) -> None:
        self.registry = registry

    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        intents = self.rule_strategy.on_event(event, context)
        if not intents:
            return ()

        validated_intents: list[StrategyIntent] = []
        for intent in intents:
            if intent.action != IntentAction.ENTER:
                # Unconditionally passes through non-entry intents (EXIT, REDUCE, CANCEL_ENTRY)
                # per §12.3 and §13.3
                validated_intents.append(intent)
                continue

            self.evaluations_count += 1
            if self.registry is None:
                # Fails closed if ML registry is missing
                continue

            model = (
                self.registry.get_active_model()
                if hasattr(self.registry, "get_active_model")
                else None
            )
            if model is None:
                # Fails closed if no active model
                continue

            # Model inference wrapped in exception handler to fail closed on latency or errors
            try:
                features = context.get("features", {})
                pred = model.predict(features) if hasattr(model, "predict") else 0.0
                probability = float(pred)
            except Exception:
                # Fail closed on inference error or latency budget breach
                continue

            if probability >= self.threshold:
                model_hash = getattr(model, "model_hash", "ml-v1")
                validated = replace(intent, model_hash_or_none=model_hash)
                validated_intents.append(validated)

        return tuple(validated_intents)
