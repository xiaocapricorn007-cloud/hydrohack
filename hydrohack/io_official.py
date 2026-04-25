"""Loader for the official WAVEZ 2026 input layout.

Two sheets:

    "Main Particulars"
        Key/value pairs in columns B–C:
            Length overall (LOA), Breadth, Draft, Depth, CB
        Plus an embedded waterline-schedule table (label / % of draft / WL height)
        that we don't need — the WL heights are repeated on the "Offset data"
        sheet anyway.

    "Offset data"
        Row "STN Spacing"   →  station x-positions (m)         [non-uniform]
        Row "WL Spacing"    →  station numbers (0…N)           [we ignore this]
        Below: one row per waterline. Column B = WL label
        ("0", "A", "B", … "K"), column C = WL height in metres,
        columns D… = half-breadths at each station.

    Notes
    -----
    * No KG and no ρ in the workbook — caller must supply KG; ρ defaults
      to 1.025 t/m³ (sea water).
    * The first waterline in the offset matrix is the baseline (z = 0),
      so no flat-bottom extrapolation is needed.
    * Stations and waterlines are both **non-uniform**; the solver's
      ``integrate(method="auto")`` dispatcher picks non-uniform Simpson.
"""

from __future__ import annotations
import re
from pathlib import Path
import numpy as np
import pandas as pd
import openpyxl

from .geometry import Hull
from .io_offsets import ShipInput

DEFAULT_RHO_SEAWATER = 1.025  # t/m³

# Sheet names seen in the WAVEZ 2026 file. Match is case-insensitive.
PARTICULARS_SHEETS = ("main particulars", "particulars")
OFFSETS_SHEETS     = ("offset data", "offsets")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _norm(s: object) -> str:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    return re.sub(r"[\s_().,/-]+", "", str(s)).lower()


def _find_value(df: pd.DataFrame, *aliases: str) -> float | None:
    """Find the first numeric cell to the right of any cell whose normalised
    text equals one of ``aliases`` (also normalised)."""
    targets = {_norm(a) for a in aliases}
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            cell = df.iat[r, c]
            n = _norm(cell)
            if n in targets or any(t in n for t in targets):
                for cc in range(c + 1, df.shape[1]):
                    v = df.iat[r, cc]
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
    return None


def _pick_sheet(book: openpyxl.Workbook, names_low: tuple[str, ...]) -> openpyxl.worksheet.worksheet.Worksheet | None:
    for sh in book.worksheets:
        if sh.title.strip().lower() in names_low:
            return sh
    return None


def _extract_centerline_profiles_xyz(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Pull stern + stem centerline profiles from a sheet using the
    ``(x, y, z)`` triplet layout — the columns under cells containing
    just ``"STERN"`` or ``"STEM"``, where each subsequent row is a
    string like ``"13.73,0,0.18"``.
    """
    out: dict[str, np.ndarray] = {}
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            cell = df.iat[r, c]
            if not isinstance(cell, str):
                continue
            tag = cell.strip().upper()
            if tag not in ("STERN", "STEM"):
                continue
            key = tag.lower()
            pts: list[list[float]] = []
            for rr in range(r + 1, df.shape[0]):
                v = df.iat[rr, c]
                if not isinstance(v, str):
                    break
                parts = v.split(",")
                if len(parts) != 3:
                    break
                try:
                    pts.append([float(p.strip()) for p in parts])
                except ValueError:
                    break
            if pts:
                out[key] = np.asarray(pts, dtype=float)
    return out


def _find_wl_height_map(df: pd.DataFrame) -> dict[str, float] | None:
    """Locate a row whose first non-empty cell text matches ``"WL height"`` and
    return a mapping from waterline label (``A``, ``B`` …) to z value (m).

    The waterline labels live on a *previous* row above the heights row
    (within five rows). Used by the Table 4.4 / 4.5 profile parser to
    convert label-keyed x-coordinates into ``(x, y, z)`` triplets.
    """
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            v = df.iat[r, c]
            if not isinstance(v, str):
                continue
            if "wlheight" in re.sub(r"\s+", "", v.lower()):
                z_vals: list[float] = []
                for cc in range(c + 1, df.shape[1]):
                    try:
                        z_vals.append(float(df.iat[r, cc]))
                    except (TypeError, ValueError):
                        if z_vals:
                            break
                        continue
                if not z_vals:
                    continue
                # search up for a labels row aligned with these heights
                for rr in range(max(0, r - 5), r):
                    labels: list[str] = []
                    for cc in range(c + 1, c + 1 + len(z_vals)):
                        cell = df.iat[rr, cc]
                        if cell is None or (isinstance(cell, float) and np.isnan(cell)):
                            labels = []
                            break
                        s = str(cell).strip()
                        if not s or len(s) > 4:        # WL labels are short tokens
                            labels = []
                            break
                        labels.append(s)
                    if len(labels) == len(z_vals):
                        return dict(zip(labels, z_vals))
    return None


def _extract_centerline_profiles_table(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Extract stern / stem profiles from the WAVEZ Table 4.4 / 4.5 layout.

    Layout::

        TABLE 4.4 Stern profile offset.  (description cell)
        A   B   C   D   ...   K                 (waterline-label row)
        6.016 6.818 7.219 7.62 ...               (x values, one per WL)

    Returns a dict with ``stern`` and/or ``stem`` keys, each an Nx3
    array of ``(x, 0, z)`` triplets — z values resolved via the
    ``WL height`` map on the same sheet.
    """
    wl_map = _find_wl_height_map(df)
    if not wl_map:
        return {}

    out: dict[str, np.ndarray] = {}
    valid_labels = set(wl_map.keys())
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            v = df.iat[r, c]
            if not isinstance(v, str):
                continue
            v_low = v.lower()
            if "stern" in v_low and "profile" in v_low:
                key = "stern"
            elif "stem" in v_low and "profile" in v_low:
                key = "stem"
            else:
                continue
            if key in out:
                continue

            # Scan the next few rows for a labels row, then read x-values one row below it.
            for d in (1, 2, 3):
                rr = r + d
                if rr + 1 >= df.shape[0]:
                    break
                labels_with_cols: list[tuple[int, str]] = []
                for cc in range(df.shape[1]):
                    cell = df.iat[rr, cc]
                    if cell is None or (isinstance(cell, float) and np.isnan(cell)):
                        continue
                    s = str(cell).strip()
                    if s and s in valid_labels:
                        labels_with_cols.append((cc, s))
                if len(labels_with_cols) < 3:
                    continue
                pts: list[list[float]] = []
                for cc, label in labels_with_cols:
                    x_cell = df.iat[rr + 1, cc]
                    try:
                        x_val = float(x_cell)
                    except (TypeError, ValueError):
                        continue
                    pts.append([x_val, 0.0, float(wl_map[label])])
                if len(pts) >= 3:
                    # sort by z so the curve is monotone
                    arr = np.asarray(pts, dtype=float)
                    arr = arr[np.argsort(arr[:, 2])]
                    out[key] = arr
                    break
    return out


def looks_like_official(path: str | Path) -> bool:
    """Return True if the workbook has the WAVEZ "Main Particulars" + "Offset data" pair."""
    try:
        book = openpyxl.load_workbook(path, data_only=True, read_only=True)
        names = {sh.title.strip().lower() for sh in book.worksheets}
        return any(n in names for n in PARTICULARS_SHEETS) and any(n in names for n in OFFSETS_SHEETS)
    except Exception:
        return False


# ---------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------
def load_official(
    path: str | Path,
    *,
    KG: float,
    rho: float = DEFAULT_RHO_SEAWATER,
) -> ShipInput:
    """Read the WAVEZ-style two-sheet workbook into :class:`ShipInput`.

    Parameters
    ----------
    path : str | Path
        Path to the workbook.
    KG : float
        Vertical centre of gravity (m). Required — the workbook does
        not provide it.
    rho : float, default 1.025
        Fluid density in t/m³.
    """
    book = openpyxl.load_workbook(path, data_only=True)

    sh1 = _pick_sheet(book, PARTICULARS_SHEETS)
    sh2 = _pick_sheet(book, OFFSETS_SHEETS)
    if sh1 is None or sh2 is None:
        raise ValueError(
            "Workbook does not contain both a 'Main Particulars' and an "
            "'Offset data' sheet."
        )

    # 1) Main particulars -----------------------------------------------
    df1 = pd.DataFrame(sh1.values)
    LOA = _find_value(df1, "Length overall", "LOA")
    B   = _find_value(df1, "Breadth")
    T   = _find_value(df1, "Draft", "Draught")
    D   = _find_value(df1, "Depth")
    CB  = _find_value(df1, "CB", "C_B", "Block Coefficient")

    if None in (B, T, D):
        raise ValueError(f"Missing particulars on Main Particulars sheet: B={B}, T={T}, D={D}")

    # 2) Offset data sheet ----------------------------------------------
    df2 = pd.DataFrame(sh2.values)

    # Find the row whose cells contain "STN Spacing"; that row gives station x-positions.
    stn_row, stn_col = -1, -1
    for r in range(df2.shape[0]):
        for c in range(df2.shape[1]):
            if _norm(df2.iat[r, c]) == "stnspacing":
                stn_row, stn_col = r, c
                break
        if stn_row >= 0:
            break
    if stn_row < 0:
        raise ValueError("Could not find 'STN Spacing' row on the Offset data sheet.")

    stations: list[float] = []
    for cc in range(stn_col + 1, df2.shape[1]):
        v = df2.iat[stn_row, cc]
        try:
            stations.append(float(v))
        except (TypeError, ValueError):
            break
    stations = np.asarray(stations, dtype=float)
    n_stations = stations.size
    if n_stations < 3:
        raise ValueError(f"Too few stations parsed ({n_stations}).")

    # Waterline rows live below "WL Spacing". Each row: col(stn_col) = WL height.
    # Skip the "WL Spacing" header row (one row below STN Spacing).
    waterlines: list[float] = []
    offsets_rows: list[list[float]] = []
    for r in range(stn_row + 2, df2.shape[0]):
        z = df2.iat[r, stn_col]
        try:
            z_val = float(z)
        except (TypeError, ValueError):
            # blank row → end of waterlines
            if not waterlines:
                continue
            break
        waterlines.append(z_val)
        row_offsets: list[float] = []
        for cc in range(stn_col + 1, stn_col + 1 + n_stations):
            v = df2.iat[r, cc]
            try:
                row_offsets.append(float(v))
            except (TypeError, ValueError):
                row_offsets.append(0.0)
        offsets_rows.append(row_offsets)

    if not waterlines:
        raise ValueError("Could not locate any waterline rows below 'WL Spacing'.")
    waterlines = np.asarray(waterlines, dtype=float)
    offsets_wl_by_stn = np.asarray(offsets_rows, dtype=float)  # (n_wl, n_stations)
    offsets = offsets_wl_by_stn.T                              # → (n_stations, n_wl)

    # 3) Build the hull --------------------------------------------------
    LBP = float(stations[-1] - stations[0])
    hull = Hull(stations=stations, waterlines=waterlines, offsets=offsets)

    extra: dict = {}
    if LOA is not None:
        extra["LOA"] = float(LOA)
    if CB is not None:
        extra["CB_listed"] = float(CB)
    # Reference displacement = CB × LBP × B × T
    if CB is not None:
        extra["Delta_listed_estimate"] = float(rho * CB * LBP * B * T)
    # Stern + stem centerline profiles (Nx3 arrays of (x, y, z) points).
    # Two layouts are supported and tried in order:
    #   1. (x,y,z) string triplets under "STERN" / "STEM" headers (Offset
    #      data sheet on the WAVEZ workbook). Preferred — has the keel point.
    #   2. Table 4.4 / 4.5 layout: a row of waterline letters (A…K) with
    #      x-coordinates below, on the Main Particulars sheet.
    profile_sources: dict[str, str] = {}
    for src_name, _df in [("xyz", df2), ("xyz", df1)]:
        if "stern_profile" in extra and "stem_profile" in extra:
            break
        profiles = _extract_centerline_profiles_xyz(_df)
        for key in ("stern", "stem"):
            if key in profiles and f"{key}_profile" not in extra:
                extra[f"{key}_profile"] = profiles[key]
                profile_sources[key] = src_name
    for src_name, _df in [("table_4.4/4.5", df1), ("table_4.4/4.5", df2)]:
        if "stern_profile" in extra and "stem_profile" in extra:
            break
        profiles = _extract_centerline_profiles_table(_df)
        for key in ("stern", "stem"):
            if key in profiles and f"{key}_profile" not in extra:
                extra[f"{key}_profile"] = profiles[key]
                profile_sources[key] = src_name
    if profile_sources:
        extra["profile_sources"] = profile_sources

    return ShipInput(
        hull=hull,
        LBP=LBP, B=float(B), D=float(D), T=float(T),
        rho=float(rho), KG=float(KG),
        extra=extra,
    )
