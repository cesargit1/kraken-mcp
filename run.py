"""
run.py — Start the trading bot + dashboard UI together.

Usage:
  python3 run.py           # paper mode (default)
  TRADING_MODE=live python3 run.py
"""

import os
import sys
import signal
import subprocess

procs = []


def shutdown(sig=None, frame=None):
    print("\n[run] Shutting down...")
    for p in procs:
        p.terminate()
    sys.exit(0)


signal.signal(signal.SIGINT,  shutdown)
signal.signal(signal.SIGTERM, shutdown)

env = os.environ.copy()

ui_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "ui_server:app", "--port", "8000"],
    env=env,
)
procs.append(ui_proc)
print("[run] Dashboard → http://localhost:8000")

bot_proc = subprocess.Popen(
    [sys.executable, "bot.py"],
    env=env,
)
procs.append(bot_proc)
print(f"[run] Bot started (mode={env.get('TRADING_MODE', 'paper')})")
print("[run] Press Ctrl+C to stop both.\n")

# Wait for either process to exit
while True:
    for p in procs:
        ret = p.poll()
        if ret is not None:
            print(f"[run] A process exited with code {ret}. Shutting down.")
            shutdown()
    import time
    time.sleep(1)
