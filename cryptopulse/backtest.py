"""Бэктест без заглядывания в будущее: сигнал на закрытии свечи i, вход по открытию i+1."""
import numpy as np
import pandas as pd

from .analysis import generate_signal
from .config import Config
from .data import resample
from .indicators import add_indicators
from .position import Position
from .risk import position_size

TF_DELTA = {"1h": "1h", "4h": "4h", "15m": "15min", "1d": "1D"}


def prepare(ltf_df: pd.DataFrame, cfg: Config):
    """Считает индикаторы и привязывает к каждой LTF-свече последнюю ЗАКРЫТУЮ HTF-свечу."""
    ltf = add_indicators(ltf_df)
    htf_raw = resample(ltf_df, TF_DELTA[cfg.htf])
    htf = add_indicators(htf_raw)
    htf.index = htf.index + pd.Timedelta(TF_DELTA[cfg.htf])  # доступна только после закрытия
    htf = htf.add_prefix("h_")
    merged = pd.merge_asof(ltf, htf, left_index=True, right_index=True)
    return merged


class _HTF:
    def __init__(self, row):
        self.__dict__.update({k[2:]: v for k, v in row.items() if k.startswith("h_")})


def run_backtest(ltf_df: pd.DataFrame, cfg: Config):
    m = prepare(ltf_df, cfg)
    equity, peak, max_dd = cfg.equity, cfg.equity, 0.0
    pos, pending, cooldown = None, None, 0
    trades, curve = [], []
    day, day_start = None, equity
    rows = list(m.itertuples())
    for i in range(1, len(rows) - 1):
        bar, nxt = rows[i], rows[i + 1]
        d = bar.Index.date()
        if d != day:
            day, day_start = d, equity
        # 1) исполнить отложенный вход по открытию текущей свечи
        if pending is not None and pos is None:
            sig = pending
            qty, margin = position_size(equity, sig, cfg)
            # SL/TP пересчитываем относительно фактической цены входа (сохраняем дистанции)
            shift = bar.open - sig.entry
            pos = Position(sig.side, bar.open, sig.sl + shift, sig.tp + shift, qty, margin, sig.leverage,
                           bar.Index, sig.score, sig.reasons, sig.ctx)
        pending = None
        # 2) сопровождение позиции
        if pos is not None:
            res = pos.update(bar)
            if res:
                px, why = res
                pnl = max(pos.pnl(px, cfg.fee, cfg.slippage), -pos.margin)
                equity += pnl
                trades.append({"open": pos.open_time, "close": bar.Index, "side": pos.side, "entry": pos.entry,
                               "exit": px, "pnl": pnl, "roe": pnl / pos.margin, "leverage": pos.leverage,
                               "exit_reason": why, "bars": pos.bars, "mae": pos.mae, "mfe": pos.mfe,
                               "score": pos.score, "reasons": ",".join(pos.reasons), **pos.ctx})
                pos, cooldown = None, cfg.cooldown_bars
        # 3) новый сигнал
        cooldown = max(0, cooldown - 1)
        if pos is None and cooldown == 0 and (equity - day_start) / day_start > -cfg.max_daily_loss and equity > 0:
            sig = generate_signal(bar, rows[i - 1], _HTF(bar._asdict()), cfg)
            if sig:
                pending = sig
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        curve.append(equity)
    return pd.DataFrame(trades), stats(trades, cfg.equity, equity, max_dd, cfg)


def stats(trades, start_eq, end_eq, max_dd, cfg):
    if not trades:
        return {"trades": 0, "return_pct": 0.0, "max_dd_pct": 0.0}
    t = pd.DataFrame(trades)
    w, l = t[t.pnl > 0], t[t.pnl <= 0]
    return {
        "trades": len(t),
        "win_rate_pct": round(100 * len(w) / len(t), 1),
        "profit_factor": round(w.pnl.sum() / abs(l.pnl.sum()), 2) if len(l) and l.pnl.sum() else float("inf"),
        "return_pct": round(100 * (end_eq / start_eq - 1), 2),
        "max_dd_pct": round(100 * max_dd, 2),
        "avg_roe_pct": round(100 * t.roe.mean(), 1),
        "hit_target_roe_pct": round(100 * (t.roe >= cfg.target_roe * 0.95).mean(), 1),
        "liquidations": int((t.exit_reason == "liquidation").sum()),
        "end_equity": round(end_eq, 2),
    }
