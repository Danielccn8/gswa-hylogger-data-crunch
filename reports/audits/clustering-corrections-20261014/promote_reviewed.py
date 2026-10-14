import json
from datetime import datetime,UTC
from pathlib import Path
from hylogger.explorer.db import connect,DEFAULT_URL
root=Path(__file__).resolve().parent
manifest=json.loads((root/"promotion_manifest.json").read_text())
new=[r["run_id"] for r in manifest["new_runs"]]
prior=[r["run_id"] for r in manifest["prior_active"]]
with connect(DEFAULT_URL) as c:
    current=[r[0] for r in c.execute("SELECT run_id FROM analysis_run WHERE status='active' FOR UPDATE").fetchall()]
    assert sorted(current)==sorted(prior),"Active runs changed since reviewed snapshot"
    assert c.execute("SELECT count(*) FROM analysis_run WHERE run_id=ANY(%s) AND status='staged'",(new,)).fetchone()[0]==15
    assert not c.execute("SELECT job_id FROM job WHERE status IN ('running','queued') AND lane='analysis'").fetchall(),"Competing analysis jobs"
    c.execute("UPDATE analysis_run SET status='archived' WHERE run_id=ANY(%s)",(prior,))
    c.execute("UPDATE analysis_run SET status='active' WHERE run_id=ANY(%s)",(new,))
    assert sorted(r[0] for r in c.execute("SELECT run_id FROM analysis_run WHERE status='active'").fetchall())==sorted(new)
receipt={"completed_at":datetime.now(UTC).isoformat(),"local_database":"127.0.0.1:5434/hylogger","active_ids":new,"archived_previous_ids":prior,"atomic":True,"promotion_authority":"Parent review after user-authorized local correction","rollback_manifest":"promotion_manifest.json"}
(root/"promotion_receipt.json").write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt,indent=2))
