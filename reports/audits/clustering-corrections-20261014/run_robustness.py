from __future__ import annotations
import os
os.environ["OMP_NUM_THREADS"]="4"
os.environ["OPENBLAS_NUM_THREADS"]="4"
import json,hashlib
from pathlib import Path
from threadpoolctl import threadpool_limits
from hylogger.explorer import analysis
from hylogger.explorer.db import connect,DEFAULT_URL
from hylogger.explorer.data import load_intervals
from hylogger.explorer.features import select_dataset
from datetime import datetime,UTC
import numpy as np

def checksum(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        while chunk:=f.read(1024*1024): h.update(chunk)
    return h.hexdigest()

def pilot_manifest(conn):
    records=[]
    for sensor in analysis.SENSORS:
        data=load_intervals(conn,sensor)
        candidates=sorted(data.meta.hole_id.unique())
        rng=np.random.default_rng(analysis.SEED)
        sample=sorted(rng.choice(candidates,min(24,len(candidates)),replace=False))
        for hole in sample:
            source=select_dataset(Path("C:/HyLoggerData/downloads/processed")/hole)
            qc=conn.execute("SELECT s.qc FROM feature_set s JOIN active_feature_set a USING(feature_set_id) WHERE s.hole_id=%s AND s.sensor=%s",(hole,sensor)).fetchone()
            actual=checksum(source) if source else None
            records.append({"sensor":sensor,"hole_id":hole,"dataset_id":source.parent.name if source else None,"source_path":str(source),"sha256":actual,"active_qc_sha256":qc[0].get("source_sha256") if qc else None,"matches_active_qc":bool(qc and actual==qc[0].get("source_sha256"))})
        del data
    return {"captured_at":datetime.now(UTC).isoformat(),"sample_holes_per_sensor":24,"seed":analysis.SEED,"sources":records}
root=Path(__file__).resolve().parent
staged=json.loads((root/"staged_runs.json").read_text())
interval_runs={r[2]:r[0] for r in staged if r[1]=="interval"}
validation_sha=hashlib.sha256(Path(analysis.__file__).with_name("robustness.py").read_bytes()).hexdigest()
(root/"validation_source.json").write_text(json.dumps({"robustness.py":validation_sha},indent=2))
with connect(DEFAULT_URL) as conn, threadpool_limits(limits=4):
    before=pilot_manifest(conn)
    (root/"width_sources_before.json").write_text(json.dumps(before,default=analysis._default,indent=2))
    result=analysis.run_robustness(conn,Path("C:/HyLoggerData"),sample_holes=24,promote=False,interval_runs=interval_runs,log=lambda m:print(m,flush=True))
    after=pilot_manifest(conn)
    (root/"width_sources_after.json").write_text(json.dumps(after,default=analysis._default,indent=2))
    result["width_sources_unchanged"]=before["sources"]==after["sources"]
    result["width_sources_match_active_qc"]=all(r["matches_active_qc"] for r in before["sources"])
    result["validation_source_sha256"]=validation_sha
    rows=conn.execute("SELECT run_id,kind,sensor,status,card,product_path,created_at FROM analysis_run WHERE run_id=ANY(%s)",(result["runs"],)).fetchall()
    (root/"staged_robustness.json").write_text(json.dumps(rows,default=analysis._default,indent=2))
    (root/"robustness_result.json").write_text(json.dumps(result,default=analysis._default,indent=2))
    print(json.dumps(result,default=analysis._default),flush=True)
