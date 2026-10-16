import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def macd(close: pd.Series, f=12, s=26, sig=9):
    line = ema(close, f) - ema(close, s)
    signal = ema(line, sig)
    return line, signal, line - signal


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema20"], d["ema50"], d["ema200"] = ema(d.close, 20), ema(d.close, 50), ema(d.close, 200)
    d["rsi"] = rsi(d.close)
    d["atr"] = atr(d)
    d["macd"], d["macd_sig"], d["macd_hist"] = macd(d.close)
    d["adx"] = adx(d)
    d["vol_ma"] = d.volume.rolling(20).mean()
    d["hh"] = d.high.rolling(20).max().shift()   # пробойные уровни предыдущих 20 свечей
    d["ll"] = d.low.rolling(20).min().shift()
    return d
