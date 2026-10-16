"""Walk-forward: параметры подбираются на «обучающем» окне и проверяются на следующем, невиданном окне.
Склеенные результаты тестовых окон — честная оценка (без подгонки под прошлое)."""
import itertools
from dataclasses import replace

import pandas as pd

from .backtest import prepare, run_backtest
from .config import Config

DEFAULT_GRID = {"min_score": [5.0, 6.0, 7.0], "atr_sl_mult": [1.2, 1.5, 2.0], "atr_tp_mult": [3.0, 3.5, 4.5]}


def _objective(st: dict, min_trades: int) -> float:
    if st["trades"] < min_trades or st["return_pct"] <= 0:
        return float("-inf")
    return st["return_pct"] / max(st["max_dd_pct"], 1.0)   # доходность на единицу просадки


def walk_forward(df: pd.DataFrame, cfg: Config, funding=None, train_bars: int = 2000, test_bars: int = 500,
                 grid: dict | None = None, warmup: int = 300, min_trades: int = 8):
    grid = grid or DEFAULT_GRID
    full = prepare(df, cfg, funding)            # индикаторы причинные -> можно считать один раз
    keys = list(grid)
    windows, all_trades = [], []
    for start in range(0, len(df) - train_bars - test_bars + 1, test_bars):
        tr_end, te_end = start + train_bars, start + train_bars + test_bars
        train = full.iloc[start:tr_end]
        best, best_val = None, float("-inf")
        for vals in itertools.product(*(grid[k] for k in keys)):
            c = replace(cfg, **dict(zip(keys, vals)))
            _, st = run_backtest(None, c, prepared=train, trade_from=train.index[warmup])
            v = _objective(st, min_trades)
            if v > best_val:
                best, best_val = dict(zip(keys, vals)), v
        test = full.iloc[tr_end - warmup:te_end]
        row = {"from": full.index[tr_end], "to": full.index[te_end - 1], "params": best, "train_score": best_val}
        if best is None:           # на обучении ничего устойчивого не нашли -> не торгуем
            row.update(trades=0, return_pct=0.0, max_dd_pct=0.0)
        else:
            t, st = run_backtest(None, replace(cfg, **best), prepared=test, trade_from=full.index[tr_end])
            row.update(trades=st["trades"], return_pct=st["return_pct"], max_dd_pct=st["max_dd_pct"])
            if len(t):
                all_trades.append(t)
        windows.append(row)
    w = pd.DataFrame(windows)
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    if w.empty:
        return w, trades, {"windows": 0}
    compounded = (1 + w.return_pct / 100).prod() - 1
    summary = {
        "windows": len(w),
        "oos_return_pct": round(100 * compounded, 2),
        "profitable_windows_pct": round(100 * (w.return_pct > 0).mean(), 1),
        "idle_windows": int((w.trades == 0).sum()),
        "oos_trades": int(w.trades.sum()),
        "worst_window_dd_pct": float(w.max_dd_pct.max()),
        "oos_win_rate_pct": round(100 * (trades.pnl > 0).mean(), 1) if len(trades) else 0.0,
    }
    return w, trades, summary
