import json,hashlib
from pathlib import Path
from hylogger.explorer import analysis
from hylogger.explorer.db import connect,DEFAULT_URL
root=Path(__file__).resolve().parent
main=json.loads((root/"staged_runs.json").read_text())
holes=json.loads((root/"regrouped_runs.json").read_text())
robust=json.loads((root/"staged_robustness.json").read_text())
rows=[r for r in main if r[1]!="hole"]+holes+robust
ids=[r[0] for r in rows]
expected={("interval",s) for s in analysis.SENSORS}|{("prediction",s) for s in analysis.SENSORS}|{("robustness",s) for s in analysis.SENSORS}|{("virtual_assay","combined")}|{("hole",s) for s in ["combined","swir","tir","seq_combined","seq_swir","seq_tir","raw_nir","raw_tir"]}
assert len(rows)==15 and {(r[1],r[2]) for r in rows}==expected
prior=json.loads((root/"before_active.json").read_text())
with connect(DEFAULT_URL) as c:
    current=c.execute("SELECT run_id,kind,sensor,status FROM analysis_run WHERE status='active' ORDER BY kind,sensor").fetchall()
    assert sorted(r[0] for r in current)==sorted(r[0] for r in prior)
    assert c.execute("SELECT count(*) FROM analysis_run WHERE run_id=ANY(%s) AND status='staged'",(ids,)).fetchone()[0]==15
manifest={"local_database":"127.0.0.1:5434/hylogger","status":"READY; requires parent final go","new_runs":[{"run_id":r[0],"kind":r[1],"sensor":r[2]} for r in rows],"prior_active":[{"run_id":r[0],"kind":r[1],"sensor":r[2]} for r in prior],"guard":"initial prior active IDs exactly unchanged;15new statusesstaged; everyexpectedkind/sensor appears once","rollback":"Atomically restore prior_active toactive and new_runs tostaged; preserve allruns/products"}
(root/"promotion_manifest.json").write_text(json.dumps(manifest,indent=2))
print(json.dumps({"ready":True,"new_runs":len(ids),"prior_active":len(prior)},indent=2))
