
def test_rule_strategies_entry_and_exit_paths(case):
    r = case("rule_strategies")
    assert r["imbalance_entries"] > 0
    assert r["imbalance_exits"] > 0
    assert r["momentum_entries"] > 0
    assert r["momentum_exits"] > 0
    assert r["mean_reversion_entries"] > 0
    assert r["mean_reversion_exits"] > 0
    assert r["sweep_entries"] > 0
    assert r["sweep_exits"] > 0
    assert r["hybrid_evaluations"] > 0
