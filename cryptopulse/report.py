"""Глубокий разбор сделок: по сторонам, причинам входа, выходам; MAE/MFE."""
import pandas as pd


def _grp(df, key):
    g = df.groupby(key).agg(n=("pnl", "size"), win=("pnl", lambda s: (s > 0).mean() * 100),
                            pnl=("pnl", "sum"), avg_roe=("roe", lambda s: s.mean() * 100))
    return g.round(1)


def analyze(trades: pd.DataFrame) -> str:
    if trades.empty:
        return "Сделок нет."
    out = ["=== По стороне ===", _grp(trades, "side").to_string()]
    out += ["", "=== По причине выхода ===", _grp(trades, "exit_reason").to_string()]
    ex = trades.assign(reason=trades.reasons.str.split(",")).explode("reason")
    out += ["", "=== По факторам входа (какие сигналы реально работают) ===",
            _grp(ex, "reason").sort_values("pnl", ascending=False).to_string()]
    out += ["", "=== По силе сигнала (score) ===", _grp(trades.assign(score=trades.score.round()), "score").to_string()]
    win, loss = trades[trades.pnl > 0], trades[trades.pnl <= 0]
    out += ["", "=== MAE/MFE (в % от цены входа) ===",
            f"Прибыльные: MAE {win.mae.mean()*100:.2f}%  MFE {win.mfe.mean()*100:.2f}%" if len(win) else "Прибыльных нет",
            f"Убыточные : MAE {loss.mae.mean()*100:.2f}%  MFE {loss.mfe.mean()*100:.2f}%  "
            f"(если MFE убыточных велик — стоит подтягивать стоп/частично фиксировать)" if len(loss) else "Убыточных нет"]
    return "\n".join(out)
