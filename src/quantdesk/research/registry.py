from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from quantdesk.research.train import ModelManifest


class ModelState(StrEnum):
    """Model governance lifecycle states per §13.3."""

    TRAINING = "TRAINING"
    EVALUATED = "EVALUATED"
    REJECTED = "REJECTED"
    SHADOW = "SHADOW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


@dataclass
class RegistryResult:
    """Outcome of a model state transition or rollback per §13.3."""

    success: bool
    active_model_id: str | None
    reason: str
    target_state: str | None = None


class Registry:
    """Model governance registry tracking artifacts, states, champions, and rollback per §13.3."""

    def __init__(self) -> None:
        self.models: dict[str, Any] = {}
        self.manifests: dict[str, ModelManifest] = {}
        self.states: dict[str, str] = {}
        self.champions: dict[tuple[str, str], str] = {}
        self.champion_history: dict[tuple[str, str], list[str]] = {}
        self.active_model_id: str | None = None
        self.active_manifest: ModelManifest | None = None

    def register(
        self,
        model: Any,
        manifest: ModelManifest,
        initial_state: str = "EVALUATED",
    ) -> None:
        """Registers a model artifact and immutable manifest into the registry."""
        self.models[manifest.model_id] = model
        self.manifests[manifest.model_id] = manifest
        self.states[manifest.model_id] = initial_state
        manifest.status = initial_state

    def transition(self, command: dict[str, Any]) -> RegistryResult:
        """Transitions a model artifact through governance lifecycle states (§13.3)."""
        action = command.get("action")
        strategy_id = str(command.get("strategy_id", "default"))
        environment = str(command.get("environment", "DEMO")).upper()
        env_key = (strategy_id, environment)

        if action == "ROLLBACK":
            return self.rollback(strategy_id=strategy_id, environment=environment)

        target_id = command.get("model_id")
        if not target_id or target_id not in self.manifests:
            return RegistryResult(
                False, self.active_model_id, f"Model {target_id} not found in registry"
            )

        manifest = self.manifests[target_id]
        current_state = self.states.get(target_id, manifest.status)

        # 1. Determine target state
        if action == "PROMOTE":
            target_state = ModelState.ACTIVE.value
        elif action == "REJECT":
            target_state = ModelState.REJECTED.value
        elif action == "APPROVE":
            target_state = ModelState.APPROVED.value
        elif action == "SHADOW":
            target_state = ModelState.SHADOW.value
        elif action == "RETIRE":
            target_state = ModelState.RETIRED.value
        else:
            target_state = str(command.get("target_state", ModelState.ACTIVE.value)).upper()

        if target_state not in [s.value for s in ModelState]:
            return RegistryResult(
                False, self.active_model_id, f"Invalid target state: {target_state}"
            )

        # 2. Hash and schema validation
        expected_payload_hash = command.get("expected_payload_hash")
        if expected_payload_hash and expected_payload_hash != manifest.model_payload_hash:
            return RegistryResult(False, self.active_model_id, "Payload hash mismatch")

        expected_schema_hash = command.get("expected_feature_schema_hash")
        if expected_schema_hash and expected_schema_hash != manifest.feature_schema_hash:
            return RegistryResult(False, self.active_model_id, "Feature schema mismatch")

        # 3. Disallow promotion of rejected models
        if current_state == ModelState.REJECTED.value and target_state in (
            ModelState.APPROVED.value,
            ModelState.ACTIVE.value,
            ModelState.SHADOW.value,
        ):
            return RegistryResult(
                False, self.active_model_id, "Cannot promote a rejected model candidate"
            )

        # 4. Live-environment restrictions
        if target_state == ModelState.ACTIVE.value and environment == "LIVE":
            # Synthetic data live-block
            if manifest.dataset_origin in ("demo-only", "synthetic"):
                return RegistryResult(
                    False,
                    self.active_model_id,
                    "Candidate models trained on synthetic data can never become live champions",
                )

            # Flat position guard: disallow live strategy/model change with open exposure
            current_position_qty = command.get("current_position_qty", 0)
            has_unresolved_orders = command.get("has_unresolved_orders", False)
            if current_position_qty != 0 or has_unresolved_orders:
                return RegistryResult(
                    False,
                    self.active_model_id,
                    "Cannot change active model while position is open or unresolved orders exist",
                )

            # Operator confirmation check
            if not command.get("operator_confirmed", False):
                return RegistryResult(
                    False,
                    self.active_model_id,
                    "Live promotion requires operator confirmation",
                )

        # 5. Apply state transition
        self.states[target_id] = target_state
        manifest.status = target_state

        if target_state == ModelState.ACTIVE.value:
            prev_champion = self.champions.get(env_key)
            if prev_champion and prev_champion != target_id:
                if env_key not in self.champion_history:
                    self.champion_history[env_key] = []
                self.champion_history[env_key].append(prev_champion)
                self.states[prev_champion] = ModelState.RETIRED.value
                self.manifests[prev_champion].status = ModelState.RETIRED.value

            self.champions[env_key] = target_id
            self.active_model_id = target_id
            self.active_manifest = manifest

        return RegistryResult(
            True,
            self.active_model_id,
            f"Transition to {target_state} successful",
            target_state=target_state,
        )

    def rollback(self, strategy_id: str = "default", environment: str = "DEMO") -> RegistryResult:
        """Rolls back active champion to the immutable prior artifact (§13.3)."""
        env_key = (strategy_id, environment.upper())
        history = self.champion_history.get(env_key, [])
        if not history:
            return RegistryResult(
                False, self.active_model_id, "No prior champion available for rollback"
            )

        prior_id = history.pop()
        prior_manifest = self.manifests[prior_id]

        current_active = self.champions.get(env_key)
        if current_active and current_active in self.manifests:
            self.states[current_active] = ModelState.RETIRED.value
            self.manifests[current_active].status = ModelState.RETIRED.value

        self.states[prior_id] = ModelState.ACTIVE.value
        prior_manifest.status = ModelState.ACTIVE.value
        self.champions[env_key] = prior_id
        self.active_model_id = prior_id
        self.active_manifest = prior_manifest

        return RegistryResult(
            True,
            prior_id,
            f"Rollback successful: restored champion {prior_id}",
            target_state=ModelState.ACTIVE.value,
        )

    def get_active_model(
        self, strategy_id: str = "default", environment: str = "DEMO"
    ) -> Any | None:
        """Returns the active champion model instance for the given strategy and environment."""
        env_key = (strategy_id, environment.upper())
        model_id = self.champions.get(env_key, self.active_model_id)
        if model_id is None:
            return None
        return self.models.get(model_id)

    def get_active_manifest(
        self, strategy_id: str = "default", environment: str = "DEMO"
    ) -> ModelManifest | None:
        """Returns the active champion manifest for the given strategy and environment."""
        env_key = (strategy_id, environment.upper())
        model_id = self.champions.get(env_key, self.active_model_id)
        if model_id is None:
            return None
        return self.manifests.get(model_id)

    def get_model(self, model_id: str) -> Any | None:
        return self.models.get(model_id)

    def get_manifest(self, model_id: str) -> ModelManifest | None:
        return self.manifests.get(model_id)

    def get_state(self, model_id: str) -> str | None:
        return self.states.get(model_id)
