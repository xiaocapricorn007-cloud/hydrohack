# HydroHack — quick run guide

WAVEZ 2026 ship hydrostatics & intact-stability solver.
This zip is a self-contained snapshot of the source tree. The
input workbook (`data/official.xlsx`) is bundled.

## 1. Install (once)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requires Python 3.10+. Dependencies: NumPy, pandas, openpyxl,
matplotlib, Streamlit, Plotly — that's it. No commercial naval-
architecture or pre-built hydrostatics library is used.

## 2. Streamlit GUI (recommended)

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Upload `data/official.xlsx`
(or use the bundled file), set KG and ρ, hit *Run*. Eight tabs:
Overview, Hydrostatics, Stability, Trim, Geometry, 3D model,
Methodology, Downloads.

## 3. CLI

```bash
python -m hydrohack.cli data/official.xlsx \
    --format official \
    --KG 20.5 \
    --rho 1.025 \
    --angles 0:90:1
```

Outputs upright hydrostatics, GZ table, KN cross-curves,
IS-Code criteria, and saves figures to `outputs/`.

## 4. Reproduce the submission package

```bash
PYTHONPATH=. python submission/build_submission.py    # output.xlsx + PNGs
PYTHONPATH=. python submission/build_report.py        # report.pdf
```

## 5. Headline results (for `data/official.xlsx`, KG = 0.55·D = 20.5 m)

| Quantity | Value |
|---|---|
| ∇ | 554,119 m³ |
| Δ | 567,972 t |
| CB (computed) | 0.7729   (input listed 0.78 → 0.7 % match) |
| GMt | 5.952 m |
| max GZ | 2.472 m  at φ = 31° |
| Vanishing angle | 77.5° |
| IS-Code A.749 | **PASS** (all 7 criteria) |

## 6. Validation

Synthetic wall-sided box (L = 100, B = 12, D = 8, T = 4):
**0 absolute error** on every upright quantity, ≤ 10⁻⁹ m on GZ.
On the WAVEZ ship, dGZ/dφ at φ = 0 reproduces GMt to 0.018 %.
