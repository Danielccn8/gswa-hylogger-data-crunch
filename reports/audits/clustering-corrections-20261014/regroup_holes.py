from __future__ import annotations
import json,hashlib,shutil
from pathlib import Path
import numpy as np
from psycopg.types.json import Jsonb
from threadpoolctl import threadpool_limits
from hylogger.explorer import analysis,clusters,fingerprints
from hylogger.explorer.db import connect,DEFAULT_URL,resolve_product,copy_frame,read_frame
root=Path(__file__).resolve().parent
staged=json.loads((root/"staged_runs.json").read_text())
source_sha=hashlib.sha256(Path(fingerprints.__file__).read_bytes()).hexdigest()
created=[];reports=[];mapping={}

def mix(summary):
    direct=summary.get("top_minerals") or {f"class {k}":v for k,v in summary.get("classes",{}).items()}
    if direct: return direct
    total={}
    for value in summary.values():
        if isinstance(value,dict):
            for mineral,weight in mix(value).items(): total[mineral]=total.get(mineral,0)+weight
    return total

with connect(DEFAULT_URL) as conn,threadpool_limits(limits=4):
    for old,kind,sensor,_,card,stored,_ in staged:
        if kind!="hole":continue
        source=resolve_product(stored,Path("C:/HyLoggerData"))
        archive=np.load(source/"distance.npz")
        names=archive["names"].tolist();distance=archive["distance"]
        labels,group_card,leaves=fingerprints.cluster_holes(distance,analysis.SEED)
        profiles=read_frame(conn,"SELECT hole_id,cluster_id AS source_cluster_id,x,y,anomaly_score,anomaly_reasons,summary FROM hole_profile WHERE run_id=%s ORDER BY hole_id",(old,)).set_index("hole_id").loc[names].reset_index()
        group_card["sizes"]={int(c):int((labels==c).sum()) for c in np.unique(labels)}
        if group_card["descriptive_container"]:
            group_card["names"]={0:"Unassigned — no supported hole grouping"}
        else:
            group_map={int(c):set(profiles.source_cluster_id.to_numpy()[labels==c]) for c in np.unique(labels)}
            if any(len(v)!=1 for v in group_map.values()) or len({next(iter(v)) for v in group_map.values()})!=len(group_map):
                raise RuntimeError("Supported partition changed: derive names from full saved features, never truncated summaries")
            group_card["names"]={c:card["hole_clustering"]["names"].get(str(next(iter(v))),card["hole_clustering"]["names"].get(int(next(iter(v))))) for c,v in group_map.items()}
        rid=analysis._run_id("hole",sensor);path=Path("C:/HyLoggerData/products/analysis")/rid
        shutil.copytree(source,path)
        np.savez_compressed(path/"distance.npz",names=np.array(names),distance=distance,leaves=leaves)
        source_npz_sha=hashlib.sha256((source/"distance.npz").read_bytes()).hexdigest()
        new_npz_sha=hashlib.sha256((path/"distance.npz").read_bytes()).hexdigest()
        card={**card,"hole_clustering":group_card,"regrouping":{
            "source_hole_run":old,"source_distance_npz_sha256":source_npz_sha,
            "regrouped_distance_npz_sha256":new_npz_sha,
            "source_distance_data_sha256":card.get("distance_data_sha256"),
            "grouping_source_sha256":source_sha,"grouping_version":fingerprints.HOLE_CLUSTER_VERSION,
            "inherited_distance_method":card["distance"],"distance_recomputed":False,
            "embedding_neighbors_anomaly_preserved":True,
            "meaning":"Only hole grouping was reevaluated with a minimum two-hole replication floor; this is not a calibrated accuracy threshold"}}
        profiles.insert(0,"run_id",rid);profiles["cluster_id"]=labels
        analysis._store_run(conn,rid,"hole",sensor,card,path)
        copy_frame(conn,"hole_profile",profiles[["run_id","hole_id","cluster_id","x","y","anomaly_score","anomaly_reasons","summary"]],json_columns=("summary",),array_columns=("anomaly_reasons",))
        conn.execute("INSERT INTO hole_neighbor(run_id,hole_id,rank,neighbor_id,distance) SELECT %s,hole_id,rank,neighbor_id,distance FROM hole_neighbor WHERE run_id=%s",(rid,old))
        mapping[old]=rid;created.append(rid)
        reports.append({"source_run":old,"run_id":rid,"sensor":sensor,"holes":len(names),"grouping":group_card,"distance_unchanged":bool(np.array_equal(distance,np.load(path/"distance.npz")["distance"])),"source_distance_npz_sha256":source_npz_sha})
        print(sensor,rid,group_card["k"],group_card["status"],group_card["sizes"],flush=True)
    for rid in created:
        card=conn.execute("SELECT card FROM analysis_run WHERE run_id=%s",(rid,)).fetchone()[0]
        if "views" in card:card["views"]={s:mapping.get(v,v) for s,v in card["views"].items()}
        conn.execute("UPDATE analysis_run SET card=%s WHERE run_id=%s",(Jsonb(card),rid))
    rows=conn.execute("SELECT run_id,kind,sensor,status,card,product_path,created_at FROM analysis_run WHERE run_id=ANY(%s) ORDER BY sensor",(created,)).fetchall()
    (root/"regrouped_runs.json").write_text(json.dumps(rows,default=analysis._default,indent=2))
    (root/"regroup_result.json").write_text(json.dumps({"runs":created,"source_mapping":mapping,"reports":reports,"promoted":False,"grouping_source_unchanged":source_sha==hashlib.sha256(Path(fingerprints.__file__).read_bytes()).hexdigest()},default=analysis._default,indent=2))
