"""Загрузка свечей: Binance Futures (публичный API), CSV, синтетика для тестов."""
import numpy as np
import pandas as pd
import requests

URL = "https://fapi.binance.com/fapi/v1/klines"
COLS = ["time", "open", "high", "low", "close", "volume"]


def fetch_klines(symbol: str, interval: str, limit: int = 1000) -> pd.DataFrame:
    r = requests.get(URL, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=15)
    r.raise_for_status()
    df = pd.DataFrame(r.json()).iloc[:, :6]
    df.columns = COLS
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df.astype({c: float for c in COLS[1:]}).set_index("time")


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"]).set_index("time")
    return df[COLS[1:]].astype(float)


def synthetic(n: int = 4000, seed: int = 7, start: float = 30000.0, freq: str = "1h") -> pd.DataFrame:
    """Режимный случайный процесс (тренд/флэт) — только для проверки кода, не для оценки прибыли."""
    rng = np.random.default_rng(seed)
    drift = np.zeros(n)
    i = 0
    while i < n:
        L = int(rng.integers(80, 400))
        drift[i:i + L] = rng.choice([-0.0006, 0.0, 0.0008])
        i += L
    ret = drift + rng.normal(0, 0.006, n)
    close = start * np.exp(np.cumsum(ret))
    open_ = np.r_[start, close[:-1]]
    spread = np.abs(rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    vol = rng.lognormal(5, 0.5, n) * (1 + 5 * np.abs(ret) / 0.006)
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
