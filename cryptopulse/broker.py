import json
import os
import time


def retry(fn, tries=3, delay=1.0):
    """Повтор при сетевых/биржевых сбоях. Последняя ошибка пробрасывается."""
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(delay * 2 ** i)


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
        return float(retry(lambda: self.ex.fetch_balance())["USDT"]["free"])

    def open(self, symbol, sig, qty):
        ccxt_sym = symbol.replace("USDT", "/USDT:USDT")
        self.ex.set_margin_mode("isolated", ccxt_sym)
        self.ex.set_leverage(sig.leverage, ccxt_sym)
        side, close = ("buy", "sell") if sig.side == "long" else ("sell", "buy")
        qty = float(self.ex.amount_to_precision(ccxt_sym, qty))
        retry(lambda: self.ex.create_order(ccxt_sym, "market", side, qty))
        try:   # позиция не должна остаться без стопа
            retry(lambda: self.ex.create_order(ccxt_sym, "STOP_MARKET", close, qty, None,
                                               {"stopPrice": sig.sl, "reduceOnly": True}))
            retry(lambda: self.ex.create_order(ccxt_sym, "TAKE_PROFIT_MARKET", close, qty, None,
                                               {"stopPrice": sig.tp, "reduceOnly": True}))
        except Exception:
            self.close_all(symbol)
            raise
        return qty

    def _sym(self, symbol):
        return symbol.replace("USDT", "/USDT:USDT")

    def position(self, symbol):
        """(side, qty) реальной позиции на бирже или None."""
        for p in retry(lambda: self.ex.fetch_positions([self._sym(symbol)])):
            c = float(p["contracts"] or 0)
            if c:
                return p["side"], abs(c)
        return None

    def has_protection(self, symbol):
        """Есть ли reduce-only стоп и тейк. Без них позиция опасна."""
        kinds = set()
        for o in retry(lambda: self.ex.fetch_open_orders(self._sym(symbol))):
            t = str(o.get("type", "")).upper()
            if "STOP" in t and "TAKE" not in t:
                kinds.add("sl")
            elif "TAKE_PROFIT" in t:
                kinds.add("tp")
        return kinds >= {"sl", "tp"}

    def protect(self, symbol, side, qty, sl, tp):
        close = "sell" if side == "long" else "buy"
        cs = self._sym(symbol)
        self.ex.cancel_all_orders(cs)
        self.ex.create_order(cs, "STOP_MARKET", close, qty, None, {"stopPrice": sl, "reduceOnly": True})
        self.ex.create_order(cs, "TAKE_PROFIT_MARKET", close, qty, None, {"stopPrice": tp, "reduceOnly": True})

    def close_all(self, symbol):
        """Рыночное закрытие позиции и отмена всех ордеров."""
        cs = self._sym(symbol)
        pos = self.position(symbol)
        self.ex.cancel_all_orders(cs)
        if pos:
            side, qty = pos
            self.ex.create_order(cs, "market", "sell" if side == "long" else "buy", qty, None, {"reduceOnly": True})

    def is_flat(self, symbol):
        ccxt_sym = symbol.replace("USDT", "/USDT:USDT")
        return all(abs(float(p["contracts"] or 0)) == 0 for p in self.ex.fetch_positions([ccxt_sym]))
