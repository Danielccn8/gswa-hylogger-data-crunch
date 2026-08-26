
# How to understand the Data Crunch Project


## Where does the data comes from? 

1. Physical drill core 
2. HyLogger scans the core at different depths 
3. Raw reflectance spectra
4. TSG/TSA interprets those spectra
5. Processed mineral logs, weights, groups, and errors

**HyLogger is the physical scanning system. It shines light on the drill core and measures how the material reflects the light at many wavelengths.**

For every depth reading, we can therefore have hundreds of measurements. Together, those measurements from a spectrum. 

## Raw vs Processes data

**There are two principal sources:**
-  **Raw spectrum**: the instrument's measurement
- **Processed log:** TSG's interpretation of that measurement. 
*Basically, the raw spectrum is the photograph and the processed log is the label assigned to the photograph.*

## What did Julian said in the first Teams meeting? 

For data crunch, he mentioned: 
- Thousands of drill cores across hundreds of kilometres.
- Statistical analysis across the complete dataset.
- Clustering.
- Interactive data profiles.
- Workflows that help people make decisions from unfamiliar data.

*He discussed the probability of narrowing the analysis to something meaningfull, such as gold, lithium, a geographical region, or proximity to known deposits*.

**What Data Crunch means?**
The idea of the data crunch team is **to process large amount of data in one hit**.

So, our project should: 
- Compare or process many holes together.
- It should identify large-scale patterns, clusters, differences, or useful classifications. 
- It does not need to examine every individual spectrum manually.

**Where did Julian said the Data comes from:**

Julian showed several sources rather than one single dataset:

- NVCL ZIP files, with filenames based on drill-hole IDs.
- A catalogue connecting the available folders to GSWA database records.
- WAMEX for mineral-exploration records, searched using an `A` number.
- WAPIMS for petroleum wells.
- GeoVIEW for maps and regional spatial context.
- Geological logs, coordinates, surveys, assays, and other exploration information associated with drill holes.

### What Julian said about TSG and data differences

pyTSG is an open-source package for reading files produced for The Spectral Geologist software. 

Crucially, he explicitly warned that the archive contains data from four **4** different HyLogger machine versions. He said **we should expect differences between datasets because the capabilities changed between machine versions.**

He also mentioned different processing levels, as a representation of how much specialist had processed or addressed the data. **Suggested to speak with the people who operates HyLogger to understand these technical differences.**

### What he said about SWIR and TIR

> Each one is important for different uses.


He recommended:

- Showing where the major differences occur.
- Giving those differences back to a geologist for interpretation.
- Examining which spectral region is most useful for a particular geological feature.
- Continuing to investigate observed variance rather than prematurely choosing one sensor.

## Why are TSG datasets different

There are three major reasons: 
- `TSAV`: Visible /VNIR-related interpretations
- `TSAS`: SWIR-related interpretations.
- `jCLST:` Thermal/TIR-related interpretations. 

These regions are sensitive to different families. For example.
- `SWIR` is useful for: clays, micas, and chlorites
- `TIR` can identify quartz and feldspars.

### Different processing levels

Prefixes describe how much interpretation has occurred:
- s: System/L1, automatically generated.
- u: User/L2, reviewed or defined.
- d: Domain/L3, refined within geologist-defined domains.

Sources:
-  https://dme.nt.gov.au/media/docs/geoscience/hylogger/HyLogger-3-How-it-Works.pdf
- https://warsydprdstadasc.blob.core.windows.net/downloads/Hylogger/Summary_Reports/SR_RHDD034.pdf?ts=20240205075114


**What is NIR?**
Near-infrared

**What is VNIR?**
Visible and near-infrared

**What is TIT**?
Thermal infrared


### What does "matching band counts" mean?

Basically the "rule" that was used in the system. In between VNIR/SWIR bands and TIR bands

## What `Min1` actually means

`Min1 uTSAS` does **not** mean “the most abundant mineral in the whole hole.”

At every depth measurement, TSG produces ranked interpretations:

- `Min1`: the first-ranked or best-matching mineral at that depth.
- `Min2`: the second-ranked mineral.
- `Min3`: the third-ranked mineral.
- `Wt1`, `Wt2`, `Wt3`: estimated contributions associated with those matches.
- `Grp1`: the first-ranked broader mineral group at that depth.


Therefore using hole: `05GJD001`

`min1_u_tsas: Chlorite-FeMg (33.6%)`

means: 

>Of the depths where `Min1 uTSAS` has a result, Chlorite-FeMg is the most frequently returned first-ranked mineral, appearing in 33.6% of those results.

It does **not** mean that 33.6% of the physical rock is Chlorite-FeMg. TSG is producing a spectral interpretation, not a laboratory assay.

Likewise:

```
grp1_u_tsas: CHLORITE (40.6%)
```

means that `CHLORITE` is the most frequent first-ranked group among the non-empty `Grp1` results.



## Using `hylogger processed preview ######`

>`processed preview` shows the **menu of processed variables available for download**. It does not show the actual mineral observations yet.

One example row: 
`Min1 uTSAS    SWIR    L2-user    1`

This means:

- `Min1`: first-ranked mineral interpretation at each depth.
- `uTSAS`: the processing result that produced it.
    - `u` means user/geologist-refined processing.
    - `TSAS` is the SWIR mineral interpretation.
- `SWIR`: the spectral region used.
- `L2-user`: Level 2 processing, refined by a TSG operator/geologist.
- `Type 1`: a categorical result, such as `Chlorite-FeMg`, rather than a number.


### The table contains two families

Although the rows are displayed in a mixed order, they can be reorganised like this:

| Meaning                  | SWIR family   | TIR family     |
| ------------------------ | ------------- | -------------- |
| First mineral            | `Min1 uTSAS`  | `Min1 ujCLST`  |
| Second mineral           | `Min2 uTSAS`  | `Min2 ujCLST`  |
| Third mineral            | `Min3 uTSAS`  | `Min3 ujCLST`  |
| First group              | `Grp1 uTSAS`  | `Grp1 ujCLST`  |
| Second group             | `Grp2 uTSAS`  | `Grp2 ujCLST`  |
| Third group              | `Grp3 uTSAS`  | `Grp3 ujCLST`  |
| Weight of first mineral  | `Wt1 uTSAS`   | `Wt1 ujCLST`   |
| Weight of second mineral | `Wt2 uTSAS`   | `Wt2 ujCLST`   |
| Weight of third mineral  | `Wt3 uTSAS`   | `Wt3 ujCLST`   |
| Model error              | `Error uTSAS` | `Error ujCLST` |
**At one particular depth, the model might produce something conceptually like:**
Min1 = Chlorite-FeMg
Min2 = Muscovite
Min3 = Kaolinite

Wt1 = 0.65
Wt2 = 0.25
Wt3 = 0.10


That means Chlorite-FeMg was the model’s first component, Muscovite the second, and Kaolinite the third.

The weights are semi-quantitative spectral contributions. They should not automatically be interpreted as exact physical percentages of the rock.

**What does `Type` mean?**
- `Type 1`: categorical labels.
    - `Min1`, `Min2`, `Min3`
    - `Grp1`, `Grp2`, `Grp3`
- `Type 2`: numeric values.
    - `Wt1`, `Wt2`, `Wt3`
    - `Error`