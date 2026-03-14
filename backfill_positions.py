"""Backfill open positions for trades that executed but weren't recorded."""
from dotenv import load_dotenv
load_dotenv()
import db

# Trades #18 (SPYx), #29 (QQQx), #32 (AAPLx) — sim=True but no position row created
# because the SSE stream was missing db.open_position() calls.
ORPHANED_TRADE_IDS = [18, 29, 32]

client = db.get_client()
rows = client.table("trade_log").select("*").in_("id", ORPHANED_TRADE_IDS).execute().data

for t in rows:
    ticker = t["ticker"]
    er     = t.get("execution_result") or {}
    volume = er.get("volume")
    price  = er.get("price") or t.get("entry_price")
    lev    = t.get("leverage") or 1

    if not volume or not price:
        print(f"  SKIP #{t['id']} {ticker} — missing volume/price")
        continue

    # Don't double-insert if position already exists
    existing = db.get_open_position(ticker)
    if existing:
        print(f"  SKIP {ticker} — already has open position: {existing}")
        continue

    db.open_position(
        ticker=ticker,
        side="long" if t["action"] == "buy" else "short",
        volume=volume,
        entry_price=price,
        stop_loss=t.get("stop_loss") or None,
        leverage=lev,
        trade_log_id=t["id"],
    )
    print(f"  backfilled {ticker}: short {volume} @ ${price} lev={lev}x (trade #{t['id']})")

print("Done.")
