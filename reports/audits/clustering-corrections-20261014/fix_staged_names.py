import json
from pathlib import Path
import numpy as np
from psycopg.types.json import Jsonb
from hylogger.explorer import analysis
from hylogger.explorer.db import connect,DEFAULT_URL,read_frame
root=Path(__file__).resolve().parent
result=json.loads((root/"regroup_result.json").read_text())
records=[]
with connect(DEFAULT_URL) as c:
    for report in result["reports"]:
        new,old=report["run_id"],report["source_run"]
        card=c.execute("SELECT card FROM analysis_run WHERE run_id=%s AND status='staged'",(new,)).fetchone()[0]
        original=c.execute("SELECT card FROM analysis_run WHERE run_id=%s",(old,)).fetchone()[0]
        before=card["hole_clustering"].get("names")
        if card["hole_clustering"].get("descriptive_container"):
            names={"0":"Unassigned — no supported hole grouping"};equivalent=False
        else:
            rows=read_frame(c,"SELECT n.hole_id,n.cluster_id AS new,o.cluster_id AS old FROM hole_profile n JOIN hole_profile o USING(hole_id) WHERE n.run_id=%s AND o.run_id=%s",(new,old))
            mapping={int(g):set(rows.old[rows.new==g]) for g in rows.new.unique()}
            equivalent=all(len(v)==1 for v in mapping.values()) and len({next(iter(v)) for v in mapping.values()})==len(mapping)
            if not equivalent:raise RuntimeError("Unexpected changed supported grouping")
            names={str(g):original["hole_clustering"]["names"][str(int(next(iter(v))))] for g,v in mapping.items()}
        card["hole_clustering"]["names"]=names
        card["regrouping"]["naming"]="Original full-feature names preserved through verified equivalent partition; unsupported container is unassigned"
        c.execute("UPDATE analysis_run SET card=%s WHERE run_id=%s",(Jsonb(card),new))
        report["grouping"]["names"]=names
        records.append({"run_id":new,"source_run":old,"supported_partition_equivalent":equivalent,"before":before,"after":names})
    rows=c.execute("SELECT run_id,kind,sensor,status,card,product_path,created_at FROM analysis_run WHERE run_id=ANY(%s) ORDER BY sensor",(result["runs"],)).fetchall()
    (root/"regrouped_runs.json").write_text(json.dumps(rows,default=analysis._default,indent=2))
    (root/"regroup_result.json").write_text(json.dumps(result,default=analysis._default,indent=2))
    (root/"staged_names_fix.json").write_text(json.dumps(records,default=analysis._default,indent=2))
print(json.dumps(records,default=analysis._default,indent=2))
