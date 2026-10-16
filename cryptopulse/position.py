from dataclasses import dataclass, field


@dataclass
class Position:
    side: str
    entry: float
    sl: float
    tp: float
    qty: float
    margin: float
    leverage: int
    open_time: object
    score: float
    reasons: list
    ctx: dict
    mae: float = 0.0   # макс. неблагоприятное движение (доля от входа)
    mfe: float = 0.0   # макс. благоприятное движение
    bars: int = 0
    funding: float = 0.0   # накопленный funding (положительное = заплатили)
    extra: dict = field(default_factory=dict)

    @property
    def sgn(self):
        return 1 if self.side == "long" else -1

    def update(self, bar):
        """Применяет свечу. Возвращает (цена_выхода, причина) или None. При SL и TP в одной свече — считаем SL."""
        self.bars += 1
        f = getattr(bar, "fund", 0.0)
        if f:
            self.funding += self.sgn * f * self.qty * getattr(bar, "close", self.entry)
        fav = (bar.high if self.sgn > 0 else bar.low)
        adv = (bar.low if self.sgn > 0 else bar.high)
        self.mfe = max(self.mfe, self.sgn * (fav - self.entry) / self.entry)
        self.mae = max(self.mae, -self.sgn * (adv - self.entry) / self.entry)
        liq = self.entry * (1 - self.sgn * 0.9 / self.leverage)
        if self.sgn * (adv - self.sl) <= 0:
            return self.sl, "sl"
        if self.sgn * (adv - liq) <= 0:
            return liq, "liquidation"
        if self.sgn * (fav - self.tp) >= 0:
            return self.tp, "tp"
        return None

    def pnl(self, exit_price, fee, slip):
        gross = self.sgn * (exit_price - self.entry) * self.qty
        costs = self.qty * (self.entry + exit_price) * (fee + slip)
        return gross - costs - self.funding
