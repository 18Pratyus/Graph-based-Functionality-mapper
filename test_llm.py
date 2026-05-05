"""
test_llm.py — Quick test to verify Ollama API connectivity and auth.
Run: python test_llm.py
"""
import httpx, json, sys

BASE_URL = "https://ollama.com"
API_KEY  = "305deef28fa54bd883e20b7116651d0e.aUNJ9b8duG8ww6uxpJDpmq-A"
MODEL    = "deepseek-v3.1:671b-cloud"

headers = {"Authorization": f"Bearer {API_KEY}"}
payload = {
    "model": MODEL,
    "prompt": 'Reply with exactly this JSON: {"status": "ok"}',
    "system": "You are a test assistant. Reply only with valid JSON.",
    "stream": False,
    "format": "json",
    "options": {"temperature": 0.0, "num_predict": 32},
}

print(f"  URL   : {BASE_URL}")
print(f"  Model : {MODEL}")
print(f"  Key   : {API_KEY[:20]}...")
print()

try:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{BASE_URL}/api/generate", json=payload, headers=headers)
    print(f"  Status: {r.status_code} {r.reason_phrase}")
    if r.status_code == 200:
        raw = r.json().get("response","")
        print(f"  Response: {raw[:200]}")
        print("\n  ✅ Ollama API is WORKING")
    elif r.status_code == 403:
        print(f"  Body: {r.text[:300]}")
        print("\n  ❌ 403 Forbidden — API key is INVALID or EXPIRED")
        print("     → Get a new key from ollama.com and update config.py")
    else:
        print(f"  Body: {r.text[:300]}")
        print(f"\n  ⚠️  Unexpected status {r.status_code}")
except Exception as e:
    print(f"  ❌ Connection error: {e}")
    sys.exit(1)
