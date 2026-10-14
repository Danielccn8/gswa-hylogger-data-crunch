from __future__ import annotations
import json,hashlib
from datetime import datetime,UTC
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr
from hylogger.explorer.db import connect,DEFAULT_URL,resolve_product
root=Path(__file__).resolve().parent
rows=json.loads((root/"staged_runs.json").read_text())
old=json.loads((root/"before_active.json").read_text())
oldmap={(r[1],r[2]):r for r in old}
summary={"staged_runs":len(rows),"promotion":"pending parent review","intervals":{},"holes":{},"raw_comparators":{}}
for r in rows:
    rid,kind,sensor,status,card,stored,_=r
    path=resolve_product(stored,Path("C:/HyLoggerData"))
    if kind=="interval":
        previous=oldmap[(kind,sensor)][4]
        sizes=card["clustering"]["training_cluster_sizes"]
        model=np.load(path/"model.npz")
        summary["intervals"][sensor]={"run_id":rid,"old_intervals":previous["intervals"],"new_intervals":card["intervals"],"old_fine_k":previous["clustering"]["k_fine"],"new_fine_k":card["clustering"]["k_fine"],"new_coarse_k":card["clustering"]["k_coarse"],"final_training_min_share":min(sizes)/sum(sizes),"model_cardinality":len(model["centres"]),"data_quality":card["data_quality"],"provenance":card["provenance"]}
    if kind=="hole":
        distance=np.load(path/"distance.npz")
        matrix=distance["distance"]
        summary["holes"][sensor]={"run_id":rid,"holes":len(distance["names"]),"distance":card["distance"],"distance_version":card.get("distance_version"),"shape":list(matrix.shape),"finite":bool(np.isfinite(matrix).all()),"symmetric":bool(np.allclose(matrix,matrix.T)),"zero_diagonal":bool(np.allclose(np.diag(matrix),0)),"hole_cluster_k":card["hole_clustering"]["k"],"hole_cluster_status":card["hole_clustering"].get("status"),"coverage":card.get("coverage"),"support":card.get("support"),"pca_components":card.get("pca_components"),"pca_variance":card.get("pca_variance"),"pca_variance_target_met":card.get("pca_variance_target_met"),"distance_data_sha256":card.get("distance_data_sha256")}
        if sensor.startswith("raw_"):
            legacy=np.load(path/"legacy_marginal_distance.npz")
            upper=np.triu_indices(len(matrix),1)
            rho=float(spearmanr(matrix[upper],legacy["distance"][upper]).statistic)
            summary["raw_comparators"][sensor]={"same_names":bool(np.array_equal(distance["names"],legacy["names"])),"spearman_rho":rho,"interpretation":"Same cohort/PCA; rank agreement does not validate geological accuracy or select a champion","legacy_distance_version":card["legacy_comparator"]["distance_version"]}
(root/"final_summary.json").write_text(json.dumps(summary,indent=2))
records=[]
with connect(DEFAULT_URL) as c:
    for hole,sensor,stored in c.execute("SELECT hole_id,sensor,product_path FROM raw_product ORDER BY sensor,hole_id"):
        product=resolve_product(stored,Path("C:/HyLoggerData"))
        for suffix in ("npz","parquet"):
            path=product/f"raw_{sensor}.{suffix}"
            if not path.exists():
                records.append({"hole_id":hole,"sensor":sensor,"path":str(path),"exists":False});continue
            h=hashlib.sha256()
            with path.open("rb") as stream:
                while chunk:=stream.read(1024*1024): h.update(chunk)
            stat=path.stat()
            records.append({"hole_id":hole,"sensor":sensor,"path":str(path),"exists":True,"bytes":stat.st_size,"modified_ns":stat.st_mtime_ns,"sha256":h.hexdigest()})
(root/"raw_sources_post.json").write_text(json.dumps({"captured_at":datetime.now(UTC).isoformat(),"timing":"post-run only; no before-run hashes, stability is not proved", "sources":records},indent=2))
print(json.dumps({"intervals":{s:{k:v[k] for k in ["old_intervals","new_intervals","old_fine_k","new_fine_k","new_coarse_k","final_training_min_share"]} for s,v in summary["intervals"].items()},"hole_counts":{s:v["holes"] for s,v in summary["holes"].items()},"raw_comparators":summary["raw_comparators"],"raw_input_files":len(records)},indent=2))
