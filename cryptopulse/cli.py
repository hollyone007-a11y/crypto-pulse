import argparse
import json

from . import bot
from .backtest import run_backtest
from .config import Config
from .data import fetch_klines, load_csv, synthetic
from .report import analyze


def main():
    p = argparse.ArgumentParser(prog="cryptopulse")
    p.add_argument("command", choices=["backtest", "run", "telegram", "history", "walkforward"])
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--csv", help="CSV со свечами: time,open,high,low,close,volume")
    p.add_argument("--synthetic", action="store_true", help="синтетические данные (проверка кода)")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--equity", type=float, default=1000)
    p.add_argument("--max-leverage", type=int, default=10)
    p.add_argument("--min-score", type=float, default=5.0)
    p.add_argument("--start", default="2022-01-01", help="начало истории для history/walkforward")
    p.add_argument("--train", type=int, default=2000, help="walkforward: свечей в обучающем окне")
    p.add_argument("--test", type=int, default=500, help="walkforward: свечей в тестовом окне")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    cfg = Config(symbol=a.symbol, equity=a.equity, max_leverage=a.max_leverage, min_score=a.min_score)
    if a.command == "history":
        from .data import fetch_funding, fetch_history
        for itv in (cfg.ltf,):
            d = fetch_history(a.symbol, itv, a.start)
            print(f"{a.symbol} {itv}: {len(d)} свечей {d.index[0]} .. {d.index[-1]}")
        f = fetch_funding(a.symbol, a.start)
        print(f"funding: {len(f)} записей")
    elif a.command == "walkforward":
        from .data import fetch_funding, fetch_history
        from .walkforward import walk_forward
        df = synthetic(6000) if a.synthetic else fetch_history(a.symbol, cfg.ltf, a.start)
        fund = None if a.synthetic else fetch_funding(a.symbol, a.start)
        w, trades, summary = walk_forward(df, cfg, fund, a.train, a.test)
        print(w.to_string())
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        print(analyze(trades))
    elif a.command == "backtest":
        from .data import fetch_funding, fetch_history
        if a.synthetic:
            df, fund = synthetic(), None
        elif a.csv:
            df, fund = load_csv(a.csv), None
        else:
            df, fund = fetch_history(a.symbol, cfg.ltf, a.start), fetch_funding(a.symbol, a.start)
        trades, st = run_backtest(df, cfg, fund)
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
