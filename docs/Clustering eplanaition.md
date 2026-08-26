
# Clusering


## TSG clustering versus raw-spectrum clustering

They answer different questions.

| Input              | What clustering would find                                                                |
| ------------------ | ----------------------------------------------------------------------------------------- |
| Processed TSG logs | Similarities in the mineral interpretations already produced by TSG                       |
| Raw spectra        | Similarities in the measured spectral shapes before TSG converts them into mineral labels |
### Processed TSG clustering

This is easier to start with because the data is compact and interpretable:

Min1, Min2, Min3
Grp1, Grp2, Grp3
Wt1, Wt2, Wt3
Error

But the clusters will partly reflect decisions already made by:

- the TSG algorithm;
- its mineral reference library;
- the geologist’s processing;
- the selected processing level.

Therefore, it is valuable as a **baseline**, but it is not independent of TSG.

## Raw spectral clustering

This contains more of the original measured information. It may reveal spectral patterns that were simplified, missed or grouped together by TSG.

However, it is also much more demanding because we must account for:

- noise and invalid spectra;
- wavelength grids;
- instrument generations;
- scaling and normalisation;
- gaps in core;
- different spectral regions;
- potentially hundreds of bands;
- acquisition artefacts that could become clusters.

>Raw spectra probably offer the greater exploratory and scientific value, but processed TSG results give us an essential, understandable baseline.

## Should SWIR and TIR be clustered separately?

Initially, yes.

For processed TSG:

```
uTSAS  → SWIR interpretation
ujCLST → TIR interpretation
```

They describe different spectral regions and different mineral families. We should not treat `Wt1 uTSAS` and `Wt1 ujCLST` as though they were measurements of exactly the same thing.

**For raw spectra, separation is even more important:**
```
SWIR: one wavelength grid and spectral behaviour
TIR:  a different wavelength grid and spectral behaviour
```

**Our initial models should therefore be:**
```
SWIR processed model
TIR processed model
SWIR raw-spectra model
TIR raw-spectra model
```

## ## “Matching data” means more than matching region

Before putting observations into one clustering model, they should match in several ways.

**For raw spectra:**

- same spectral region;
- same wavelength count and wavelength positions;
- compatible instrument generation;
- same preprocessing and normalisation;
- comparable quality controls.

**For TSG:**

- same processing family, such as `uTSAS`;
- same processing level, such as `L2-user`;
- same available log structure;
- equivalent meaning for the selected variables;
- missing and invalid categories handled consistently.
## One critical decision: what are we clustering?

We have not decided what one row in the clustering table represents.

There are at least three possibilities:

1. **Individual spectral reading**
    
    One row represents one very small depth measurement. This gives detailed results but can be noisy and produces many highly correlated neighbouring observations.
    
2. **Depth interval**
    
    One row represents perhaps 10 cm, 50 cm or 1 m of core. Spectra or mineral proportions are summarised within each interval. This is often easier to interpret geologically.
    
3. **Whole drill hole**
    
    One row summarises an entire hole. This can compare holes regionally, but loses detailed downhole changes.
    

For our first investigation, I recommend working at the **depth-reading or small depth-interval level within one hole**. Once we understand that, we can decide how to summarise multiple holes.