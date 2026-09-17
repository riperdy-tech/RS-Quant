"""Fault tests for risk protection and emergency recovery.

Exercises real engine components: kill latch persistence through restart,
entry blocking, protection retention, and flatten behavior during outage.
"""



def test_kill_survives_restart_and_does_not_claim_flat(case):
    """§11.2 acceptance test from IMPLEMENTATION_PLAN.md Task 08."""
    r = case("kill_with_venue_outage", restart=True)
    assert r["kill_latched_after_restart"] is True
    assert r["entry_dispatches_after_kill"] == 0
    assert r["protective_orders_canceled_by_kill"] == 0
    assert r["flatten_status"] == "FLATTEN_BLOCKED"
    assert r["reported_position_lots"] > 0


def test_kill_without_restart(case):
    """Kill latch active even without restart."""
    r = case("kill_with_venue_outage", restart=False)
    assert r["kill_latched_after_restart"] is True
    assert r["entry_dispatches_after_kill"] == 0
    assert r["flatten_status"] == "FLATTEN_BLOCKED"
    assert r["reported_position_lots"] > 0
