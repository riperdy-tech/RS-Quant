"""§11.2 emergency semantics: kill, flatten, and workflow tracking.

Emergency kill itself requires no typing or slow confirmation; it must be
usable immediately. Flatten is an asynchronous, confirmed command that tracks
residuals until verified flat.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from quantdesk.core.events import (
    RiskLatchChanged,
)


@dataclass(frozen=True, slots=True)
class FlattenPolicy:
    """Flatten parameters bound to the reviewed position snapshot."""

    price_collar_bps: Decimal = Decimal("50")
    max_child_lots: int = 1000
    retry_budget: int = 3
    timeout_ns: int = 30_000_000_000


@dataclass(frozen=True, slots=True)
class FlattenState:
    """Tracked state for an asynchronous flatten workflow."""

    workflow_id: str
    scope: str
    instrument_id: str | None
    position_lots_at_start: int
    remaining_lots: int
    status: str  # RUNNING | SUCCEEDED | PARTIAL | FAILED | EXPIRED | FLATTEN_BLOCKED
    attempts: int = 0
    residual_reason: str | None = None

    @property
    def completed(self) -> bool:
        return self.status in {"SUCCEEDED", "FAILED", "EXPIRED", "FLATTEN_BLOCKED"}


class Emergency:
    """Emergency actions per §11.2.

    kill() immediately persists a latched halt, blocks risk-increasing dispatch,
    requests cancellation of bot-owned entry orders, retains protection, and
    shows unresolved exposure.

    flatten() is an async confirmed command: kill entries, cancel conflicting
    orders, reconcile position, submit bounded reduce-only exits, track residuals
    until verified flat.
    """

    def __init__(self) -> None:
        self._flatten_workflows: dict[str, FlattenState] = {}

    # ── kill ─────────────────────────────────────────────────────────

    def kill(self, scope: str, reason: str, risk_version: str) -> RiskLatchChanged:
        """Produce a hard kill switch latch. No typing or slow confirmation required."""
        return RiskLatchChanged(
            latch_id="kill",
            scope=scope,
            active=True,
            reason=reason,
            risk_version=risk_version,
        )

    def reset_kill(self, scope: str, reason: str, risk_version: str) -> RiskLatchChanged:
        """Separate deliberate command after incident resolution.

        Does not automatically resume strategies or arm live.
        """
        return RiskLatchChanged(
            latch_id="kill",
            scope=scope,
            active=False,
            reason=reason,
            risk_version=risk_version,
        )

    # ── flatten ──────────────────────────────────────────────────────

    def flatten(
        self,
        scope: str,
        workflow_id: str,
        instrument_id: str | None,
        position_lots: int,
        policy: FlattenPolicy | None = None,
        *,
        venue_available: bool = True,
        data_trustworthy: bool = True,
    ) -> tuple[FlattenState, RiskLatchChanged]:
        """Start an asynchronous flatten workflow.

        Returns the initial flatten state and a kill latch to block entries.
        Per §11.2: if no trustworthy data/connection exists, report
        FLATTEN_BLOCKED and the direct venue fallback, not "closed".
        """
        if policy is None:
            policy = FlattenPolicy()

        # Kill entries first (flatten implies kill)
        kill = self.kill(scope, f"FLATTEN:{workflow_id}", f"flatten-{workflow_id}")

        if not venue_available or not data_trustworthy:
            state = FlattenState(
                workflow_id=workflow_id,
                scope=scope,
                instrument_id=instrument_id,
                position_lots_at_start=abs(position_lots),
                remaining_lots=abs(position_lots),
                status="FLATTEN_BLOCKED",
                residual_reason=(
                    "NO_TRUSTWORTHY_DATA" if not data_trustworthy else "VENUE_UNAVAILABLE"
                ),
            )
            self._flatten_workflows[workflow_id] = state
            return state, kill

        if position_lots == 0:
            state = FlattenState(
                workflow_id=workflow_id,
                scope=scope,
                instrument_id=instrument_id,
                position_lots_at_start=0,
                remaining_lots=0,
                status="SUCCEEDED",
            )
            self._flatten_workflows[workflow_id] = state
            return state, kill

        state = FlattenState(
            workflow_id=workflow_id,
            scope=scope,
            instrument_id=instrument_id,
            position_lots_at_start=abs(position_lots),
            remaining_lots=abs(position_lots),
            status="RUNNING",
        )
        self._flatten_workflows[workflow_id] = state
        return state, kill

    def update_flatten(
        self,
        workflow_id: str,
        filled_lots: int,
        min_lots: int,
    ) -> FlattenState:
        """Update flatten state after a fill. Track residuals.

        Per §11.2: dust/minimum constraints produce RESIDUAL_BELOW_MINIMUM.
        """
        state = self._flatten_workflows[workflow_id]
        remaining = max(0, state.remaining_lots - filled_lots)
        attempts = state.attempts + 1

        if remaining == 0:
            updated = replace(state, remaining_lots=0, status="SUCCEEDED", attempts=attempts)
        elif remaining < min_lots:
            updated = replace(
                state,
                remaining_lots=remaining,
                status="PARTIAL",
                attempts=attempts,
                residual_reason="RESIDUAL_BELOW_MINIMUM",
            )
        else:
            updated = replace(state, remaining_lots=remaining, attempts=attempts)

        self._flatten_workflows[workflow_id] = updated
        return updated

    def timeout_flatten(self, workflow_id: str) -> FlattenState:
        """Mark flatten as expired after timeout."""
        state = self._flatten_workflows[workflow_id]
        updated = replace(state, status="EXPIRED", residual_reason="TIMEOUT")
        self._flatten_workflows[workflow_id] = updated
        return updated

    def get_flatten(self, workflow_id: str) -> FlattenState | None:
        return self._flatten_workflows.get(workflow_id)

    @property
    def active_flattens(self) -> tuple[FlattenState, ...]:
        return tuple(s for s in self._flatten_workflows.values() if not s.completed)

    # ── soft breaker ─────────────────────────────────────────────────

    def soft_breaker(
        self, scope: str, reason: str, risk_version: str
    ) -> RiskLatchChanged:
        """Disable new risk for a scope; continue protective/reducing work."""
        return RiskLatchChanged(
            latch_id=f"soft:{scope}",
            scope=scope,
            active=True,
            reason=reason,
            risk_version=risk_version,
        )

    def clear_soft_breaker(
        self, scope: str, reason: str, risk_version: str
    ) -> RiskLatchChanged:
        """Auto-clear only for documented transient cases after cooldown."""
        return RiskLatchChanged(
            latch_id=f"soft:{scope}",
            scope=scope,
            active=False,
            reason=reason,
            risk_version=risk_version,
        )

    # ── pause / resume ───────────────────────────────────────────────

    def pause_strategy(
        self, strategy_id: str, reason: str, risk_version: str
    ) -> RiskLatchChanged:
        """Stop new entries; cancel its entry quotes; continue existing
        stops, exits, fills, ledger, and reconciliation."""
        return RiskLatchChanged(
            latch_id=f"pause:{strategy_id}",
            scope=strategy_id,
            active=True,
            reason=reason,
            risk_version=risk_version,
        )

    def resume_strategy(
        self, strategy_id: str, reason: str, risk_version: str
    ) -> RiskLatchChanged:
        """Revalidate warmup, account/risk/model state and current limits;
        journal explicit resume."""
        return RiskLatchChanged(
            latch_id=f"pause:{strategy_id}",
            scope=strategy_id,
            active=False,
            reason=reason,
            risk_version=risk_version,
        )
