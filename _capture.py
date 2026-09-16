"""Throwaway: dump real result payloads for the three agents being redesigned."""
import json
from backend.agents import REGISTRY
out = {}
for aid, inputs in (("dashboard_insights", {}),
                    ("workflow_optimizer", {}),
                    ("document_collection", {"case_id": "case-001"})):
    try:
        out[aid] = REGISTRY[aid].run(inputs)
        print(f"{aid}: ok")
    except Exception as e:
        out[aid] = {"EXC": f"{type(e).__name__}: {e}"}
        print(f"{aid}: FAILED {e}")
json.dump(out, open("_payloads.json", "w", encoding="utf-8"), indent=1, default=str)
print("written")
