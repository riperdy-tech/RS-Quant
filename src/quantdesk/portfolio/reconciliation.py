"""Time-bounded comparisons only. Recovery policy and convergence belong to Task07."""

from dataclasses import dataclass
from decimal import Decimal

from quantdesk.core.events import (
    AccountSnapshotObserved,
    PositionDiscrepancy,
    ReconciliationObservation,
)
from quantdesk.portfolio.arithmetic import ZERO, exact_sum, money
from quantdesk.portfolio.ledger import LedgerState


@dataclass(frozen=True, slots=True)
class BalanceDiscrepancy:
    asset: str
    expected: Decimal | None
    observed: Decimal | None
    difference: Decimal | None
    reason: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    observation: ReconciliationObservation
    position_discrepancies: tuple[PositionDiscrepancy, ...]
    balance_discrepancies: tuple[BalanceDiscrepancy, ...]


class Reconciler:
    @staticmethod
    def compare(
        state: LedgerState,
        snapshot: AccountSnapshotObserved,
        start_receive_seq: int,
        end_receive_seq: int,
        settlement_rounding_unit: Decimal,
    ) -> ReconciliationResult:
        if money(settlement_rounding_unit) <= 0:
            raise ValueError("positive documented settlement rounding unit required")
        if not snapshot.observation_id or not 0 <= start_receive_seq <= end_receive_seq:
            raise ValueError("invalid observation identity/receive window")
        balances = dict(snapshot.wallet_balances)
        positions = dict(snapshot.positions)
        if len(balances) != len(snapshot.wallet_balances) or len(positions) != len(
            snapshot.positions
        ):
            raise ValueError("duplicate observation keys")
        for amount in balances.values():
            money(amount)
        if any(type(lots) is not int for lots in positions.values()):
            raise TypeError("observed position lots must be exact integers")
        local_cash = {b.asset: b.amount for b in state.balances if b.account == f"cash:{b.asset}"}
        local_cash.setdefault("USDT", ZERO)
        balance_differences: list[BalanceDiscrepancy] = []
        for asset in sorted(local_cash.keys() | balances.keys()):
            expected = local_cash.get(asset, ZERO)
            observed = balances.get(asset)
            difference = None if observed is None else exact_sum(observed, expected.copy_negate())
            tolerance = settlement_rounding_unit if asset == "USDT" else ZERO
            if difference is None or difference.copy_abs() > tolerance:
                balance_differences.append(
                    BalanceDiscrepancy(
                        asset,
                        expected,
                        observed,
                        difference,
                        "BALANCE_UNAVAILABLE" if observed is None else "BALANCE_DIVERGENCE",
                    )
                )
        local_positions = {p.instrument_id: p.signed_lots for p in state.positions}
        position_differences = tuple(
            PositionDiscrepancy(
                instrument,
                local_positions.get(instrument, 0),
                positions.get(instrument, 0),
                "QUANTITY_DIVERGENCE",
            )
            for instrument in sorted(local_positions.keys() | positions.keys())
            if local_positions.get(instrument, 0) != positions.get(instrument, 0)
        )
        reasons = tuple(f"{d.reason}:{d.asset}" for d in balance_differences) + tuple(
            f"QUANTITY_DIVERGENCE:{d.instrument_id}" for d in position_differences
        )
        return ReconciliationResult(
            ReconciliationObservation(
                snapshot.observation_id, start_receive_seq, end_receive_seq, not reasons, reasons
            ),
            position_differences,
            tuple(balance_differences),
        )
