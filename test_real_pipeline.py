"""Quick test: Full multi-agent pipeline with REAL Gemini + REAL data."""
import sys
import os
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Deveops", "backend"))

from agents.multi_agent_pipeline import run_multi_agent

print("=" * 60)
print("  FULL MULTI-AGENT PIPELINE TEST")
print("  Gemini 3.8 Flash + Real seL4 Data")
print("=" * 60)
print()

t0 = time.time()
result = run_multi_agent("seL4__seL4", "Why was the seL4 microkernel verification approach chosen?")
elapsed = time.time() - t0

print()
print("=" * 60)
print("  RESULTS")
print("=" * 60)
print(f"Time:         {elapsed:.1f}s")
print(f"Condition:    {result['condition_label']}")
print(f"Evidence:     {len(result['evidence'])} pieces")
print(f"Retries:      {result['multi_agent']['retry_count']}")

v = result["multi_agent"].get("verification", {})
print(f"Sufficient:   {v.get('sufficient')}")
print(f"Confidence:   {v.get('confidence')}")

print()
print("=" * 60)
print("  ANSWER")
print("=" * 60)
print(result["answer"][:1000])

print()
print("=" * 60)
print("  AGENT LOGS")
print("=" * 60)
for log in result["multi_agent"]["agent_logs"]:
    detail = log["detail"][:100]
    print(f"  [{log['agent']}] {log['action']} - {detail}")
