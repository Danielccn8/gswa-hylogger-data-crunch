# Clustering notebooks

For the processed TSG baseline, run `hylogger prepare --selection tsg_pilot --source processed`
and open `01_baseline_clustering.ipynb`. See [the first-test guide](../../docs/TSG_BASELINE_FIRST_TEST.md).

- `01_baseline_clustering.ipynb`: one row per hole, using proportions from one shared
  processed log (`Grp1 uTSAS` by default). StandardScaler + optional PCA + K-Means.
- `03_improved_cluster.ipynb`: a separate raw-spectral workflow; it is not the processed baseline.

Every run must declare its selection, source, feature log, preprocessing, algorithm
parameters, and random seed. Use the project kernel, **HyLogger Data Crunch (Python 3.11)**.
