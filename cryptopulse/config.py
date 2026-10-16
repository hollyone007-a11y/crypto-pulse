from dataclasses import dataclass


@dataclass
class Config:
    symbol: str = "BTCUSDT"
    htf: str = "4h"            # старший таймфрейм: определяет тренд
    ltf: str = "1h"            # рабочий таймфрейм: вход
    equity: float = 1000.0     # стартовый баланс (USDT)
    risk_per_trade: float = 0.01   # риск на сделку = 1% депозита
    target_roe: float = 0.50   # цель: +50% к марже (с учётом плеча)
    max_leverage: int = 10     # потолок плеча
    min_rr: float = 2.0        # минимальное соотношение прибыль/риск
    min_score: float = 5.0     # минимальный балл сигнала (из ~10)
    atr_sl_mult: float = 1.5   # стоп = 1.5 ATR
    atr_tp_mult: float = 3.5   # тейк = 3.5 ATR
    fee: float = 0.0005        # комиссия тейкера на сторону
    slippage: float = 0.0002
    max_daily_loss: float = 0.05   # стоп-торговля на день при -5%
    max_open: int = 1
    cooldown_bars: int = 3
    default_funding: float = 0.0001  # funding за 8ч, если реальной истории нет (лонг платит)
    min_htf_adx: float = 15.0        # фильтр режима: во флэте на старшем ТФ не торгуем
    max_drawdown: float = 0.15       # kill-switch: остановка при просадке от пика
    max_loss_streak: int = 4         # серия стопов подряд -> уменьшаем риск
    streak_risk_mult: float = 0.5
