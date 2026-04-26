"""Build all WAVEZ 2026 submission artifacts in one shot.

Reads ``data/official.xlsx``, runs the full solver pipeline, and writes:

    submission/output.xlsx        — computed hydrostatic values (judges' deliverable)
    submission/GZ_curve.png       — GZ curve plot (required upload)
    submission/KN_curves.png      — KN cross-curves (bonus upload)
    submission/hydrostatic_curves.png  — supplementary 4-panel sweep
    submission/section_areas.png  — supplementary
    submission/bonjean.png        — supplementary
    submission/body_plan.png      — supplementary
    submission/headline.json      — headline numbers used by the report
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from hydrohack.io_official import load_official
from hydrohack.hydrostatics import (
    compute_upright,
    compute_upright_sweep,
    bonjean_table,
    trim_balance,
)
from hydrohack.stability import gz_curve, cross_curves_kn
from hydrohack.criteria import evaluate_criteria
from hydrohack.plotting import (
    make_gz_figure,
    make_kn_figure,
    make_cross_curves_figure,
    make_hydrostatic_curves_figure,
    make_section_areas_figure,
    make_bonjean_figure,
    make_body_plan_figure,
)
from hydrohack.hydrostatics import _section_area_and_zcentroid

OUT = Path(__file__).resolve().parent
DATA = OUT.parent / "data" / "official.xlsx"

# ---------------------------------------------------------------------- run
ship = load_official(DATA, KG=20.5, rho=1.025)
hull = ship.hull
KG = ship.KG
rho = ship.rho
T = ship.T
LBP = ship.LBP
B = ship.B

upright = compute_upright(hull, draft=T, rho=rho, KG=KG, LBP=LBP, B=B)
TPC = rho * upright.Aw / 100.0  # t/cm
MCT = upright.Delta * upright.GMl / (100.0 * LBP)  # t·m/cm

# heel sweep 0..90 step 1
angles = np.arange(0.0, 90.5, 1.0)
gz = gz_curve(hull, draft_upright=T, rho=rho, KG=KG, angles_deg=angles)
crit = evaluate_criteria(gz, upright.GMt)

# KN cross-curves: drafts at 0.5T, 0.75T, T, 1.1T; angles 0..60 step 5
kn_drafts = np.array([0.5 * T, 0.75 * T, T, 1.1 * T])
kn_angles = np.arange(0.0, 60.5, 5.0)
kn = cross_curves_kn(hull, drafts=kn_drafts, rho=rho, angles_deg=kn_angles)

# Hydrostatic curves sweep (T = 0..1.5T_design step 0.5 m)
sweep_drafts = np.arange(0.5, 1.5 * T + 1e-9, 0.5)
sweep = compute_upright_sweep(hull, drafts=sweep_drafts, rho=rho, KG=KG, LBP=LBP, B=B)

# Bonjean (sectional area at draft) at the same sweep drafts
bonj = bonjean_table(hull, drafts=sweep_drafts)  # rows = stations, cols = drafts

# Trim solver demo: assume LCG = LCB_upright → expect θ ≈ 0
trim = trim_balance(hull, rho=rho, displacement=upright.V * rho, LCG=upright.LCB, LBP=LBP)

# ---------------------------------------------------------------------- xlsx
wb = openpyxl.Workbook()

# styling helpers
HEADER_FILL = PatternFill("solid", fgColor="0E7C86")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
SECTION_FILL = PatternFill("solid", fgColor="E5F4F6")
SECTION_FONT = Font(bold=True, size=12, color="0E4D54")
THIN = Side(border_style="thin", color="C0CAD2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def style_header_row(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER


def autosize(ws, max_w=28):
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        w = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col)
        ws.column_dimensions[col_letter].width = min(max(w + 2, 10), max_w)


# ------ Sheet 1: Summary
ws = wb.active
ws.title = "Summary"
ws["A1"] = "WAVEZ 2026 — Computed hydrostatic values"
ws["A1"].font = Font(bold=True, size=14, color="0E4D54")
ws["A2"] = f"Input: data/official.xlsx · KG = {KG:.3f} m (Schneekluth 0.55·D) · ρ = {rho:.3f} t/m³"
ws["A2"].font = Font(italic=True, color="555555")

rows = [
    ("Main particulars (from input)", None),
    ("LOA (m)", ship.extra.get("LOA", "—")),
    ("LBP (m)", LBP),
    ("Breadth B (m)", B),
    ("Depth D (m)", ship.D),
    ("Design draft T (m)", T),
    ("CB (listed)", ship.extra.get("CB_listed", "—")),
    ("ρ (t/m³)", rho),
    ("KG (m)", KG),
    ("", None),
    ("Upright hydrostatics @ T = %.3f m" % T, None),
    ("Volume of displacement ∇ (m³)", upright.V),
    ("Displacement Δ (t)", upright.Delta),
    ("Block coefficient CB", upright.CB),
    ("Waterplane area Aw (m²)", upright.Aw),
    ("Waterplane coefficient CWP", upright.CWP),
    ("Midship-section coefficient CM", upright.CM),
    ("Prismatic coefficient Cp", upright.Cp),
    ("Wetted surface Sw (m²)", upright.Sw),
    ("LCB from midship (m, +fwd)", upright.LCB),
    ("LCF from midship (m, +fwd)", upright.LCF),
    ("KB (m)", upright.KB),
    ("BMt (m)", upright.BMt),
    ("BMl (m)", upright.BMl),
    ("KMt (m)", upright.KMt),
    ("KMl (m)", upright.KMl),
    ("GMt (m)", upright.GMt),
    ("GMl (m)", upright.GMl),
    ("TPC (t/cm)", TPC),
    ("MCT 1cm (t·m/cm)", MCT),
    ("", None),
    ("Stability — heel sweep summary (0–90°, 1° step)", None),
    ("Max GZ (m)", float(np.max(gz.GZ))),
    ("Heel of max GZ (deg)", float(gz.phi_deg[int(np.argmax(gz.GZ))])),
    ("Vanishing angle (deg)", float(_v) if (_v := next((c.value for c in crit if "Vanishing" in c.name), None)) is not None else "—"),
    ("Area 0–30° (m·rad)", float(next(c.value for c in crit if "Area 0" in c.name and "30" in c.name and "40" not in c.name)),),
    ("Area 0–40° (m·rad)", float(next(c.value for c in crit if "Area 0" in c.name and "40" in c.name)),),
    ("Area 30–40° (m·rad)", float(next(c.value for c in crit if "Area 30" in c.name and "40" in c.name)),),
    ("Overall IS-Code A.749 pass", "PASS" if all(c.passed for c in crit) else "FAIL"),
    ("", None),
    ("Trim solver self-check (LCG = LCB_upright)", None),
    ("θ (deg)", float(trim.theta_deg)),
    ("Trim t = T_aft − T_fwd (m)", float(trim.trim_m)),
    ("T mean (m)", float(trim.T_mean)),
    ("Volume after balance (m³)", float(trim.V)),
    ("LCB after balance (m)", float(trim.LCB)),
    ("Converged", "yes" if trim.converged else "no"),
]
r0 = 4
for i, (label, val) in enumerate(rows):
    r = r0 + i
    if val is None and label:
        ws.cell(row=r, column=1, value=label).fill = SECTION_FILL
        ws.cell(row=r, column=1).font = SECTION_FONT
        ws.merge_cells(start_row=r, end_row=r, start_column=1, end_column=2)
        continue
    ws.cell(row=r, column=1, value=label)
    c = ws.cell(row=r, column=2, value=val)
    if isinstance(val, float):
        if abs(val) < 0.01 or abs(val) > 1e5:
            c.number_format = "0.000E+00"
        else:
            c.number_format = "0.0000"
ws.column_dimensions["A"].width = 46
ws.column_dimensions["B"].width = 22

# ------ Sheet 2: GZ curve
ws = wb.create_sheet("GZ curve")
ws.append(["Heel (deg)", "GZ (m)", "KN (m)", "Equilibrium draft T_h (m)", "yB (m)", "zB (m)"])
style_header_row(ws, 1, 6)
for i in range(len(gz.phi_deg)):
    ws.append(
        [
            float(gz.phi_deg[i]),
            float(gz.GZ[i]),
            float(gz.KN[i]),
            float(gz.T_world[i]),
            float(gz.yB[i]),
            float(gz.zB[i]),
        ]
    )
for r in range(2, ws.max_row + 1):
    for c in range(1, 7):
        ws.cell(row=r, column=c).number_format = "0.0000"
autosize(ws)

# ------ Sheet 3: KN cross-curves
ws = wb.create_sheet("KN cross-curves")
ws.cell(row=1, column=1, value="Heel φ (deg) ↓ \\ Draft T (m) →").font = Font(bold=True)
for j, d in enumerate(kn["drafts"]):
    ws.cell(row=1, column=2 + j, value=f"T = {d:.3f}")
style_header_row(ws, 1, 1 + len(kn["drafts"]))
for i, ang in enumerate(kn["phi_deg"]):
    ws.cell(row=2 + i, column=1, value=float(ang))
    for j in range(len(kn["drafts"])):
        ws.cell(row=2 + i, column=2 + j, value=float(kn["KN"][j, i]))
for r in range(2, ws.max_row + 1):
    for c in range(1, ws.max_column + 1):
        ws.cell(row=r, column=c).number_format = "0.0000"
autosize(ws)

# ------ Sheet 4: IS-Code criteria
ws = wb.create_sheet("Stability criteria")
ws.append(["Criterion", "Value", "Units", "Threshold", "Pass", "Note"])
style_header_row(ws, 1, 6)
for c in crit:
    ws.append([c.name, float(c.value), c.units, float(c.threshold), "PASS" if c.passed else "FAIL", c.note])
for r in range(2, ws.max_row + 1):
    ws.cell(row=r, column=2).number_format = "0.0000"
    ws.cell(row=r, column=4).number_format = "0.0000"
    pf = ws.cell(row=r, column=5).value
    ws.cell(row=r, column=5).fill = PatternFill(
        "solid", fgColor=("C6EFCE" if pf == "PASS" else "F8C8C8")
    )
autosize(ws, max_w=44)

# ------ Sheet 5: Hydrostatic curves sweep
ws = wb.create_sheet("Hydrostatic curves")
ws.append(
    [
        "T (m)", "∇ (m³)", "Δ (t)", "Aw (m²)", "LCB (m)", "LCF (m)",
        "KB (m)", "BMt (m)", "BMl (m)", "KMt (m)", "GMt (m)",
        "TPC (t/cm)", "MCT (t·m/cm)", "Cp", "Sw (m²)",
    ]
)
style_header_row(ws, 1, 15)
for r in sweep:
    tpc_r = rho * r.Aw / 100.0
    mct_r = r.Delta * r.GMl / (100.0 * LBP) if r.GMl == r.GMl else float("nan")
    ws.append(
        [
            r.T, r.V, r.Delta, r.Aw, r.LCB, r.LCF,
            r.KB, r.BMt, r.BMl, r.KMt, r.GMt,
            tpc_r, mct_r, r.Cp, r.Sw,
        ]
    )
for r in range(2, ws.max_row + 1):
    for c in range(1, 16):
        ws.cell(row=r, column=c).number_format = "0.0000"
autosize(ws)

# ------ Sheet 6: Bonjean curves
ws = wb.create_sheet("Bonjean")
ws.cell(row=1, column=1, value="Station x (m) ↓ \\ Draft T (m) →").font = Font(bold=True)
for j, d in enumerate(sweep_drafts):
    ws.cell(row=1, column=2 + j, value=f"T = {d:.2f}")
style_header_row(ws, 1, 1 + len(sweep_drafts))
for i, x in enumerate(hull.stations):
    ws.cell(row=2 + i, column=1, value=float(x))
    for j in range(len(sweep_drafts)):
        ws.cell(row=2 + i, column=2 + j, value=float(bonj[j, i]))
for r in range(2, ws.max_row + 1):
    for c in range(1, ws.max_column + 1):
        ws.cell(row=r, column=c).number_format = "0.000"
autosize(ws)

xlsx_path = OUT / "output.xlsx"
wb.save(xlsx_path)
print(f"wrote {xlsx_path}")

# ---------------------------------------------------------------------- plots
import matplotlib
matplotlib.use("Agg")

fig = make_gz_figure(gz, GMt=upright.GMt)
fig.savefig(OUT / "GZ_curve.png", dpi=180, bbox_inches="tight")
print("wrote GZ_curve.png")

# KN at design draft (single-line plot — required submission picture)
fig = make_kn_figure(gz)
fig.savefig(OUT / "KN_curve.png", dpi=180, bbox_inches="tight")
print("wrote KN_curve.png")

# Cross-curves of stability — KN vs Δ at multiple heel angles (richer chart for the report)
fig = make_cross_curves_figure(
    displacements=kn["displacements"],
    phi_deg=kn["phi_deg"],
    KN=kn["KN"],
    Delta_design=upright.Delta,
)
fig.savefig(OUT / "KN_cross_curves.png", dpi=180, bbox_inches="tight")
print("wrote KN_cross_curves.png")

# Hydrostatic curves sweep — 4-panel
sweep_T = np.array([r.T for r in sweep])
sweep_V = np.array([r.V for r in sweep])
sweep_Aw = np.array([r.Aw for r in sweep])
sweep_KMt = np.array([r.KMt for r in sweep])
sweep_LCB = np.array([r.LCB for r in sweep])
sweep_LCF = np.array([r.LCF for r in sweep])
fig = make_hydrostatic_curves_figure(
    drafts=sweep_T, V=sweep_V, Aw=sweep_Aw, KMt=sweep_KMt,
    LCB=sweep_LCB, LCF=sweep_LCF, LBP=LBP,
)
fig.savefig(OUT / "hydrostatic_curves.png", dpi=180, bbox_inches="tight")
print("wrote hydrostatic_curves.png")

# Sectional areas at design draft
section_areas = np.array(
    [_section_area_and_zcentroid(hull, i, T)[0] for i in range(len(hull.stations))]
)
fig = make_section_areas_figure(hull.stations, section_areas, T)
fig.savefig(OUT / "section_areas.png", dpi=180, bbox_inches="tight")
print("wrote section_areas.png")

# Bonjean curves: areas shape (drafts, stations)
fig = make_bonjean_figure(hull.stations, sweep_drafts, bonj, T_design=T)
fig.savefig(OUT / "bonjean.png", dpi=180, bbox_inches="tight")
print("wrote bonjean.png")

fig = make_body_plan_figure(hull)
fig.savefig(OUT / "body_plan.png", dpi=180, bbox_inches="tight")
print("wrote body_plan.png")

# ---------------------------------------------------------------------- json headline
headline = {
    "input_file": "data/official.xlsx",
    "LOA": ship.extra.get("LOA"),
    "LBP": LBP,
    "B": B,
    "D": ship.D,
    "T": T,
    "CB_listed": ship.extra.get("CB_listed"),
    "rho": rho,
    "KG": KG,
    "KG_method": "Schneekluth & Bertram (1998), C_KG=0.55 (low-end bulk-carrier band)",
    "upright": {
        "V_m3": upright.V,
        "Delta_t": upright.Delta,
        "CB_computed": upright.CB,
        "Aw_m2": upright.Aw,
        "CWP": upright.CWP,
        "CM": upright.CM,
        "Cp": upright.Cp,
        "Sw_m2": upright.Sw,
        "LCB_m": upright.LCB,
        "LCF_m": upright.LCF,
        "KB_m": upright.KB,
        "BMt_m": upright.BMt,
        "BMl_m": upright.BMl,
        "KMt_m": upright.KMt,
        "KMl_m": upright.KMl,
        "GMt_m": upright.GMt,
        "GMl_m": upright.GMl,
        "TPC_t_per_cm": TPC,
        "MCT_t_m_per_cm": MCT,
    },
    "stability": {
        "max_GZ_m": float(np.max(gz.GZ)),
        "heel_of_max_GZ_deg": float(gz.phi_deg[int(np.argmax(gz.GZ))]),
        "vanishing_angle_deg": next(
            (float(c.value) for c in crit if "Vanishing" in c.name), None
        ),
        "area_0_30_m_rad": float(next(c.value for c in crit if "Area 0" in c.name and "30" in c.name and "40" not in c.name)),
        "area_0_40_m_rad": float(next(c.value for c in crit if "Area 0" in c.name and "40" in c.name)),
        "area_30_40_m_rad": float(next(c.value for c in crit if "Area 30" in c.name and "40" in c.name)),
        "overall_pass": all(c.passed for c in crit),
        "criteria": [
            {"name": c.name, "value": float(c.value), "units": c.units, "threshold": float(c.threshold), "pass": bool(c.passed), "note": c.note}
            for c in crit
        ],
    },
    "trim_self_check": {
        "LCG_input_m": upright.LCB,
        "theta_deg": trim.theta_deg,
        "trim_m": trim.trim_m,
        "T_mean_m": trim.T_mean,
        "V_balanced_m3": trim.V,
        "LCB_balanced_m": trim.LCB,
        "converged": bool(trim.converged),
    },
}
(OUT / "headline.json").write_text(json.dumps(headline, indent=2, default=str))
print("wrote headline.json")
print("done.")
