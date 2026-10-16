import json
import os


class Journal:
    def __init__(self, path="journal.jsonl"):
        self.path = path

    def write(self, **rec):
        with open(self.path, "a") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")


class CcxtBroker:
    """Реальная торговля на USDT-M фьючерсах через ccxt. ВНИМАНИЕ: не тестировалась на живой бирже —
    сначала прогоните на testnet (EXCHANGE_TESTNET=1) и маленькой суммой."""

    def __init__(self):
        import ccxt  # lazy: нужен только для live
        ex = getattr(ccxt, os.getenv("EXCHANGE", "binanceusdm"))(
            {"apiKey": os.environ["API_KEY"], "secret": os.environ["API_SECRET"],
             "options": {"defaultType": "future"}})
        if os.getenv("EXCHANGE_TESTNET") == "1":
            ex.set_sandbox_mode(True)
        self.ex = ex

    def balance(self):
        return float(self.ex.fetch_balance()["USDT"]["free"])

    def open(self, symbol, sig, qty):
        ccxt_sym = symbol.replace("USDT", "/USDT:USDT")
        self.ex.set_margin_mode("isolated", ccxt_sym)
        self.ex.set_leverage(sig.leverage, ccxt_sym)
        side, close = ("buy", "sell") if sig.side == "long" else ("sell", "buy")
        qty = float(self.ex.amount_to_precision(ccxt_sym, qty))
        self.ex.create_order(ccxt_sym, "market", side, qty)
        self.ex.create_order(ccxt_sym, "STOP_MARKET", close, qty, None, {"stopPrice": sig.sl, "reduceOnly": True})
        self.ex.create_order(ccxt_sym, "TAKE_PROFIT_MARKET", close, qty, None, {"stopPrice": sig.tp, "reduceOnly": True})
        return qty

    def is_flat(self, symbol):
        ccxt_sym = symbol.replace("USDT", "/USDT:USDT")
        return all(abs(float(p["contracts"] or 0)) == 0 for p in self.ex.fetch_positions([ccxt_sym]))
