"""Generates 4 moderately large JSON files (well under
MAX_STRUCTURED_RECORDS, but substantial) to upload concurrently -- tests
whether the concurrency fixes (global file-parallelism semaphore,
per-backend LLM throttling) hold up under real multi-file load, not just
the small 2-6 file test batches used earlier this session.
"""
import json
import os
import tempfile

N = 25_000  # per file -- 4 files = 100,000 records total in flight

for file_idx in range(4):
    records = [
        {
            "order_id": f"ORD-{file_idx}-{i:06d}",
            "customer": f"Customer {i}",
            "amount": round(10 + (i % 900) * 1.11, 2),
            "currency": "AED",
            "paid": i % 3 != 0,
        }
        for i in range(N)
    ]
    payload = {"orders": records}
    out_path = os.path.join(tempfile.gettempdir(), f"concurrent_stress_{file_idx}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    print(f"file {file_idx}: {N} records, {os.path.getsize(out_path) / 1024 / 1024:.1f} MB -> {out_path}")
