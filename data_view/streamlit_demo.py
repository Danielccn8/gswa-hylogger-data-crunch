"""A minimal Streamlit demo for the GSWA HyLogger project.

WHAT THIS SHOWS
    A map of every HyLogger hole in WA, filters in the sidebar, and a downhole
    mineral profile for the hole you pick.

HOW TO RUN
    streamlit run streamlit_demo.py

    That opens a browser at http://localhost:8501. Nothing else to install or
    configure. Ctrl-C in the terminal stops it.

THE ONE THING TO UNDERSTAND
    Streamlit re-runs this ENTIRE file, top to bottom, every single time you
    touch a widget. Move a slider -> whole script runs again.

    That is why it is so simple (no callbacks, no state machine, no front-end)
    and also why slow code is fatal. @st.cache_data is how you opt out: the
    decorated function runs once, and every re-run reuses the stored result.
"""
import math
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).parent
OUTPUTS = HERE / "outputs"

# set_page_config must be the first Streamlit call in the script.
st.set_page_config(page_title="GSWA HyLogger Explorer", page_icon="🪨", layout="wide")


# ----------------------------------------------------------------------------
# DATA LOADING
# ----------------------------------------------------------------------------
# @st.cache_data means: run this once, remember the answer. Without it, every
# click would re-read the file from disk. With a 3 MB CSV you would feel it;
# with the full spectra you would be waiting 30 seconds per click.
@st.cache_data
def load_holes():
    """Every HyLogger hole in WA. Built by build_hole_index.py."""
    path = OUTPUTS / "wa_holes.csv"
    if not path.exists():
        return None
    return pd.read_csv(path)


@st.cache_data
def load_hole_detail():
    """Per-reading data for whichever hole we have locally, if any."""
    for candidate in list(OUTPUTS.glob("*_samples.csv")) + list(HERE.glob("*_data.csv")):
        return pd.read_csv(candidate), candidate.name
    return None, None


holes = load_holes()

if holes is None:
    st.error("No hole index found. Run this first:  python build_hole_index.py")
    st.stop()   # st.stop() ends the script run right here


# ----------------------------------------------------------------------------
# SIDEBAR — the filters
# ----------------------------------------------------------------------------
# Every widget RETURNS its current value. There is no event handler: you just
# read the variable. On the next re-run it holds whatever the user picked.
st.sidebar.header("Filters")

# math.ceil, not int(): int(5029.7) is 5029, which would put the deepest hole
# in WA outside its own slider. Off-by-one bugs like that hide in plain sight.
longest = math.ceil(holes["length_m"].max())

min_len, max_len = st.sidebar.slider(
    "Hole length (m)", min_value=0, max_value=longest, value=(0, longest)
)

methods = st.sidebar.multiselect(
    "Drilling method",
    options=sorted(holes["drilling_method"].dropna().unique()),
    default=[],
)

search = st.sidebar.text_input("Operator contains", "")

# Some holes have no length recorded, and at least one has a NEGATIVE length
# (YGD4: -115,224 m — corrupt data in GSWA's own records). .between() is False
# for both NaN and negatives, so they would silently vanish from the map.
# Make the choice explicit rather than losing rows without saying so.
bad = holes["length_m"].isna() | (holes["length_m"] < 0)
keep_bad = st.sidebar.checkbox(
    f"Include {int(bad.sum())} holes with missing/invalid length", value=True
)

# Ordinary pandas filtering — nothing Streamlit-specific here.
in_range = holes["length_m"].between(min_len, max_len)
if keep_bad:
    in_range = in_range | bad
view = holes[in_range]
if methods:
    view = view[view["drilling_method"].isin(methods)]
if search:
    view = view[view["operator"].str.contains(search, case=False, na=False)]


# ----------------------------------------------------------------------------
# MAIN PAGE
# ----------------------------------------------------------------------------
st.title("GSWA HyLogger — hole explorer")
st.caption("Demo showing what Streamlit gives you for very little code.")

# st.columns lays widgets out side by side. st.metric renders a big number.
c1, c2, c3, c4 = st.columns(4)
c1.metric("Holes shown", f"{len(view):,}", delta=f"{len(view) - len(holes):,}")
# Sum only the plausible lengths. One corrupt value (-115,224 m) would otherwise
# drag this headline number down by 20% and nobody would notice.
valid_length = view.loc[view["length_m"] > 0, "length_m"]
c2.metric("Total metres", f"{valid_length.sum():,.0f}")
c3.metric("Operators", view["operator"].nunique())
c4.metric("Projects", view["project"].nunique())

# st.map wants columns literally called lat/lon. That is the whole API.
st.subheader("Where the holes are")
st.map(view[["lat", "lon"]], size=800)

st.subheader("The holes themselves")
st.dataframe(view, use_container_width=True, hide_index=True)

# st.download_button turns any bytes into a working download link.
st.download_button(
    "Download this selection as CSV",
    data=view.to_csv(index=False).encode(),
    file_name="holes_selection.csv",
    mime="text/csv",
)


# ----------------------------------------------------------------------------
# ONE HOLE IN DETAIL
# ----------------------------------------------------------------------------
st.divider()
st.subheader("A single hole")

chosen = st.selectbox("Pick a hole", options=view["hole_id"].tolist())
row = holes[holes["hole_id"] == chosen].iloc[0]

left, right = st.columns([1, 2])
with left:
    st.write(f"**{row['hole_id']}**")
    st.write(f"Operator: {row['operator']}")
    st.write(f"Project: {row['project']}")
    st.write(f"Length: {row['length_m']:.0f} m")
    st.write(f"Method: {row['drilling_method']}")
    st.write(f"Location: {row['lat']:.4f}, {row['lon']:.4f}")

detail, source_name = load_hole_detail()

with right:
    if detail is None:
        st.info(
            "No per-reading data downloaded yet. Run the notebook "
            "`00_look_at_one_hole.ipynb` to produce one, and it will appear here."
        )
    elif chosen not in set(detail["hole_id"].astype(str)):
        st.info(
            f"We have detailed data for **{detail['hole_id'].iloc[0]}** only "
            f"(from `{source_name}`). Pick that hole to see its mineral profile."
        )
    else:
        # Minerals per 5 m of depth -> the "strip log" a geologist recognises.
        d = detail.dropna(subset=["group_1"])
        d = d[~d["group_1"].isin(["INVALID", "NOTAROK"])]
        binned = pd.crosstab((d["depth_m"] // 5) * 5, d["group_1"], normalize="index")
        st.write("**Mineral groups by depth** (share of readings per 5 m)")
        st.bar_chart(binned, height=380)

st.divider()
st.caption(
    "Every number here was precomputed. This app reads CSV files and never calls "
    "the NVCL API — which is what keeps it instant, and what makes it safe to demo."
)
