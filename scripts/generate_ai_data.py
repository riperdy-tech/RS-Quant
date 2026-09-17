import os

import numpy as np
import pandas as pd


def generate_synthetic_l2_data(output_path="data/btc_usdt_l2.parquet"):
    print("Generating 14 days of synthetic high-resolution L2 data...")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Generate 10,000 rows of synthetic order book and trade data
    np.random.seed(42)
    timestamps = pd.date_range(start="2026-09-01", periods=10000, freq="1S")
    
    # Random walk for mid price
    returns = np.random.normal(loc=0, scale=0.0001, size=10000)
    mid_prices = 60000 * np.exp(np.cumsum(returns))
    
    df = pd.DataFrame({
        "timestamp": timestamps,
        "mid_price": mid_prices,
        "ask_price_1": mid_prices + np.random.uniform(0.5, 2.0, 10000),
        "ask_size_1": np.random.uniform(0.1, 5.0, 10000),
        "bid_price_1": mid_prices - np.random.uniform(0.5, 2.0, 10000),
        "bid_size_1": np.random.uniform(0.1, 5.0, 10000),
        "trade_price": mid_prices + np.random.normal(0, 0.5, 10000),
        "trade_volume": np.random.exponential(1.0, 10000),
        "trade_side": np.random.choice([1, -1], size=10000)
    })
    
    df.to_parquet(output_path)
    print(f"Saved dataset to {output_path}")

if __name__ == "__main__":
    generate_synthetic_l2_data()
