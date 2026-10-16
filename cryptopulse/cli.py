import argparse
import json

from . import bot
from .backtest import run_backtest
from .config import Config
from .data import fetch_klines, load_csv, synthetic
from .report import analyze


def main():
    p = argparse.ArgumentParser(prog="cryptopulse")
    p.add_argument("command", choices=["backtest", "run", "telegram"])
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--csv", help="CSV со свечами: time,open,high,low,close,volume")
    p.add_argument("--synthetic", action="store_true", help="синтетические данные (проверка кода)")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--equity", type=float, default=1000)
    p.add_argument("--max-leverage", type=int, default=10)
    p.add_argument("--min-score", type=float, default=5.0)
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    cfg = Config(symbol=a.symbol, equity=a.equity, max_leverage=a.max_leverage, min_score=a.min_score)
    if a.command == "backtest":
        df = synthetic() if a.synthetic else load_csv(a.csv) if a.csv else fetch_klines(a.symbol, cfg.ltf, 1500)
        trades, st = run_backtest(df, cfg)
        print(json.dumps(st, indent=2, ensure_ascii=False))
        print(analyze(trades))
    elif a.command == "telegram":
        if a.mode == "live":
            input("LIVE через Telegram: реальные ордера с плечом. Enter — продолжить: ")
        from .telegram_ui import serve
        serve(cfg, a.mode)
    else:
        if a.mode == "live":
            input("LIVE: будут открываться реальные ордера с плечом. Enter — продолжить, Ctrl+C — отмена: ")
        bot.run(cfg, a.mode, a.once)


if __name__ == "__main__":
    main()
