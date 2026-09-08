"""Generates a large object-wrapped JSON test file to stress-test
_unwrap_record_array (roadmap item #3: large-scale JSON stress test).
Slightly over MAX_STRUCTURED_RECORDS (200,000) so the record-count-
exceeded warning path gets exercised for the object-wrapped shape
specifically -- previously only ever tested against the bare-array
streaming path.
"""
import json
import os
import tempfile

N = 200_050  # just over MAX_STRUCTURED_RECORDS (200,000)

departments = ["Finance", "Engineering", "Sales", "HR", "Marketing", "Operations"]
statuses = ["Active", "On Leave", "Terminated"]

records = [
    {
        "employee_id": f"EMP-{i:07d}",
        "full_name": f"Test Person {i}",
        "email": f"person{i}@example.test",
        "department": departments[i % len(departments)],
        "status": statuses[i % len(statuses)],
        "salary": round(3000 + (i % 5000) * 1.37, 2),
        "active": i % 2 == 0,
    }
    for i in range(N)
]

payload = {"employees": records}

out_path = os.path.join(tempfile.gettempdir(), "stress_test_large.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f)

print(f"Wrote {N} records, {os.path.getsize(out_path) / 1024 / 1024:.1f} MB -> {out_path}")
