from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

from hylogger.jobs import worker


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Connection:
    def execute(self, sql, params=None):
        return Rows([('A',100)] if 'raw_size_bytes' in sql else [])

    def commit(self):
        pass


def test_resume_after_cleanup_before_database_commit(tmp_path, monkeypatch):
    output = tmp_path / 'products/raw/A'
    output.mkdir(parents=True)
    pd.DataFrame({'bin':[0]}).to_parquet(output / 'raw_nir.parquet')
    np.savez(output / 'raw_nir.npz',spectra=[[1,2]],wavelengths=[450,460],bins=[0])
    saved = {'validated':True,'expected_sensors':['nir'],'discard_requested':True,
             'sensors':{'nir':{'status':'summarised'}}}
    (output / 'summary.json').write_text(json.dumps(saved))
    conn = Connection()
    ctx = SimpleNamespace(conn=conn,data_dir=tmp_path,params={'holes':['A']})
    monkeypatch.setattr(worker,'per_hole',lambda ctx, holes, parallel, work:work('A',conn))
    registered = []
    statuses = []
    monkeypatch.setattr(worker,'_register_raw',lambda *args:registered.append(args[-1]))
    monkeypatch.setattr(worker,'set_status',lambda *args:statuses.append(args[-1]))
    def no_network(*args,**kwargs):
        raise AssertionError('Verified saved data must not be downloaded again')
    monkeypatch.setattr(worker,'run_step',no_network)
    assert worker.job_raw_fetch(ctx)['ok']
    assert registered == [saved]
    assert statuses == ['summarised_discarded']
