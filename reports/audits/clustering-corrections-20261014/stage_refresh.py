from __future__ import annotations
import json
import os
import hashlib
import time
from pathlib import Path
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"
from threadpoolctl import threadpool_limits
import psutil
from hylogger.explorer import analysis
from hylogger.explorer.db import connect, DEFAULT_URL
ROOT = Path(__file__).resolve().parent
repo = ROOT.parents[2]
files = ["analysis.py", "clusters.py", "data.py", "features.py", "fingerprints.py", "rawcluster.py", "robustness.py"]
source = {n: hashlib.sha256((repo / "src/hylogger/explorer" / n).read_bytes()).hexdigest() for n in files}
(ROOT / "source_frozen.json").write_text(json.dumps(source, indent=2))
print("RESOURCE", json.dumps({"available_ram_gb": round(psutil.virtual_memory().available / 2**30, 2), "cpu_threads": 4}), flush=True)
with connect(DEFAULT_URL) as conn:
    queued = conn.execute("SELECT job_id,kind,status FROM job WHERE status IN ('running','queued')").fetchall()
    if queued:
        raise RuntimeError(f"Existing local jobs: {queued}")
    old = conn.execute("SELECT run_id,kind,sensor,status,card,product_path,created_at FROM analysis_run WHERE status='active' ORDER BY kind,sensor").fetchall()
    (ROOT / "before_active.json").write_text(json.dumps(old, default=analysis._default, indent=2))
    with threadpool_limits(limits=4):
        result = analysis.run_analysis(conn, Path("C:/HyLoggerData"), promote=False, raw=True, log=lambda message: print(message, flush=True))
    current = {n: hashlib.sha256((repo / "src/hylogger/explorer" / n).read_bytes()).hexdigest() for n in files}
    result["source_unchanged"] = current == source
    result["local_database"] = "127.0.0.1:5434/hylogger"
    rows = conn.execute("SELECT run_id,kind,sensor,status,card,product_path,created_at FROM analysis_run WHERE run_id=ANY(%s) ORDER BY kind,sensor", (result["runs"],)).fetchall()
    (ROOT / "staged_runs.json").write_text(json.dumps(rows, default=analysis._default, indent=2))
    (ROOT / "stage_result.json").write_text(json.dumps(result, default=analysis._default, indent=2))
    print("STAGED_RESULT",json.dumps(result, default=analysis._default),flush=True)
