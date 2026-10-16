"""SQLite: сделки и состояние бота переживают перезапуск."""
import json
import sqlite3
import threading

import pandas as pd


class Store:
    def __init__(self, path="cryptopulse.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            self.db.commit()

    def add_trade(self, trade: dict):
        with self.lock:
            self.db.execute("INSERT INTO trades(data) VALUES (?)", (json.dumps(trade, default=str),))
            self.db.commit()

    def trades(self) -> list:
        with self.lock:
            rows = self.db.execute("SELECT data FROM trades ORDER BY id").fetchall()
        out = []
        for (d,) in rows:
            t = json.loads(d)
            for k in ("open", "close"):
                if k in t:
                    t[k] = pd.Timestamp(t[k])
            out.append(t)
        return out

    def set_state(self, state: dict):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('bot', ?)", (json.dumps(state, default=str),))
            self.db.commit()

    def get_state(self) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT v FROM state WHERE k='bot'").fetchone()
        return json.loads(r[0]) if r else None
