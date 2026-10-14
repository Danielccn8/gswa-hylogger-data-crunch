import json
from pathlib import Path
from hylogger.explorer.db import connect,DEFAULT_URL
root=Path(__file__).resolve().parent
manifest=json.loads((root/"promotion_manifest.json").read_text())
new=[r["run_id"] for r in manifest["new_runs"]];prior=[r["run_id"] for r in manifest["prior_active"]]
with connect(DEFAULT_URL) as c:
    assert sorted(r[0] for r in c.execute("SELECT run_id FROM analysis_run WHERE status='active' FOR UPDATE").fetchall())==sorted(new)
    c.execute("UPDATE analysis_run SET status='staged' WHERE run_id=ANY(%s)",(new,))
    c.execute("UPDATE analysis_run SET status='active' WHERE run_id=ANY(%s)",(prior,))
print("Prior active runs restored atomically; every artifact preserved")
