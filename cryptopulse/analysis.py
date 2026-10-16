"""Анализ рынка: мульти-таймфрейм скоринг лонга и шорта. Каждый балл имеет текстовую причину."""
from dataclasses import dataclass, field

from .config import Config


@dataclass
class Signal:
    side: str                 # "long" | "short"
    entry: float
    sl: float
    tp: float
    leverage: int
    score: float
    reasons: list = field(default_factory=list)
    ctx: dict = field(default_factory=dict)


def score_side(side: str, ltf, prev, htf):
    """Возвращает (балл, [причины]). ltf/prev/htf — строки с индикаторами."""
    s, why = 0.0, []
    sgn = 1 if side == "long" else -1

    # 1. Тренд старшего ТФ (обязательное условие, +3)
    if sgn * (htf.ema50 - htf.ema200) > 0 and sgn * (htf.close - htf.ema50) > 0:
        s += 3; why.append("htf_trend")
    else:
        return 0.0, ["no_htf_trend"]

    # 2. Структура рабочего ТФ
    if sgn * (ltf.ema20 - ltf.ema50) > 0 and sgn * (ltf.close - ltf.ema50) > 0:
        s += 1; why.append("ltf_ema_stack")
    # 3. Импульс MACD
    if sgn * ltf.macd_hist > 0 and sgn * (ltf.macd_hist - prev.macd_hist) > 0:
        s += 1; why.append("macd_momentum")
    # 4. RSI в «здоровой» зоне (не гонимся за перегретым)
    if (side == "long" and 45 <= ltf.rsi <= 70) or (side == "short" and 30 <= ltf.rsi <= 55):
        s += 1; why.append("rsi_zone")
    # 5. Сила тренда
    if ltf.adx > 22:
        s += 1; why.append("adx_trend")
    # 6. Пробой 20-свечного уровня на повышенном объёме
    broke = ltf.close > ltf.hh if side == "long" else ltf.close < ltf.ll
    if broke and ltf.volume > 1.2 * ltf.vol_ma:
        s += 2; why.append("volume_breakout")
    # 7. Откат к EMA20 в тренде
    elif abs(ltf.close - ltf.ema20) < 0.5 * ltf.atr and sgn * (ltf.close - prev.close) > 0:
        s += 1.5; why.append("ema20_pullback")
    return s, why


def generate_signal(ltf, prev, htf, cfg: Config):
    """Единая точка принятия решения (используется и в бэктесте, и в live)."""
    if any(v != v for v in (ltf.atr, ltf.ema50, htf.ema200, ltf.hh, ltf.ll, ltf.vol_ma)):
        return None  # прогрев индикаторов
    best = None
    for side in ("long", "short"):
        sc, why = score_side(side, ltf, prev, htf)
        if sc >= cfg.min_score and (best is None or sc > best[1]):
            best = (side, sc, why)
    if best is None:
        return None
    side, sc, why = best
    from .risk import build_trade
    return build_trade(side, ltf.close, ltf.atr, sc, why, cfg,
                       ctx={"rsi": float(ltf.rsi), "adx": float(ltf.adx), "atr_pct": float(ltf.atr / ltf.close)})
