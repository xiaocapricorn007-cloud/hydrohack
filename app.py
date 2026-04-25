"""HydroHack — Streamlit GUI.

Run with:

    .venv/bin/streamlit run app.py

A single-page application that loads a ship-offsets workbook, computes
upright hydrostatics and large-angle stability (GZ / KN), evaluates the
result against IS-Code intact-stability criteria, and offers all
deliverables for download.
"""

from __future__ import annotations
import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from hydrohack.io_offsets import load_workbook, ShipInput
from hydrohack.io_official import load_official, looks_like_official
from hydrohack.geometry import Hull
from hydrohack.hydrostatics import compute_upright, compute_upright_sweep, sectional_areas, bonjean_table, trim_balance
from hydrohack.stability import gz_curve, cross_curves_kn, StabilityCurve
from hydrohack.criteria import evaluate_criteria, overall_pass, area_under_gz, vanishing_angle, angle_of_max_gz
from hydrohack import plotting
from hydrohack import plotting3d


# ---------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------
st.set_page_config(
    page_title="HydroHack — Ship Hydrostatics & Stability Solver",
    page_icon="⚓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# A small CSS pass to tighten spacing and add subtle card styling.
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1320px; }
      h1, h2, h3 { letter-spacing: -0.01em; }
      [data-testid="stMetric"] {
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(148,163,184,0.18);
          border-radius: 10px;
          padding: 0.9rem 1.0rem;
      }
      [data-testid="stMetricLabel"] { font-size: 0.85rem; color: #94A3B8; }
      [data-testid="stMetricValue"] { font-size: 1.6rem; }
      .hh-card {
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(148,163,184,0.18);
          border-radius: 10px;
          padding: 1rem 1.2rem;
          margin-bottom: 0.6rem;
      }
      .hh-card h4 { margin: 0 0 .4rem 0; color: #E2E8F0; font-size: 0.95rem;
                     text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; }
      .hh-row { display: flex; justify-content: space-between; padding: 4px 0;
                border-bottom: 1px dashed rgba(148,163,184,0.15); font-size: 0.92rem; }
      .hh-row:last-child { border-bottom: none; }
      .hh-key { color: #94A3B8; }
      .hh-val { color: #E2E8F0; font-variant-numeric: tabular-nums; }
      .hh-pill { display: inline-block; padding: 2px 10px; border-radius: 999px;
                 font-size: 0.75rem; font-weight: 700; letter-spacing: 0.05em; }
      .hh-pass { background: rgba(16,185,129,0.18); color: #10B981; }
      .hh-fail { background: rgba(239,68,68,0.18); color: #EF4444; }
      .hh-warn { background: rgba(245,158,11,0.18); color: #F59E0B; }
      .hh-banner { padding: 0.8rem 1.1rem; border-radius: 10px;
                   border: 1px solid rgba(148,163,184,0.18);
                   background: linear-gradient(135deg,
                     rgba(14,124,134,0.18) 0%,
                     rgba(15,23,42,0.0) 80%);
                   margin-bottom: 1.2rem; }
      .hh-banner h2 { margin: 0; font-size: 1.55rem; }
      .hh-banner p  { margin: 0.15rem 0 0; color: #94A3B8; font-size: 0.92rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------
# Caching wrappers
# ---------------------------------------------------------------------
# Bumping this constant invalidates the @st.cache_data wrapping
# _load_from_bytes — change it whenever the loader's behaviour changes
# in ways the cache should not silently retain.
_LOADER_CACHE_VERSION = 4   # +stern/stem (xyz + Table 4.4/4.5)


@st.cache_data(show_spinner=False)
def _load_from_bytes(file_bytes: bytes, kg_hint: float, rho_hint: float, _v: int = _LOADER_CACHE_VERSION) -> dict:
    """Detect the workbook layout and parse it.

    Two layouts are supported:
      * **Official WAVEZ 2026** ("Main Particulars" + "Offset data" sheets)
        — caller-supplied KG / ρ since the workbook omits them.
      * **Generic** ("Particulars" + "Offsets" sheets, key/value layout)
        — KG and ρ are read from the Particulars sheet.
    """
    tmp = Path("/tmp") / "_hydrohack_uploaded.xlsx"
    tmp.write_bytes(file_bytes)

    if looks_like_official(str(tmp)):
        ship = load_official(str(tmp), KG=kg_hint, rho=rho_hint)
        layout = "official"
    else:
        ship = load_workbook(str(tmp))
        layout = "generic"

    return {
        "stations":   ship.hull.stations,
        "waterlines": ship.hull.waterlines,
        "offsets":    ship.hull.offsets,
        "LBP": ship.LBP, "B": ship.B, "D": ship.D, "T": ship.T,
        "rho": ship.rho, "KG": ship.KG, "LCG": ship.LCG,
        "extra": ship.extra,
        "layout": layout,
    }


@st.cache_data(show_spinner=False)
def _compute_all(
    stations: np.ndarray, waterlines: np.ndarray, offsets: np.ndarray,
    LBP: float, B: float, T: float, rho: float, KG: float,
    angle_lo: float, angle_hi: float, angle_step: float,
    sweep_T_max_req: float, sweep_step: float,
):
    hull = Hull(stations=stations, waterlines=waterlines, offsets=offsets)
    up = compute_upright(hull, draft=T, rho=rho, KG=KG, LBP=LBP, B=B)
    angles = np.arange(angle_lo, angle_hi + 1e-9, angle_step)
    curve = gz_curve(hull, draft_upright=T, rho=rho, KG=KG, angles_deg=angles)
    A_x = sectional_areas(hull, T)

    # Hydrostatic-curves draft sweep — both bounds and step come from the UI.
    z_max = float(waterlines[-1])
    T_max = max(0.0, min(float(sweep_T_max_req), z_max))   # clamp at the top waterline
    drafts = np.arange(0.0, T_max + 1e-9, sweep_step)
    if drafts.size and drafts[-1] < T_max - 1e-9:
        drafts = np.append(drafts, T_max)
    # Always include the design draft if it falls inside the swept range,
    # so the table can highlight it on a clean row.
    if 0.0 <= T <= T_max and not np.any(np.isclose(drafts, T, atol=1e-6)):
        drafts = np.sort(np.append(drafts, T))
    sweep = compute_upright_sweep(hull, drafts, rho=rho, KG=KG, LBP=LBP, B=B)
    sweep_df = pd.DataFrame([s.as_dict() for s in sweep])

    # Bonjean curves — sectional area A(x_i, T) over the same draft grid.
    bj_drafts = drafts[drafts > 0]
    bj_areas = bonjean_table(hull, bj_drafts)

    # Cross-curves of stability KN(φ; Δ): pick ~6 evenly-spaced drafts so the
    # heeled-stability solver doesn't dominate runtime. Always include the
    # design draft so its KN trace lines up exactly with the Stability tab.
    cc_n = 6
    cc_drafts = np.linspace(max(0.4 * T, drafts[1] if drafts.size > 1 else 0.5 * T),
                            min(T_max, z_max), cc_n)
    if T not in cc_drafts:
        cc_drafts = np.sort(np.append(cc_drafts, T))
    cc_data = cross_curves_kn(hull, cc_drafts, rho=rho, angles_deg=angles)

    return {"upright": up, "curve": curve, "section_areas": A_x,
            "sweep": sweep_df, "T_design": T,
            "sweep_T_max_requested": float(sweep_T_max_req),
            "sweep_T_max_actual": T_max,
            "sweep_step": float(sweep_step),
            "bonjean_drafts": bj_drafts,
            "bonjean_areas":  bj_areas,
            "cross_curves":   cc_data}


def _fmt(v: float, prec: int = 4) -> str:
    """Pretty-print a number with thousands separators."""
    if abs(v) >= 1e4 or abs(v) < 1e-3 and v != 0:
        return f"{v:,.{prec}g}"
    return f"{v:,.{prec}f}"


# ---------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------
st.markdown(
    """
    <div class="hh-banner">
      <h2>HydroHack — Ship Hydrostatics &amp; Stability Solver</h2>
      <p>WAVEZ 2026 HydroHackathon entry · Simpson's-rule integration on offsets data ·
         polygon-clipping heeled stability solver · IS-Code intact-stability criteria.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------
# Sidebar — inputs
# ---------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 1 · Input file")
    uploaded = st.file_uploader(
        "Excel offsets workbook (.xlsx)",
        type=["xlsx", "xls"],
        help="Two sheets: a key/value 'Particulars' sheet and an "
             "'Offsets' matrix of half-breadths (stations × waterlines). "
             "See the README for the exact layout.",
    )
    use_sample = st.checkbox("Use bundled sample box hull", value=False,
                             help="Wall-sided rectangular block — used for analytical validation.")


if uploaded is None and not use_sample:
    st.info(
        "Upload an offsets workbook in the sidebar, or tick the **sample box** "
        "checkbox to demo the solver against a wall-sided rectangular hull "
        "with known analytical answers."
    )
    st.stop()


# Resolve the bytes + a friendly source name first; we'll then peek at the
# layout to decide whether KG / ρ must be supplied before we can parse it.
if use_sample:
    sample_path = Path(__file__).parent / "data" / "sample_box.xlsx"
    if not sample_path.exists():
        from tests.synthetic_box import write_sample_xlsx
        write_sample_xlsx(sample_path)
    file_bytes = sample_path.read_bytes()
    source_name = "sample_box.xlsx"
else:
    file_bytes = uploaded.getvalue()
    source_name = uploaded.name


# Layout peek: write a temp copy and ask the official-format detector
_peek = Path("/tmp") / "_hydrohack_peek.xlsx"
_peek.write_bytes(file_bytes)
is_official = looks_like_official(str(_peek))


# If the file is the official WAVEZ layout, KG is missing from the file —
# require the user to enter it BEFORE attempting to parse.
if is_official:
    # Empirical KG seed — Schneekluth & Bertram (1998) "Ship Design for
    # Efficiency and Economy", p.156: KG = C_KG · D, with C_KG = 0.55–0.58
    # for bulk carriers in loaded condition. We default to the low end
    # (0.55) since at C_KG ≥ 0.565 the 30°-min-phi-of-max-GZ IS-Code
    # criterion starts to fail on this full-form hull.
    import openpyxl as _opx
    _bk = _opx.load_workbook(str(_peek), data_only=True, read_only=True)
    _D = None
    for _sh in _bk.worksheets:
        for _row in _sh.iter_rows(values_only=True):
            for _i, _c in enumerate(_row):
                if isinstance(_c, str) and _c.strip().lower().startswith("depth"):
                    for _v in _row[_i + 1:]:
                        try:
                            _D = float(_v); break
                        except (TypeError, ValueError):
                            continue
            if _D is not None: break
        if _D is not None: break
    _kg_seed_default = round(0.55 * _D, 2) if _D else 15.0

    with st.sidebar:
        st.markdown("### KG and density")
        st.caption(
            f"WAVEZ layout detected — the workbook omits KG/ρ.\n\n"
            f"Default KG = **{_kg_seed_default:.2f} m**  ≈  0.55 · D  "
            f"(Schneekluth bulk-carrier empirical, low end of [0.55, 0.58]·D)."
        )
        kg_seed  = st.number_input("KG  (m)",     value=_kg_seed_default,  min_value=0.0, step=0.1,   format="%.4f", key="_kg_seed")
        rho_seed = st.number_input("ρ  (t/m³)",   value=1.025, min_value=0.0, step=0.001, format="%.4f", key="_rho_seed")
else:
    kg_seed = 0.0    # ignored — read from the workbook
    rho_seed = 1.025

try:
    payload = _load_from_bytes(file_bytes, kg_seed, rho_seed)
except Exception as exc:
    st.error(f"Failed to read the workbook: {exc}")
    st.stop()


with st.sidebar:
    st.markdown("### 2 · Parameters")
    st.caption("Pre-filled from the workbook · tweak before running.")

    T_in = st.number_input(
        "Draft  T  (m)",
        value=float(payload["T"]),
        min_value=0.0,
        max_value=float(payload["waterlines"][-1]),
        step=0.1, format="%.4f",
    )
    rho_in = st.number_input(
        "Density  ρ  (t/m³)",
        value=float(payload["rho"]), min_value=0.0,
        step=0.001, format="%.4f",
    )
    KG_in = st.number_input(
        "KG  (m)",
        value=float(payload["KG"]), min_value=0.0,
        step=0.1, format="%.4f",
    )

    st.markdown("### 3 · Heel-angle range")
    a_lo, a_hi = st.slider("φ range (deg)", min_value=0, max_value=90, value=(0, 90), step=1)
    a_step = st.select_slider("Step (deg)", options=[1, 2, 5, 10], value=5)

    st.markdown("### 4 · Hydrostatic-curves draft sweep")
    st.caption("Sweep starts at T = 0. Set the upper bound and the step.")

    _z_max = float(payload["waterlines"][-1])
    _T_default = float(payload["T"])
    _T_max_default = round(min(1.5 * _T_default, _z_max), 3)

    sweep_T_max = st.number_input(
        "Max draft  T_max  (m)",
        value=_T_max_default,
        min_value=0.5, step=0.5, format="%.3f",
        help=f"Default = 1.5 · T_design = {1.5*_T_default:.3f} m, clamped at the "
             f"top waterline z = {_z_max:.3f} m (no offset data above the moulded depth).",
    )
    sweep_step = st.number_input(
        "Step  ΔT  (m)",
        value=0.5,
        min_value=0.05, max_value=10.0, step=0.05, format="%.3f",
    )

    st.markdown("### 5 · Run")
    run = st.button("Run solver", type="primary", use_container_width=True)


# Persist the "solver was run" flag across reruns. Streamlit reruns the script
# on every widget interaction; without this flag, changing any widget would
# revert the page to the pre-run state.
if "ran" not in st.session_state:
    st.session_state.ran = False
if run:
    st.session_state.ran = True

if not st.session_state.ran:
    st.info("Set parameters in the sidebar, then click **Run solver**.")
    # Show a quick preview of the loaded particulars
    cols = st.columns(6)
    for col, (label, val, unit) in zip(
        cols,
        [("LBP", payload["LBP"], "m"), ("B", payload["B"], "m"), ("D", payload["D"], "m"),
         ("T", payload["T"], "m"), ("ρ", payload["rho"], "t/m³"), ("KG", payload["KG"], "m")],
    ):
        col.metric(f"{label}  ({unit})", _fmt(val, 3))
    st.caption(f"Loaded: **{source_name}** — {payload['offsets'].shape[0]} stations × "
               f"{payload['offsets'].shape[1]} waterlines.")
    st.stop()


# ---------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------
with st.spinner("Computing hydrostatics & stability curves…"):
    results = _compute_all(
        payload["stations"], payload["waterlines"], payload["offsets"],
        payload["LBP"], payload["B"],
        T_in, rho_in, KG_in,
        float(a_lo), float(a_hi), float(a_step),
        float(sweep_T_max), float(sweep_step),
    )

up = results["upright"]
curve: StabilityCurve = results["curve"]
A_x = results["section_areas"]
hull = Hull(stations=payload["stations"], waterlines=payload["waterlines"], offsets=payload["offsets"])


# ---------------------------------------------------------------------
# Executive summary — headline metrics
# ---------------------------------------------------------------------
crit = evaluate_criteria(curve, up.GMt)
overall = overall_pass(crit)
phi_max, gz_max = angle_of_max_gz(curve)
phi_van = vanishing_angle(curve)

m1, m2, m3, m4, m5 = st.columns([1, 1, 1, 1, 1])
m1.metric("Displacement  ∇",  f"{up.V:,.1f} m³")
m2.metric("Mass  Δ = ρ·∇",     f"{up.Delta:,.1f}",  help="tonnes if ρ in t/m³")
m3.metric("Initial GMt",        f"{up.GMt:,.3f} m",
          delta="positive — stable" if up.GMt > 0 else "negative — unstable",
          delta_color="normal" if up.GMt > 0 else "inverse")
m4.metric("Max GZ", f"{gz_max:.3f} m", f"at φ = {phi_max:.1f}°")
m5.metric("Vanishing φ", f"{phi_van:.1f}°" if phi_van else "—",
          help="first heel at which GZ returns to zero after the peak")


# ---------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------
tab_summary, tab_hydro, tab_stab, tab_trim, tab_geom, tab_3d, tab_method, tab_dl = st.tabs(
    ["Overview", "Hydrostatics", "Stability", "Trim", "Geometry", "3D model", "Methodology", "Downloads"]
)


# --- Overview tab ------------------------------------------------------
with tab_summary:
    st.markdown("#### IS-Code intact-stability criteria")
    pill_class = "hh-pass" if overall else "hh-fail"
    pill_text = "ALL CRITERIA SATISFIED" if overall else "ONE OR MORE CRITERIA NOT MET"
    st.markdown(
        f'<div style="margin: 0.4rem 0 1rem;"><span class="hh-pill {pill_class}">{pill_text}</span></div>',
        unsafe_allow_html=True,
    )

    df_crit = pd.DataFrame([
        {
            "Criterion": c.name,
            "Threshold": f"{c.threshold:g} {c.units}",
            "Computed":  f"{c.value:.4f} {c.units}",
            "Status":    "PASS" if c.passed else "FAIL",
            "Notes":     c.note,
        }
        for c in crit
    ])
    def _row_style(row):
        color = "rgba(16,185,129,0.10)" if row["Status"] == "PASS" else "rgba(239,68,68,0.12)"
        return [f"background-color: {color}"] * len(row)
    st.dataframe(
        df_crit.style.apply(_row_style, axis=1),
        use_container_width=True, hide_index=True,
    )

    st.caption(
        "Reference: IMO A.749(18), Ch. 3 (Intact Stability Code). "
        "Areas under GZ are integrated by trapezoid in radians on the heel grid."
    )


# --- Hydrostatics tab --------------------------------------------------
with tab_hydro:
    sweep_df: pd.DataFrame = results["sweep"]
    T_design = results["T_design"]
    T_max_req = results["sweep_T_max_requested"]
    T_max_act = results["sweep_T_max_actual"]
    step_used = results["sweep_step"]

    st.markdown("#### Hydrostatic curves  ·  draft sweep", unsafe_allow_html=True)
    cap = (f"User-supplied sweep: **0 → {T_max_req:.3f} m** at step **{step_used:.3f} m**.   "
           f"T<sub>design</sub> = **{T_design:.3f} m**.")
    if T_max_act < T_max_req - 1e-6:
        cap += (f"  Clamped to **{T_max_act:.3f} m** (top waterline) — no offset "
                f"data exists above the moulded depth, so drafts beyond it are "
                f"omitted from the sweep.")
    st.markdown(cap, unsafe_allow_html=True)

    st.pyplot(
        plotting.make_hydrostatic_curves_figure(
            drafts=sweep_df["T"].to_numpy(dtype=float),
            V=sweep_df["V"].to_numpy(dtype=float),
            Aw=sweep_df["Aw"].to_numpy(dtype=float),
            KMt=sweep_df["KMt"].to_numpy(dtype=float),
            LCB=sweep_df["LCB"].to_numpy(dtype=float),
            LCF=sweep_df["LCF"].to_numpy(dtype=float),
            LBP=payload["LBP"],
        ),
        use_container_width=True,
    )

    cGM, cBM = st.columns(2)
    with cGM:
        import matplotlib.pyplot as _plt
        fig_g, ax_g = _plt.subplots(figsize=(7, 4))
        plotting._apply_style(fig_g)
        ax_g.plot(sweep_df["T"], sweep_df["GMt"], lw=2, color=plotting.PRIMARY, label="GMt")
        ax_g.axhline(0, color=plotting.INK, lw=0.6)
        ax_g.axvline(T_design, color=plotting.ACCENT, lw=1.0, ls="--", label=f"T_design = {T_design:.2f} m")
        ax_g.set_xlabel("Draft  T  (m)")
        ax_g.set_ylabel("GMt  (m)")
        ax_g.set_title(f"GMt vs draft   (KG = {KG_in:.3f} m)")
        ax_g.legend(loc="best")
        fig_g.tight_layout()
        st.pyplot(fig_g, use_container_width=True)
    with cBM:
        fig_b, ax_b = _plt.subplots(figsize=(7, 4))
        plotting._apply_style(fig_b)
        ax_b.plot(sweep_df["T"], sweep_df["BMt"], lw=2, color=plotting.OK, label="BMt")
        ax_b.plot(sweep_df["T"], sweep_df["KB"],  lw=2, color=plotting.DANGER, label="KB")
        ax_b.axvline(T_design, color=plotting.ACCENT, lw=1.0, ls="--")
        ax_b.set_xlabel("Draft  T  (m)")
        ax_b.set_ylabel("(m)")
        ax_b.set_title("KB and BMt vs draft")
        ax_b.legend(loc="best")
        fig_b.tight_layout()
        st.pyplot(fig_b, use_container_width=True)

    # Display the full sweep table, with the design-draft row highlighted.
    show_cols = ["T", "V", "Delta", "Aw", "Sw", "LCB", "LCF", "KB", "BMt", "BMl",
                 "KMt", "GMt", "GMl", "CB", "CWP", "CM", "Cp"]
    units = {"T": "m", "V": "m³", "Delta": "t", "Aw": "m²", "Sw": "m²",
             "LCB": "m", "LCF": "m", "KB": "m", "BMt": "m", "BMl": "m",
             "KMt": "m", "GMt": "m", "GMl": "m",
             "CB": "", "CWP": "", "CM": "", "Cp": ""}
    df_show = sweep_df[show_cols].copy()
    df_show.columns = [f"{c}  ({units[c]})" if units[c] else c for c in show_cols]

    def _highlight_design(row):
        is_design = abs(row.iloc[0] - T_design) < 1e-6
        bg = "rgba(245,158,11,0.18)" if is_design else ""
        return [f"background-color: {bg}; font-weight: {'700' if is_design else '400'}"] * len(row)

    st.markdown("#### Numeric sweep table  ·  design-draft row highlighted")
    st.dataframe(
        df_show.style.format("{:.3f}").apply(_highlight_design, axis=1),
        use_container_width=True, hide_index=True, height=520,
    )

    st.markdown("---")
    st.markdown(f"#### Design-draft (T = {T_design:.3f} m) summary")
    cA, cB, cC, cD = st.columns([1, 1, 1, 1])

    def card(col, title: str, items: list[tuple[str, str]]):
        body = "".join(
            f'<div class="hh-row"><span class="hh-key">{k}</span>'
            f'<span class="hh-val">{v}</span></div>'
            for k, v in items
        )
        col.markdown(
            f'<div class="hh-card"><h4>{title}</h4>{body}</div>',
            unsafe_allow_html=True,
        )

    card(cA, "Volume & weight", [
        ("Displacement ∇",       f"{up.V:,.4f} m³"),
        ("Mass  Δ",              f"{up.Delta:,.4f}"),
        ("Weight  W",            f"{up.W:,.2f} kN"),
        ("Waterplane area  Aw",  f"{up.Aw:,.4f} m²"),
    ])
    card(cB, "Centres", [
        ("LCB",                 f"{up.LCB:,.4f} m"),
        ("LCF",                 f"{up.LCF:,.4f} m"),
        ("KB",                  f"{up.KB:,.4f} m"),
        ("midship offset",      f"{up.LCB - payload['LBP']/2:+.4f} m"),
    ])
    card(cC, "Stability", [
        ("BMt",                 f"{up.BMt:,.4f} m"),
        ("BMl",                 f"{up.BMl:,.2f} m"),
        ("KMt",                 f"{up.KMt:,.4f} m"),
        ("GMt",                 f"{up.GMt:,.4f} m"),
        ("GMl",                 f"{up.GMl:,.2f} m"),
    ])
    card(cD, "Form coefficients", [
        ("CB  block",           f"{up.CB:,.4f}"),
        ("CWP  waterplane",     f"{up.CWP:,.4f}"),
        ("CM  midship",         f"{up.CM:,.4f}"),
        ("Cp  prismatic",       f"{up.Cp:,.4f}"),
        ("Sw  wetted area",     f"{up.Sw:,.1f} m²"),
    ])

    st.markdown("#### Sectional area curve  A(x)")
    st.pyplot(plotting.make_section_areas_figure(payload["stations"], A_x, T_in),
              use_container_width=True)

    st.markdown("#### Bonjean curves  ·  A(x_i, T) for every station")
    st.caption(
        "One curve per station — the underwater sectional area swept over the "
        "same draft grid as the hydrostatic sweep. Used in trim and weight-"
        "distribution analysis (each station's curve answers 'how much "
        "section is immersed at draft T?')."
    )
    st.pyplot(
        plotting.make_bonjean_figure(
            payload["stations"], results["bonjean_drafts"], results["bonjean_areas"],
            T_design=T_in,
        ),
        use_container_width=True,
    )

    if payload["extra"]:
        # Filter out heavy numeric arrays that we don't want printed in the caption
        _display_extra = {k: v for k, v in payload["extra"].items()
                          if not isinstance(v, (np.ndarray, list, dict))}
        if _display_extra:
            st.caption(" · ".join(f"{k} = {v}" for k, v in _display_extra.items()))


# --- Stability curves tab ---------------------------------------------
with tab_stab:
    st.markdown("#### Static-stability curve")
    st.pyplot(plotting.make_gz_figure(curve, GMt=up.GMt), use_container_width=True)

    cKn, cTab = st.columns([1.2, 1])
    with cKn:
        st.markdown("#### Cross-curve of stability  (KN)")
        st.pyplot(plotting.make_kn_figure(curve), use_container_width=True)
    with cTab:
        st.markdown("#### Numeric data")
        df_curve = pd.DataFrame(
            {
                "φ (deg)":   curve.phi_deg,
                "KN (m)":    curve.KN,
                "GZ (m)":    curve.GZ,
                "T_world":   curve.T_world,
                "yB":        curve.yB,
                "zB":        curve.zB,
            }
        )
        st.dataframe(df_curve.style.format("{:.4f}"),
                     use_container_width=True, hide_index=True, height=440)

    A_30 = area_under_gz(curve, 0.0, 30.0)
    A_40 = area_under_gz(curve, 0.0, 40.0)
    A_3040 = area_under_gz(curve, 30.0, 40.0)
    cA1, cA2, cA3 = st.columns(3)
    cA1.metric("Area  0°–30°",  f"{A_30:.4f} m·rad")
    cA2.metric("Area  0°–40°",  f"{A_40:.4f} m·rad")
    cA3.metric("Area  30°–40°", f"{A_3040:.4f} m·rad")

    st.markdown("#### Cross-curves of stability  ·  KN(Δ) per heel angle")
    st.caption(
        "Family of KN curves over a sweep of displacements. KN is "
        "independent of KG, so the cross-curves give the geometric "
        "righting-arm response of the bare hull form — useful when "
        "evaluating multiple loading conditions without re-running "
        "the stability solver."
    )
    cc = results["cross_curves"]
    st.pyplot(
        plotting.make_cross_curves_figure(
            cc["displacements"], cc["phi_deg"], cc["KN"],
            Delta_design=up.Delta,
        ),
        use_container_width=True,
    )

    cc_df = pd.DataFrame(cc["KN"],
                         index=[f"T={t:.3f}, Δ={d:,.0f}"
                                for t, d in zip(cc["drafts"], cc["displacements"])],
                         columns=[f"{p:.0f}°" for p in cc["phi_deg"]])
    with st.expander("Cross-curves numeric table", expanded=False):
        st.dataframe(cc_df.style.format("{:.4f}"), use_container_width=True)


# --- Trim tab ----------------------------------------------------------
with tab_trim:
    st.markdown("#### Longitudinal trim balance")
    st.caption(
        "Solve the equilibrium waterline so that **LCB = LCG** while displacement "
        "stays at ρ·∇. Two unknowns (mean draft T_m and trim angle θ) found by "
        "nested bisection on the body-frame polygons. Useful when LCG is known "
        "from a loading condition and you want the realistic trimmed waterline "
        "rather than the upright-no-trim assumption used by the GZ solver."
    )

    if "trim_LCG" not in st.session_state:
        st.session_state.trim_LCG = float(up.LCB)
    if "trim_Delta" not in st.session_state:
        st.session_state.trim_Delta = float(up.Delta)

    cT1, cT2, cT3 = st.columns([1, 1, 0.7])
    with cT1:
        stage_LCG = st.number_input(
            "LCG  (m, from same origin as LCB)",
            value=float(st.session_state.trim_LCG),
            step=0.5, format="%.4f", key="trim_lcg_in",
            help=f"Default seeded with the upright LCB ({up.LCB:.3f} m). "
                 f"Move it forward to get bow-down trim, aft to get bow-up trim.",
        )
    with cT2:
        stage_Delta = st.number_input(
            "Δ target (mass)",
            value=float(st.session_state.trim_Delta),
            min_value=0.0, step=1000.0, format="%.1f", key="trim_delta_in",
            help="Mass displacement to balance the trimmed waterline against. "
                 "Default = the upright Δ at the design draft.",
        )
    with cT3:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("Solve trim", type="primary", use_container_width=True, key="trim_solve_btn"):
            st.session_state.trim_LCG = stage_LCG
            st.session_state.trim_Delta = stage_Delta

    with st.spinner("Solving trim equilibrium…"):
        tr = trim_balance(
            hull, rho=rho_in, displacement=st.session_state.trim_Delta,
            LCG=st.session_state.trim_LCG, LBP=payload["LBP"],
        )

    cM1, cM2, cM3, cM4 = st.columns(4)
    cM1.metric("Mean draft  T_m", f"{tr.T_mean:.4f} m")
    cM2.metric("Trim angle  θ",   f"{tr.theta_deg:+.4f}°",
               help="positive = bow-down (forward draft greater than aft)")
    cM3.metric("T_aft  /  T_fwd",
               f"{tr.T_aft:.3f}  /  {tr.T_fwd:.3f} m",
               help="drafts at the aft and forward perpendiculars")
    cM4.metric("Trim T_F − T_A", f"{tr.trim_m:+.4f} m")

    cS1, cS2, cS3 = st.columns(3)
    cS1.metric("∇ at trimmed eqm",     f"{tr.V:,.2f} m³")
    cS2.metric("LCB at trimmed eqm",   f"{tr.LCB:,.4f} m")
    cS3.metric("LCG (input)",          f"{tr.LCG:,.4f} m")

    if not tr.converged:
        st.warning(
            "Bisection did not fully converge — the supplied LCG may lie outside "
            "the achievable trim range for this displacement, or the bracket "
            "needs widening. The values shown are the best-effort solution; "
            "look at LCB vs LCG to see how close we got."
        )
    else:
        residual = abs(tr.LCB - tr.LCG)
        st.success(f"Converged.  |LCB − LCG| = {residual:.5f} m  ·  "
                   f"|Δ_computed − Δ_target| = {abs(rho_in*tr.V - st.session_state.trim_Delta):.2f}")

    st.caption(
        "Note — the GZ curve elsewhere in this app still assumes zero trim "
        "(LCG = LCB). The trim balance here is an upright-condition solver. "
        "Coupling trim into the GZ sweep would require iterating trim per "
        "heel angle and is not implemented yet."
    )


# --- Geometry tab ------------------------------------------------------
with tab_geom:
    st.markdown("#### Lines plan")
    st.caption(
        "Three orthogonal projections of the hull form: profile/sheer (side view, "
        "buttocks), half-breadth (top view, waterlines), and body plan (end-on, "
        "sections). Together these are the conventional naval-architecture lines plan."
    )

    # Persist the applied smoothness / buttock count between reruns; only the
    # explicit "Apply" button copies the staged widget values into the active
    # session state, so dragging the sliders does not redraw on every tick.
    if "geom_smooth" not in st.session_state:
        st.session_state.geom_smooth = 8
    if "geom_buttocks" not in st.session_state:
        st.session_state.geom_buttocks = 5

    cCtl1, cCtl2, cCtl3 = st.columns([1.2, 1, 0.7])
    with cCtl1:
        smooth_in = st.select_slider(
            "Curve smoothness  ·  linear-interpolation factor",
            options=[1, 2, 4, 8, 12, 16, 24, 32],
            value=st.session_state.geom_smooth,
            help="Inserts (factor−1) linearly-interpolated points along each "
                 "existing curve so the lines look smooth. The original "
                 "stations / waterlines stay as they are — no new curves are "
                 "added. Hydrostatics and GZ are always computed on the original "
                 "(un-densified) hull from the workbook.",
            key="geom_smooth_input",
        )
    with cCtl2:
        n_buttocks_in = st.slider(
            "Buttocks on the profile plan",
            min_value=2, max_value=10,
            value=st.session_state.geom_buttocks, step=1,
            key="geom_buttocks_input",
        )
    with cCtl3:
        st.markdown("&nbsp;", unsafe_allow_html=True)  # vertical alignment spacer
        if st.button("Apply", type="primary", use_container_width=True, key="geom_apply_btn"):
            st.session_state.geom_smooth = smooth_in
            st.session_state.geom_buttocks = n_buttocks_in

    smooth = int(st.session_state.geom_smooth)
    n_buttocks = int(st.session_state.geom_buttocks)

    # Axis-specific densification:
    #   body plan iterates over stations (one curve per station traced in z),
    #   so we densify only z to refine each curve without adding curves;
    #   waterline plan iterates over waterlines (one curve per WL traced in x),
    #   so we densify only x;
    #   profile plan reads both axes (buttocks as y(x,z) crossings), so we
    #   densify both.
    hull_body  = hull.densify(n_x_factor=1,      n_z_factor=smooth) if smooth > 1 else hull
    hull_wl    = hull.densify(n_x_factor=smooth, n_z_factor=1)      if smooth > 1 else hull
    hull_prof  = hull.densify(n_x_factor=smooth, n_z_factor=smooth) if smooth > 1 else hull

    if smooth > 1:
        st.caption(
            f"Smoothing factor **×{smooth}** — same {hull.n_stations} station and "
            f"{hull.n_waterlines} waterline curves as in the workbook, each drawn "
            f"with the densified intermediate points so the curves render smoothly. "
            f"Buttocks on the profile plan: **{n_buttocks}**."
        )

    st.markdown("##### Profile (sheer) plan  ·  buttocks at constant y")
    st.pyplot(
        plotting.make_profile_plan_figure(hull_prof, n_buttocks=n_buttocks),
        use_container_width=True,
    )

    st.markdown("##### Half-breadth (waterline) plan  ·  top-down view")
    st.pyplot(plotting.make_waterline_plan_figure(hull_wl), use_container_width=True)

    st.markdown("##### Body plan  ·  end-on view of the sections")
    cBp, cTab = st.columns([1.4, 1])
    with cBp:
        st.pyplot(plotting.make_body_plan_figure(hull_body), use_container_width=True)
        st.caption(
            "Stations colored bow → stern by viridis; forward half on the right, "
            "aft half mirrored on the left."
        )
    with cTab:
        st.markdown("**Offsets table  ·  original workbook values  (half-breadths, m)**")
        df = pd.DataFrame(
            payload["offsets"],
            index=[f"x={s:.2f}" for s in payload["stations"]],
            columns=[f"z={z:.2f}" for z in payload["waterlines"]],
        )
        st.dataframe(df.style.format("{:.3f}"),
                     use_container_width=True, height=560)


# --- 3D model tab ------------------------------------------------------
with tab_3d:
    st.markdown("#### Interactive 3-D view")
    st.caption(
        "All three lines-plan view types overlaid in a single 3-D scene — "
        "drag to rotate, scroll to zoom, shift-drag to pan. Click "
        "**Construct 3-D hull model** to switch to a shaded triangle-mesh "
        "rendering of the hull surface built directly from the offset table."
    )

    # Persist which view (lines / mesh) is active across reruns. Switching
    # tabs or changing widgets in other tabs no longer wipes the 3-D pane.
    if "view_3d" not in st.session_state:
        st.session_state.view_3d = "lines"
    if "n_buttocks_3d" not in st.session_state:
        st.session_state.n_buttocks_3d = 5
    if "smooth_3d" not in st.session_state:
        st.session_state.smooth_3d = 4

    cCtl1, cCtl2, cCtl3, cCtl4 = st.columns([1, 1, 1, 1])
    with cCtl1:
        smooth_3d_in = st.select_slider(
            "Smoothness ×",
            options=[1, 2, 4, 8, 12],
            value=st.session_state.smooth_3d,
            help="Linear-interpolation factor for the lines (preserves curve count, refines each curve).",
            key="smooth_3d_input",
        )
    with cCtl2:
        n_btk_3d_in = st.slider("Buttocks", min_value=2, max_value=10,
                                value=st.session_state.n_buttocks_3d, step=1,
                                key="n_btk_3d_input")
    with cCtl3:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("Show lines plan", use_container_width=True):
            st.session_state.view_3d = "lines"
            st.session_state.smooth_3d = smooth_3d_in
            st.session_state.n_buttocks_3d = n_btk_3d_in
    with cCtl4:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("Construct 3-D hull model", type="primary", use_container_width=True):
            st.session_state.view_3d = "mesh"
            st.session_state.smooth_3d = smooth_3d_in
            st.session_state.n_buttocks_3d = n_btk_3d_in

    smooth_3d = int(st.session_state.smooth_3d)
    n_btk_3d  = int(st.session_state.n_buttocks_3d)

    # --- Closure / shading options (only relevant for the mesh view) ---
    for k, default in [
        ("closure_bottom", False),
        ("closure_top",    False),
        ("shade_mode",     "depth"),  # "depth" (tealrose by z) or "grey" (opaque)
    ]:
        if k not in st.session_state:
            st.session_state[k] = default

    if st.session_state.view_3d == "mesh":
        st.markdown("##### Hull closures and shading")
        st.caption(
            "Stage the closures and shading mode below, then click **Apply** to "
            "rebuild the mesh. The current view stays intact while you toggle — "
            "nothing redraws until Apply is clicked."
        )

        cKb1, cKb2, cShade, cKbApply = st.columns([1, 1, 1.4, 1])
        with cKb1:
            stage_bottom = st.checkbox(
                "Fill bottom (keel cap)",
                value=st.session_state.closure_bottom,
                key="stage_closure_bottom",
                help="Flat triangulated cap at the lowest waterline.",
            )
        with cKb2:
            stage_top = st.checkbox(
                "Fill top (deck cap)",
                value=st.session_state.closure_top,
                key="stage_closure_top",
                help="Flat triangulated cap at the topmost waterline.",
            )
        with cShade:
            shade_options = {"Depth (tealrose, semi-transparent)": "depth",
                             "Opaque grey": "grey"}
            current_label = next(k for k, v in shade_options.items()
                                 if v == st.session_state.shade_mode)
            stage_shade_label = st.radio(
                "Shading",
                options=list(shade_options.keys()),
                index=list(shade_options.keys()).index(current_label),
                horizontal=False,
                key="stage_shade_radio",
            )
            stage_shade = shade_options[stage_shade_label]
        with cKbApply:
            st.markdown("&nbsp;", unsafe_allow_html=True)
            if st.button("Apply", type="primary", use_container_width=True, key="mesh_apply_btn"):
                st.session_state.closure_bottom = stage_bottom
                st.session_state.closure_top    = stage_top
                st.session_state.shade_mode     = stage_shade

    if st.session_state.view_3d == "lines":
        hull_for_btk = hull.densify(n_x_factor=smooth_3d, n_z_factor=smooth_3d) if smooth_3d > 1 else hull
        st.caption(
            f"Active view: **lines** · smoothness ×{smooth_3d} · {n_btk_3d} buttocks. "
            f"Click **Construct 3-D hull model** to render the shaded surface."
        )
        fig3d = plotting3d.make_3d_lines_figure(hull_for_btk, n_buttocks=n_btk_3d)
    else:
        hull_mesh = hull.densify(n_x_factor=smooth_3d, n_z_factor=smooth_3d) if smooth_3d > 1 else hull
        active_closures: list[str] = []
        if st.session_state.closure_bottom: active_closures.append("bottom")
        if st.session_state.closure_top:    active_closures.append("top")
        closure_str = (" + " + ", ".join(active_closures)) if active_closures else ""
        st.caption(
            f"Active view: **3-D hull mesh**{closure_str} · "
            f"{hull_mesh.n_stations}×{hull_mesh.n_waterlines} grid. "
            f"Click **Show lines plan** to switch back."
        )
        fig3d = plotting3d.make_3d_hull_mesh_figure(
            hull_mesh,
            fill_bottom=st.session_state.closure_bottom,
            fill_top=st.session_state.closure_top,
            shade_mode=st.session_state.shade_mode,
        )

    st.plotly_chart(fig3d, use_container_width=True)


# --- Methodology tab ---------------------------------------------------
with tab_method:
    st.markdown(
        """
        ### Numerical methodology

        **Upright hydrostatics.** Each station's underwater cross-section is reconstructed
        as a closed polygon and clipped against the horizontal plane *z = T* by Sutherland-Hodgman
        half-plane clipping, then integrated by the shoelace formula. This gives the section
        area *A(x)* and its area-centroid *(ȳ, z̄)* without any assumption about uniform
        waterline spacing. Length-wise integrals (∇, *LCB*, *KB*, *Aw*, *LCF*, *IT*, *IL*) are
        evaluated by composite Simpson's 1/3 when the station grid is uniform; otherwise by the
        non-uniform-grid generalisation that fits a parabola through every consecutive triplet.

        **Heeled stability (GZ / KN).** For each heel angle *φ* the station polygons are
        rotated by *R<sub>x</sub>(−φ)* into the world frame (gravity along −z, free
        surface horizontal). Bisection on the equilibrium-waterline height enforces
        conservation of displaced volume against the upright ∇. The transverse buoyancy-
        centroid *y<sub>B</sub>′* is the cross-curve arm *KN*; the righting arm is
        *GZ = KN − KG·sin φ*, with the assumption *LCG = LCB* (zero trim).

        **Validation.** A wall-sided rectangular block has closed-form analytical
        hydrostatics. The solver matches every upright quantity to machine precision and
        the small-angle GZ to the wall-sided formula *(GM + ½·BM·tan²φ)·sin φ* within ≈10⁻⁹ m.
        At 90° heel the solver recovers the exact analytical *GZ = 1.000 m* for the box.
        Independent cross-check: *dGZ/dφ* at *φ = 0* equals *GMt* — for the WAVEZ Bulk Carrier
        dataset these two quantities agree to 0.018 %.

        ### Assumptions

        - Trim is held at zero (LCG = LCB). For deeply-inclined hull forms this can shift
          the peak GZ by a few percent.
        - Linear interpolation of half-breadths between waterlines.
        - The hull is closed at the deck (no down-flooding openings).
        - Density given in t/m³ if numerically &lt; 10, otherwise interpreted as kg/m³.

        ### Libraries

        Only NumPy, pandas, openpyxl, matplotlib, and Streamlit are used. All numerical
        quadrature, polygon clipping, and stability logic is implemented from first
        principles in this package — no SciPy, no commercial naval-architecture software,
        no pre-built hydrostatics or stability libraries.
        """,
        unsafe_allow_html=True,
    )


# --- Downloads tab -----------------------------------------------------
with tab_dl:
    st.markdown("#### Download deliverables")

    def _fig_to_png(fig) -> bytes:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        return buf.getvalue()

    df_hydro_csv   = pd.DataFrame([up.as_dict()]).to_csv(index=False).encode()
    df_sweep_csv   = sweep_df.to_csv(index=False).encode()

    bj_df = pd.DataFrame(
        results["bonjean_areas"],
        index=[f"T={t:.3f}" for t in results["bonjean_drafts"]],
        columns=[f"x={s:.3f}" for s in payload["stations"]],
    )
    df_bonjean_csv = bj_df.to_csv().encode()

    cc_phi = results["cross_curves"]["phi_deg"]
    cc_T   = results["cross_curves"]["drafts"]
    cc_D   = results["cross_curves"]["displacements"]
    cc_KN  = results["cross_curves"]["KN"]
    cc_df_dl = pd.DataFrame(cc_KN,
                            index=[f"T={t:.3f}_Δ={d:.0f}" for t, d in zip(cc_T, cc_D)],
                            columns=[f"phi={p:.0f}" for p in cc_phi])
    df_cc_csv = cc_df_dl.to_csv().encode()
    df_curve_full = pd.DataFrame({
        "phi_deg":   curve.phi_deg,
        "KN_m":      curve.KN,
        "GZ_m":      curve.GZ,
        "T_world_m": curve.T_world,
        "yB_m":      curve.yB,
        "zB_m":      curve.zB,
    })
    df_curve_csv = df_curve_full.to_csv(index=False).encode()
    df_crit_csv = df_crit.to_csv(index=False).encode()

    gz_png    = _fig_to_png(plotting.make_gz_figure(curve, GMt=up.GMt))
    kn_png    = _fig_to_png(plotting.make_kn_figure(curve))
    bp_png    = _fig_to_png(plotting.make_body_plan_figure(hull))
    sec_png   = _fig_to_png(plotting.make_section_areas_figure(payload["stations"], A_x, T_in))
    wlp_png   = _fig_to_png(plotting.make_waterline_plan_figure(hull))
    prof_png  = _fig_to_png(plotting.make_profile_plan_figure(hull))
    bonj_png  = _fig_to_png(plotting.make_bonjean_figure(
        payload["stations"], results["bonjean_drafts"], results["bonjean_areas"], T_design=T_in))
    cc_png    = _fig_to_png(plotting.make_cross_curves_figure(
        cc_D, cc_phi, cc_KN, Delta_design=up.Delta))

    # Multi-sheet Excel
    xlsx_buf = io.BytesIO()
    with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as xw:
        pd.DataFrame([up.as_dict()]).to_excel(xw, sheet_name="hydrostatics", index=False)
        sweep_df.to_excel(xw, sheet_name="hydrostatic_sweep", index=False)
        bj_df.to_excel(xw, sheet_name="bonjean")
        cc_df_dl.to_excel(xw, sheet_name="cross_curves_KN")
        df_curve_full.to_excel(xw, sheet_name="gz_kn_curve", index=False)
        df_crit.to_excel(xw, sheet_name="criteria", index=False)
        pd.DataFrame(payload["offsets"],
                     index=[f"x={s:.3f}" for s in payload["stations"]],
                     columns=[f"z={z:.3f}" for z in payload["waterlines"]]
                     ).to_excel(xw, sheet_name="offsets")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("hydrostatics.csv",       df_hydro_csv, file_name="hydrostatics.csv",       mime="text/csv", use_container_width=True)
        st.download_button("hydrostatic_sweep.csv",  df_sweep_csv, file_name="hydrostatic_sweep.csv",  mime="text/csv", use_container_width=True)
        st.download_button("gz_curve.png",           gz_png,       file_name="gz_curve.png",           mime="image/png", use_container_width=True)
    with c2:
        st.download_button("gz_curve.csv",           df_curve_csv, file_name="gz_curve.csv",           mime="text/csv", use_container_width=True)
        st.download_button("kn_curve.png",           kn_png,       file_name="kn_curve.png",           mime="image/png", use_container_width=True)
    with c3:
        st.download_button("criteria.csv",           df_crit_csv,  file_name="criteria.csv",           mime="text/csv", use_container_width=True)
        st.download_button("body_plan.png",          bp_png,       file_name="body_plan.png",          mime="image/png", use_container_width=True)

    c4, c5, c6 = st.columns(3)
    c4.download_button("section_areas.png",  sec_png,  file_name="section_areas.png",  mime="image/png", use_container_width=True)
    c5.download_button("waterline_plan.png", wlp_png,  file_name="waterline_plan.png", mime="image/png", use_container_width=True)
    c6.download_button("profile_plan.png",   prof_png, file_name="profile_plan.png",   mime="image/png", use_container_width=True)

    c7, c8, c9, c10 = st.columns(4)
    c7.download_button("bonjean.csv",     df_bonjean_csv, file_name="bonjean.csv",     mime="text/csv",  use_container_width=True)
    c8.download_button("bonjean.png",     bonj_png,       file_name="bonjean.png",     mime="image/png", use_container_width=True)
    c9.download_button("cross_curves.csv", df_cc_csv,     file_name="cross_curves.csv", mime="text/csv",  use_container_width=True)
    c10.download_button("cross_curves.png", cc_png,        file_name="cross_curves.png", mime="image/png", use_container_width=True)

    st.download_button("results.xlsx (multi-sheet)", xlsx_buf.getvalue(),
                       file_name="hydrohack_results.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("hydrostatics.csv",       df_hydro_csv)
        zf.writestr("hydrostatic_sweep.csv",  df_sweep_csv)
        zf.writestr("gz_curve.csv",           df_curve_csv)
        zf.writestr("criteria.csv",           df_crit_csv)
        zf.writestr("gz_curve.png",           gz_png)
        zf.writestr("kn_curve.png",           kn_png)
        zf.writestr("body_plan.png",          bp_png)
        zf.writestr("section_areas.png",      sec_png)
        zf.writestr("waterline_plan.png",     wlp_png)
        zf.writestr("profile_plan.png",       prof_png)
        zf.writestr("bonjean.csv",            df_bonjean_csv)
        zf.writestr("bonjean.png",            bonj_png)
        zf.writestr("cross_curves.csv",       df_cc_csv)
        zf.writestr("cross_curves.png",       cc_png)
        zf.writestr("results.xlsx",           xlsx_buf.getvalue())
    st.download_button("Download all deliverables (.zip)",
                       zbuf.getvalue(),
                       file_name="hydrohack_outputs.zip",
                       mime="application/zip",
                       use_container_width=True, type="primary")


# ---------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------
st.markdown("---")
st.caption(
    f"Run on {source_name} ({payload['layout']} layout) · "
    f"{payload['offsets'].shape[0]} stations × {payload['offsets'].shape[1]} waterlines · "
    f"GZ grid {a_lo}°…{a_hi}° / {a_step}° · "
    f"draft sweep 0 → {results['sweep_T_max_actual']:.3f} m / {results['sweep_step']:.3f} m · "
    f"WAVEZ 2026 — IIT Madras Department of Ocean Engineering."
)
