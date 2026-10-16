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


# ---------- длинная история с кэшем ----------
import os
import time as _time

MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


def _save(df: pd.DataFrame, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.reset_index().to_csv(path, index=False)


def fetch_history(symbol: str, interval: str, start: str, end: str | None = None, cache_dir: str = "data",
                  session=None) -> pd.DataFrame:
    """Постраничная выгрузка свечей (по 1500) с докачкой кэша в CSV. Незакрытые свечи отбрасываются."""
    http = session or requests
    path = f"{cache_dir}/{symbol}_{interval}.csv"
    cached = load_csv(path) if os.path.exists(path) else None
    since = int(pd.Timestamp(start).timestamp() * 1000)
    if cached is not None and len(cached):
        since = max(since, int(cached.index[-1].timestamp() * 1000) + MS[interval])
    until = int(pd.Timestamp(end).timestamp() * 1000) if end else int(_time.time() * 1000)
    rows = []
    while since < until:
        r = http.get(URL, params={"symbol": symbol, "interval": interval, "startTime": since, "endTime": until,
                                  "limit": 1500}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows += batch
        since = int(batch[-1][0]) + MS[interval]
        if len(batch) < 1500:
            break
    new = pd.DataFrame()
    if rows:
        new = pd.DataFrame(rows)
        new = new[new[6].astype("int64") < int(_time.time() * 1000)].iloc[:, :6]   # только закрытые
        new.columns = COLS
        new["time"] = pd.to_datetime(new["time"].astype("int64"), unit="ms")
        new = new.astype({c: float for c in COLS[1:]}).set_index("time")
    df = pd.concat([cached, new]) if cached is not None else new
    df = df[~df.index.duplicated()].sort_index()
    if len(df):
        _save(df, path)
    return df.loc[pd.Timestamp(start):] if len(df) else df


def fetch_funding(symbol: str, start: str, end: str | None = None, cache_dir: str = "data", session=None) -> pd.Series:
    """История funding rate (раз в 8ч). Возвращает Series: время -> ставка."""
    http = session or requests
    path = f"{cache_dir}/{symbol}_funding.csv"
    cached = None
    if os.path.exists(path):
        c = pd.read_csv(path, parse_dates=["time"]).set_index("time")
        cached = c["rate"]
    since = int(pd.Timestamp(start).timestamp() * 1000)
    if cached is not None and len(cached):
        since = max(since, int(cached.index[-1].timestamp() * 1000) + 1)
    until = int(pd.Timestamp(end).timestamp() * 1000) if end else int(_time.time() * 1000)
    rows = []
    while since < until:
        r = http.get(FUNDING_URL, params={"symbol": symbol, "startTime": since, "endTime": until, "limit": 1000},
                     timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows += batch
        since = int(batch[-1]["fundingTime"]) + 1
        if len(batch) < 1000:
            break
    new = pd.Series({pd.to_datetime(int(x["fundingTime"]), unit="ms").round("s"): float(x["fundingRate"])
                     for x in rows}, dtype=float)
    s = pd.concat([cached, new]) if cached is not None else new
    s = s[~s.index.duplicated()].sort_index()
    if len(s):
        s.rename("rate").rename_axis("time").reset_index().to_csv(path, index=False)
    return s.loc[pd.Timestamp(start):] if len(s) else s
