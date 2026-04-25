"""Command-line interface for the HydroHack solver.

Usage
-----
    python -m hydrohack.cli --input data/ship.xlsx --output outputs/

Writes:
    outputs/hydrostatics.csv       (single-row table of upright results)
    outputs/gz_curve.csv           (heel angle, KN, GZ, T_world, yB, zB)
    outputs/gz_curve.png
    outputs/kn_curve.png
    outputs/body_plan.png
"""

from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

from .io_offsets import load_workbook
from .io_bulk_carrier import load_bulk_carrier
from .io_official import load_official, looks_like_official
from .hydrostatics import compute_upright
from .stability import gz_curve
from . import plotting


def parse_angles(arg: str) -> np.ndarray:
    """Parse ``--angles 0:90:5`` (start:stop:step) or comma list ``0,5,10,...``."""
    if ":" in arg:
        parts = [float(p) for p in arg.split(":")]
        if len(parts) == 2:
            start, stop = parts; step = 5.0
        elif len(parts) == 3:
            start, stop, step = parts
        else:
            raise argparse.ArgumentTypeError("angles spec must be start:stop[:step]")
        return np.arange(start, stop + 1e-9, step)
    return np.array([float(p) for p in arg.split(",") if p.strip()])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hydrohack", description="Ship hydrostatics & stability solver.")
    p.add_argument("--input", "-i", required=True, type=Path, help="Input .xlsx with offsets and particulars.")
    p.add_argument("--output", "-o", default=Path("outputs"), type=Path, help="Output directory (created if missing).")
    p.add_argument("--angles", default="0:90:5", type=parse_angles, help="Heel-angle grid in degrees (start:stop:step or comma list).")
    p.add_argument("--no-plots", action="store_true", help="Skip PNG plot generation.")
    p.add_argument("--format", choices=["auto", "official", "bulk_carrier", "generic"], default="auto",
                   help="Input layout. 'auto' detects WAVEZ-official two-sheet workbooks and falls back to the generic loader.")
    p.add_argument("--KG", type=float, default=None,
                   help="Vertical centre of gravity (m). Required for the official / bulk_carrier formats (workbook omits it).")
    p.add_argument("--rho", type=float, default=1.025,
                   help="Fluid density (used only when missing from the input). Default 1.025 t/m³ (sea water).")
    args = p.parse_args(argv)

    args.output.mkdir(parents=True, exist_ok=True)

    fmt = args.format
    if fmt == "auto":
        fmt = "official" if looks_like_official(args.input) else "generic"
    print(f"[1/4] Loading workbook: {args.input}  (format={fmt})")
    if fmt == "official":
        if args.KG is None:
            p.error("--KG is required for the official format (the workbook does not provide it).")
        ship = load_official(args.input, KG=args.KG, rho=args.rho)
    elif fmt == "bulk_carrier":
        if args.KG is None:
            p.error("--KG is required for the bulk_carrier format (the workbook does not provide it).")
        ship = load_bulk_carrier(args.input, KG=args.KG, rho=args.rho)
    else:
        ship = load_workbook(args.input)
    if ship.extra:
        for k, v in ship.extra.items():
            print(f"      reference: {k} = {v}")
    print(f"      Hull: {ship.hull.n_stations} stations × {ship.hull.n_waterlines} waterlines")
    print(f"      LBP={ship.LBP:.3f} m, B={ship.B:.3f} m, D={ship.D:.3f} m, T={ship.T:.3f} m")
    print(f"      ρ={ship.rho:.4f}, KG={ship.KG:.3f} m")

    print("[2/4] Computing upright hydrostatics...")
    up = compute_upright(ship.hull, draft=ship.T, rho=ship.rho, KG=ship.KG, LBP=ship.LBP, B=ship.B)
    pd.DataFrame([up.as_dict()]).to_csv(args.output / "hydrostatics.csv", index=False)
    print(f"      ∇ = {up.V:.4f} m³   Δ = {up.Delta:.4f}")
    print(f"      Aw = {up.Aw:.4f} m²   LCB = {up.LCB:.4f} m   LCF = {up.LCF:.4f} m")
    print(f"      KB = {up.KB:.4f} m   BMt = {up.BMt:.4f} m   GMt = {up.GMt:.4f} m")
    print(f"      BMl = {up.BMl:.3f} m   GMl = {up.GMl:.3f} m")
    print(f"      CB = {up.CB:.4f}   CWP = {up.CWP:.4f}   CM = {up.CM:.4f}")
    if "Delta_listed" in ship.extra:
        rel = (up.Delta / ship.extra["Delta_listed"] - 1.0) * 100.0
        print(f"      Δ vs reference {ship.extra['Delta_listed']:.1f}: {rel:+.3f}%")
    if "CB_listed" in ship.extra:
        rel = (up.CB / ship.extra["CB_listed"] - 1.0) * 100.0
        print(f"      CB vs reference {ship.extra['CB_listed']:.4f}: {rel:+.3f}%")

    print("[3/4] Computing GZ / KN curves...")
    curve = gz_curve(ship.hull, draft_upright=ship.T, rho=ship.rho, KG=ship.KG, angles_deg=args.angles)
    df_curve = pd.DataFrame({
        "phi_deg": curve.phi_deg,
        "KN_m": curve.KN,
        "GZ_m": curve.GZ,
        "T_world_m": curve.T_world,
        "yB_m": curve.yB,
        "zB_m": curve.zB,
    })
    df_curve.to_csv(args.output / "gz_curve.csv", index=False)
    print(f"      max GZ = {curve.GZ.max():.4f} m at φ = {curve.phi_deg[int(np.argmax(curve.GZ))]:.1f}°")

    if not args.no_plots:
        print("[4/4] Writing plots...")
        plotting.plot_gz_curve(curve, args.output / "gz_curve.png")
        plotting.plot_kn_curve(curve, args.output / "kn_curve.png")
        plotting.plot_body_plan(ship.hull, args.output / "body_plan.png")

    print(f"Done — results in {args.output}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
