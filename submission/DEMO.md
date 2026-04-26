# HydroHack — Streamlit GUI demo

WAVEZ 2026 ship hydrostatics & intact-stability solver.

## ➤ Live demo (no install)

**https://hydrohack-wavez2026.streamlit.app/**

The app is deployed on Streamlit Community Cloud and pre-loaded with
the official WAVEZ 2026 input. Open the link, tick **"Use bundled
WAVEZ 2026 official input"** in the sidebar (it's on by default), and
all eight tabs populate with the real numbers immediately.

GitHub source: https://github.com/xiaocapricorn007-cloud/hydrohack

## Local launch (this zip — 3 commands)

### Linux / macOS

```bash
bash run_demo.sh
```

### Windows

```bat
run_demo.bat
```

### Manual (any OS)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The GUI opens automatically at **http://localhost:8501**.
Python 3.10+ required.

## What you'll see

Eight tabs, all driven by the bundled `data/official.xlsx`
(WAVEZ 2026 official VLOC particulars + 23×11 offset table):

1. **Overview** — main particulars, headline numbers, IS-Code pass/fail
2. **Hydrostatics** — ∇, Aw, LCB, LCF, KB, BMt/BMl, KMt/KMl, GMt, CB,
   CWP, CM, Cp, TPC, MCT, Sw at the design draft + draft sweep
3. **Stability** — GZ static-stability curve (0–90° heel) with annotations,
   GMt slope reference, IS-Code A.749 zones, criteria table
4. **Trim** — nested-bisection trim solver: pick LCG and Δ, get θ, t,
   T_aft, T_fwd, balanced LCB
5. **Geometry** — body plan, half-breadth (waterline) plan,
   profile/sheer with buttocks, sectional-area curve, Bonjean
6. **3D model** — Plotly interactive 3D hull (pan / rotate / zoom),
   lines mode + meshed hull with optional bottom/top closures
   and two shading modes (depth tealrose / opaque grey)
7. **Methodology** — explanation of the polygon-clipping, Simpson
   non-uniform parabolic-fit, heeled-equilibrium bisection, and
   trim-balance algorithms
8. **Downloads** — export the same `output.xlsx` and PNGs that are
   in the submission

## Default inputs

The app pre-fills the Schneekluth empirical KG estimate
**KG = 0.55·D = 20.5 m** and ρ = 1.025 t/m³ (sea water).
Both are user-editable from the sidebar — every number recomputes
on the fly.

## CLI alternative

If a GUI isn't available:

```bash
python -m hydrohack.cli data/official.xlsx \
    --format official --KG 20.5 --rho 1.025 \
    --angles 0:90:1
```

## Headline result (`data/official.xlsx`, KG = 20.5 m)

| Quantity | Value |
|---|---|
| ∇ | 554,119 m³ |
| Δ | 567,972 t |
| CB | 0.7729   (input listed 0.78 → 0.7 % match) |
| GMt | 5.952 m |
| max GZ | 2.472 m  at φ = 31° |
| Vanishing angle | 77.5° |
| **IS-Code A.749** | **PASS** (all 7 criteria) |
