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

# Use the venv python if we're not already inside it
_venv_python = os.path.join(os.path.dirname(__file__), ".venv", "bin", "python3")
if os.path.exists(_venv_python) and sys.executable != os.path.realpath(_venv_python):
    os.execv(_venv_python, [_venv_python] + sys.argv)

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
print("[run] Bot loop started inside the UI server process.")
print("[run] Press Ctrl+C to stop.\n")

# Wait for either process to exit
while True:
    for p in procs:
        ret = p.poll()
        if ret is not None:
            print(f"[run] A process exited with code {ret}. Shutting down.")
            shutdown()
    import time
    time.sleep(1)
