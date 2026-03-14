"""Reset script: close all open positions and wipe trade_log + positions tables."""
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timezone
import db

client = db.get_client()
now = datetime.now(timezone.utc).isoformat()

# Close any still-open positions at entry price (zero P&L — paper reset)
open_pos = db.get_all_open_positions()
print(f"Closing {len(open_pos)} open positions...")
for p in open_pos:
    print(f"  {p['ticker']} {p['side']} vol={p['volume']} entry=${p['entry_price']}")
    client.table("positions").update({
        "closed_at":    now,
        "close_price":  p["entry_price"],
        "pnl":          0,
        "close_reason": "manual",
    }).eq("ticker", p["ticker"]).is_("closed_at", "null").execute()

# Delete ALL positions rows first (FK dependency on trade_log)
print("Deleting all position rows...")
resp = client.table("positions").select("id").execute()
all_ids = [r["id"] for r in (resp.data or [])]
print(f"  Found {len(all_ids)} position rows to delete.")
if all_ids:
    client.table("positions").delete().in_("id", all_ids).execute()

# Now safe to wipe trade_log
print("Clearing trade_log...")
client.table("trade_log").delete().neq("id", 0).execute()

print("Done — DB is clean. Set PAPER_CAPITAL=1000 in .env before restarting the bot.")
