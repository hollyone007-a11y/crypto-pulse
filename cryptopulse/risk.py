"""Риск-менеджмент: стоп/тейк от ATR, плечо под цель +50% ROE, размер позиции от риска."""
import math

from .analysis import Signal
from .config import Config


def build_trade(side, price, atr, score, reasons, cfg: Config, ctx=None):
    sgn = 1 if side == "long" else -1
    sl_d, tp_d = cfg.atr_sl_mult * atr, cfg.atr_tp_mult * atr
    if tp_d / sl_d < cfg.min_rr:
        return None
    # плечо, при котором движение цены до TP даёт >= target_roe на маржу (с запасом на комиссии)
    cost = 2 * (cfg.fee + cfg.slippage)
    net_move = tp_d / price - cost
    if net_move <= 0:
        return None
    lev = max(1, math.ceil(cfg.target_roe / net_move))
    if lev > cfg.max_leverage:
        return None  # цель +50% недостижима без чрезмерного плеча — сделку пропускаем
    # ликвидация должна быть заметно дальше стопа
    if (0.9 / lev) < 1.5 * (sl_d / price):
        return None
    return Signal(side, price, price - sgn * sl_d, price + sgn * tp_d, lev, score, list(reasons), ctx or {})


def position_size(equity: float, sig: Signal, cfg: Config, risk_mult: float = 1.0):
    """Возвращает (qty, margin). Риск на сделку = risk_per_trade * equity (включая комиссии)."""
    sl_frac = abs(sig.entry - sig.sl) / sig.entry
    loss_per_unit = sig.entry * (sl_frac + 2 * (cfg.fee + cfg.slippage))
    qty = equity * cfg.risk_per_trade * risk_mult / loss_per_unit
    margin = qty * sig.entry / sig.leverage
    cap = equity * 0.9
    if margin > cap:
        qty, margin = qty * cap / margin, cap
    return qty, margin
