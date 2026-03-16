"""
reset_paper.py — Wipe all trading state and reset to clean $1000 paper mode.
Run once: python3 reset_paper.py
"""
from dotenv import load_dotenv
load_dotenv()
import db

client = db.get_client()

print("=== Resetting paper trading database ===\n")

# 1. Delete all open positions
r = client.table("positions").delete().neq("id", 0).execute()
print(f"Deleted positions      : {len(r.data)} rows")

# 2. Delete all transaction ledger entries
r = client.table("transaction_ledger").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
print(f"Deleted transactions   : {len(r.data)} rows")

# 3. Delete all agent logs
r = client.table("agent_log").delete().neq("id", 0).execute()
print(f"Deleted agent_log      : {len(r.data)} rows")

# 3. Delete all signal states (resets last-AI-run timers and signal history)
r = client.table("signal_state").delete().neq("ticker", "").execute()
print(f"Deleted signal_state   : {len(r.data)} rows")

# 4. Delete all cooldowns
r = client.table("cooldown").delete().neq("ticker", "").execute()
print(f"Deleted cooldowns      : {len(r.data)} rows")

# 5. Reset settings: $1000 capital, conservative limits
# Apply base settings that are guaranteed to exist in the schema
base_settings = {
    "paper_capital":          1000.0,  # total paper capital
    "max_position_pct":       20.0,    # max 20% of capital per position ($200)
    "max_position_usd":       200.0,   # hard USD cap per trade
    "max_leverage":           2,       # max 2x leverage
    "max_open_positions":     5,       # max 5 concurrent positions
    "risk_per_trade_pct":     2.0,     # 2% of capital at risk per trade ($20)
    "stop_loss_pct_default":  2.5,     # fallback stop-loss %
    "trailing_stop_atr_mult": 2.0,     # trailing stop = N * ATR from high-water
}
base_settings["id"] = 1
client.table("bot_settings").upsert(base_settings, on_conflict="id").execute()
print("Base settings applied  : OK")

# Apply timing columns individually — these may not exist if bootstrap_settings
# was never run. Missing columns are skipped gracefully.
timing = {"poll_interval_sec": 300, "ai_timer_min": 60, "cooldown_min": 30}
for col, val in timing.items():
    try:
        client.table("bot_settings").update({col: val}).eq("id", 1).execute()
        print(f"  {col:<28} {val}")
    except Exception as e:
        print(f"  {col:<28} MISSING in DB — run bootstrap_settings.py to add it")

result = db.get_settings()
print("\nSettings applied:")
for k, v in result.items():
    print(f"  {k:<28} {v}")

# 6. Sanity check
pos = db.get_all_open_positions()
print(f"\nOpen positions remaining : {len(pos)}")
print("\n=== Reset complete. Bot is ready for a fresh $1000 paper run. ===")
