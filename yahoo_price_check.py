"""Minimal Yahoo Finance diagnostic script.

Usage:
  python yahoo_price_check.py NVDA QQQ AAPL SPY
"""

import math
import sys

import yfinance as yf


def check_ticker(ticker: str) -> None:
    print(f"\n=== {ticker} ===")

    stock = yf.Ticker(ticker)

    try:
        fast_info = stock.fast_info
        last_price = getattr(fast_info, "last_price", None)
        print(f"fast_info.last_price={last_price}")
    except Exception as exc:
        print(f"fast_info error: {type(exc).__name__}: {exc}")

    try:
        hist_1m = stock.history(period="1d", interval="1m", auto_adjust=True)
        print(f"1m rows={len(hist_1m)} empty={hist_1m.empty}")
        if not hist_1m.empty:
            last_close = hist_1m["Close"].iloc[-1]
            print(f"latest 1m close={last_close}")
    except Exception as exc:
        print(f"1m history error: {type(exc).__name__}: {exc}")

    try:
        hist_1h = stock.history(period="7d", interval="1h", auto_adjust=True)
        print(f"1h rows={len(hist_1h)} empty={hist_1h.empty}")
        if not hist_1h.empty:
            bad_mask = hist_1h[["Open", "High", "Low", "Close", "Volume"]].isna().any(axis=1)
            bad_rows = hist_1h.loc[bad_mask, ["Open", "High", "Low", "Close", "Volume"]]
            print(f"1h bad_rows={len(bad_rows)}")
            if len(bad_rows):
                print(bad_rows.tail(5).to_string())

            last = hist_1h[["Open", "High", "Low", "Close", "Volume"]].tail(3)
            print("last 1h rows:")
            print(last.to_string())
    except Exception as exc:
        print(f"1h history error: {type(exc).__name__}: {exc}")


def main() -> int:
    tickers = sys.argv[1:] or ["NVDA", "QQQ", "AAPL", "SPY"]
    for ticker in tickers:
        check_ticker(ticker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())