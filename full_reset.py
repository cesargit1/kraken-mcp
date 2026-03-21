"""
full_reset.py — Wipe ALL data and re-seed the DB from scratch.
This drops all table contents and re-applies the watchlist seed.
Run once: python3 full_reset.py
"""
from dotenv import load_dotenv
load_dotenv()
import db

client = db.get_client()

print("=== Full DB reset ===\n")

# Order matters due to FK constraints: positions → agent_log → trades
tables_ordered = [
    "positions",
    "trades",
    "agent_log",
    "signal_state",
    "indicators",
    "candles",
    "watchlist",
]

for table in tables_ordered:
    try:
        r = client.table(table).delete().neq("id", -999999).execute()
        print(f"  Cleared {table:<20} ({len(r.data or [])} rows)")
    except Exception as e:
        # Some tables use non-integer PKs (e.g. signal_state uses ticker)
        try:
            if table == "signal_state":
                r = client.table(table).delete().neq("ticker", "").execute()
            else:
                raise
            print(f"  Cleared {table:<20} ({len(r.data or [])} rows)")
        except Exception as e2:
            print(f"  WARN {table}: {e2}")

print()

# Re-seed watchlist with Yahoo Finance tickers
seed = [
    {"ticker": "AAPL",    "asset_class": "stock",  "search_name": "Apple AAPL",    "active": True},
    {"ticker": "NVDA",    "asset_class": "stock",  "search_name": "NVIDIA NVDA",   "active": True},
    {"ticker": "TSLA",    "asset_class": "stock",  "search_name": "Tesla TSLA",    "active": True},
    {"ticker": "SPY",     "asset_class": "stock",  "search_name": "SPY ETF",       "active": True},
    {"ticker": "QQQ",     "asset_class": "stock",  "search_name": "QQQ ETF",       "active": True},
    {"ticker": "BTC-USD", "asset_class": "crypto", "search_name": "Bitcoin BTC",   "active": True},
    {"ticker": "ETH-USD", "asset_class": "crypto", "search_name": "Ethereum ETH",  "active": True},
]

print("Seeding watchlist...")
for row in seed:
    try:
        client.table("watchlist").insert(row).execute()
        print(f"  + {row['ticker']:<12} ({row['asset_class']})")
    except Exception as e:
        print(f"  WARN {row['ticker']}: {e}")

print()

# Reset bot_settings to fresh defaults
settings = {
    "id":                     1,
    "paper_capital":          1000.0,
    "max_position_pct":       20.0,
    "max_position_usd":       200.0,
    "max_leverage":           2,
    "max_open_positions":     5,
    "risk_per_trade_pct":     2.0,
    "stop_loss_pct_default":  2.5,
    "trailing_stop_atr_mult": 2.0,
    "poll_interval_sec":      300,
    "ai_timer_min":           60,
    "cooldown_min":           30,
}
client.table("bot_settings").upsert(settings, on_conflict="id").execute()
print("bot_settings reset to defaults.")

print("\n=== Reset complete. Fresh $1000 paper account ready. ===")
