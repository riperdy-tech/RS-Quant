
def test_future_changes_do_not_change_earlier_decisions(case):
    r = case("causal_prefix", cutoff_ns=10_000_000_000,
             mutate_future_prices=True, mutate_late_receipts=True)
    assert r["original_prefix_features"] == r["mutated_prefix_features"]
    assert r["original_prefix_intents"] == r["mutated_prefix_intents"]
    assert r["warmup_entry_count"] == 0
