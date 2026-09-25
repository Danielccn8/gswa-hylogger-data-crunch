# Notebooks

Notebooks are organised by workflow stage: `00_getting_started`, `10_quality_control`, `20_feature_engineering`, `30_clustering`, `40_validation`, `team`, and `legacy`.

Notebooks must use `AppConfig.load()` and must not contain personal absolute paths. Reusable acquisition and transformation logic belongs in `src/hylogger/`.


### PCA + Hole-Level Spectral Clustering (Raw_data_PCA.ipynb)

1. **SWIR and TIR spectral data are processed separately** to preserve the characteristics of each sensor.

2. **Every individual spectral observation is retained**; no spectra are averaged, median-combined, or aggregated by depth.

3. **No StandardScaler or row-wise normalization is applied**, so the original spectral magnitudes are preserved. PCA performs only its normal internal centering.

4. **PCA is applied to the individual spectra**, reducing the number of spectral variables while retaining **99% of the total variance**.

5. **Each original spectrum receives its own PCA scores**, while `hole_id`, depth, and other metadata remain attached to that observation.

6. **All retained principal components are used for hole comparison**, rather than arbitrarily selecting only a small number of PCs.

7. **Holes are compared using the distributions of their individual PCA observations**. No single mean or representative spectrum is created for a hole.

8. **Wasserstein distance is calculated between holes** for each PCA component, and the component-wise distances are combined into a hole-to-hole spectral distance matrix.

9. **Agglomerative clustering is applied to the hole-to-hole distance matrix**, meaning the final clusters represent groups of holes with similar spectral distributions.

10. **Cluster quality is evaluated using silhouette scores across different numbers of clusters**, while PCA plots and distance-matrix heatmaps are generated to help inspect the spectral structure and clustering results.
