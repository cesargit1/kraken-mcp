"""Bootstrap bot_settings table row in Supabase."""
from dotenv import load_dotenv
load_dotenv()
import db

try:
    result = db.update_settings(db._DEFAULT_SETTINGS)
    print("bot_settings row created/verified:")
    for k, v in result.items():
        print(f"  {k}: {v}")
except Exception as e:
    print(f"ERROR: {e}")
    print("\nCreate/update the table first by running this SQL in Supabase SQL Editor:\n")
    print("""
CREATE TABLE IF NOT EXISTS bot_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    paper_capital FLOAT NOT NULL DEFAULT 1000,
    max_position_pct FLOAT NOT NULL DEFAULT 20,
    max_position_usd FLOAT,
    max_leverage INTEGER NOT NULL DEFAULT 3,
    max_open_positions INTEGER NOT NULL DEFAULT 10,
    risk_per_trade_pct FLOAT NOT NULL DEFAULT 2,
    stop_loss_pct_default FLOAT NOT NULL DEFAULT 2.5,
    poll_interval_sec INTEGER NOT NULL DEFAULT 300,
    ai_timer_min INTEGER NOT NULL DEFAULT 60,
    cooldown_min INTEGER NOT NULL DEFAULT 30,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT single_row CHECK (id = 1)
);
INSERT INTO bot_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

-- If table already exists, add the new columns:
ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS poll_interval_sec INTEGER NOT NULL DEFAULT 300;
ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS ai_timer_min INTEGER NOT NULL DEFAULT 60;
ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS cooldown_min INTEGER NOT NULL DEFAULT 30;
""")
