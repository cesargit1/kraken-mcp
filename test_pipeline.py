"""Quick end-to-end test of the AI pipeline on one ticker."""
import asyncio
from dotenv import load_dotenv
load_dotenv()

import db
import indicators as ind
import bot


async def main():
    rows = db.get_watchlist()
    row = next(r for r in rows if r["ticker"] == "NVDAx")

    all_ind = ind.compute_all_timeframes(row)
    flags = ind.any_flags(all_ind)
    print(f"NVDAx flags: {flags}")

    await bot.process_ticker(row, flags or ["timer"], all_ind)
    print("\n=== Pipeline test complete ===")


asyncio.run(main())
