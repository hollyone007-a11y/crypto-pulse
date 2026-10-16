"""Живой цикл. По умолчанию paper (виртуальная торговля на реальных свечах)."""
import time

from .analysis import generate_signal
from .backtest import _HTF, prepare
from .broker import CcxtBroker, Journal
from .config import Config
from .data import fetch_klines
from .position import Position
from .risk import position_size

SECS = {"15m": 900, "1h": 3600, "4h": 14400}


def run(cfg: Config, mode: str = "paper", once: bool = False):
    broker = CcxtBroker() if mode == "live" else None
    journal = Journal()
    equity = broker.balance() if broker else cfg.equity
    pos, cooldown = None, 0
    print(f"[{mode}] {cfg.symbol} {cfg.ltf}/{cfg.htf} equity={equity:.2f}")
    while True:
        df = fetch_klines(cfg.symbol, cfg.ltf, 1000).iloc[:-1]   # только закрытые свечи
        m = prepare(df, cfg)
        bar, prev = next(m.iloc[-1:].itertuples()), next(m.iloc[-2:-1].itertuples())
        if pos is not None and mode == "paper":
            res = pos.update(bar)
            if res:
                px, why = res
                pnl = max(pos.pnl(px, cfg.fee, cfg.slippage), -pos.margin)
                equity += pnl
                journal.write(event="close", reason=why, pnl=pnl, roe=pnl / pos.margin, equity=equity, t=bar.Index)
                print(f"CLOSE {pos.side} {why} pnl={pnl:.2f} equity={equity:.2f}")
                pos, cooldown = None, cfg.cooldown_bars
        elif pos is not None and mode == "live" and broker.is_flat(cfg.symbol):
            equity = broker.balance(); pos, cooldown = None, cfg.cooldown_bars
            journal.write(event="close", equity=equity, t=bar.Index)
        cooldown = max(0, cooldown - 1)
        if pos is None and cooldown == 0:
            sig = generate_signal(bar, prev, _HTF(bar._asdict()), cfg)
            if sig:
                qty, margin = position_size(equity, sig, cfg)
                if broker:
                    qty = broker.open(cfg.symbol, sig, qty)
                pos = Position(sig.side, sig.entry, sig.sl, sig.tp, qty, margin, sig.leverage, bar.Index,
                               sig.score, sig.reasons, sig.ctx)
                journal.write(event="open", side=sig.side, entry=sig.entry, sl=sig.sl, tp=sig.tp, lev=sig.leverage,
                              qty=qty, score=sig.score, reasons=sig.reasons, ctx=sig.ctx, t=bar.Index)
                print(f"OPEN {sig.side} @ {sig.entry:.2f} SL {sig.sl:.2f} TP {sig.tp:.2f} x{sig.leverage} {sig.reasons}")
        if once:
            return
        time.sleep(SECS[cfg.ltf] - time.time() % SECS[cfg.ltf] + 5)   # ждём закрытия следующей свечи
