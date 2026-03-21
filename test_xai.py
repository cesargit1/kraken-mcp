"""Quick diagnostic: test XAI_API_KEY and X search streaming."""
from dotenv import load_dotenv; load_dotenv()
import os, sys

key = os.getenv("XAI_API_KEY", "")
print(f"XAI_API_KEY length: {len(key)}", flush=True)
print(f"XAI_API_KEY set: {bool(key)}", flush=True)

if not key:
    print("ERROR: XAI_API_KEY is not set in .env!", flush=True)
    sys.exit(1)

print(f"Key starts with: {key[:8]}...", flush=True)

from openai import OpenAI
client = OpenAI(api_key=key, base_url="https://api.x.ai/v1")

# Test 1: basic chat completion
print("\n--- Test 1: basic chat ---", flush=True)
try:
    r = client.chat.completions.create(
        model="grok-4-latest",
        messages=[{"role": "user", "content": "say hi"}],
        max_tokens=5,
    )
    print(f"OK: {r.choices[0].message.content}", flush=True)
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}", flush=True)

# Test 2: streaming responses with x_search
print("\n--- Test 2: x_search stream ---", flush=True)
try:
    with client.responses.stream(
        model="grok-4-latest",
        input=[{"role": "user", "content": "Search X for posts about Bitcoin cryptocurrency in the last hour."}],
        tools=[{"type": "x_search"}],
    ) as stream:
        count = 0
        for event in stream:
            t = getattr(event, "type", None)
            if t == "response.output_text.delta":
                count += 1
        print(f"OK: got {count} text deltas", flush=True)
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}", flush=True)

print("\nDone.", flush=True)
