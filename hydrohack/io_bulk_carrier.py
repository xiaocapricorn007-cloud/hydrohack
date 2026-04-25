"""Tailored reader for the WAVEZ Bulk Carrier ``.xlsx`` layout.

This file's structure does **not** match the auto-detector's
expectations:

* Particulars (LBP/B/D/T/CB) live as key/value pairs in columns A–B
  on the same sheet as the offsets table.
* A small "Waterline heights" table sits at the top right, giving the
  z-position of each waterline (labelled A, B, C, …, K) — these
  waterlines are **not** uniformly spaced.
* The offsets table uses *station numbers* (0, 0.25, 0.5, …, 10) in
  the first column and the same letter labels (A…K) for waterlines in
  the header. Station numbers are also non-uniform.
* The lowest waterline (A) is at z = 1.36 m, not at the keel — we
  extrapolate down to z = 0 using a flat-bottom assumption.
* No density and no KG are given. We default ρ = 1.025 t/m³ and KG must
  be supplied at the call site.

If the official competition input uses a similar layout, this loader
can be invoked directly by the CLI via ``--format bulk_carrier``.
"""

from __future__ import annotations
from pathlib import Path
import re
import numpy as np
import pandas as pd
import openpyxl

from .geometry import Hull
from .io_offsets import ShipInput

DEFAULT_RHO_SEAWATER = 1.025  # t/m³


def _norm(s: object) -> str:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    return re.sub(r"[\s_().,/-]+", "", str(s)).lower()


def _find_value(df: pd.DataFrame, *aliases: str) -> float | None:
    """Look up a numeric value adjacent to a key cell that matches any alias."""
    targets = {_norm(a) for a in aliases}
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            if _norm(df.iat[r, c]) in targets:
                # walk right looking for the first numeric cell
                for cc in range(c + 1, df.shape[1]):
                    v = df.iat[r, cc]
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
    return None


def _find_displacement(df: pd.DataFrame) -> float | None:
    """Pull a cell like 'DISPLACEMENT – 125714.9T' if present."""
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            v = df.iat[r, c]
            if isinstance(v, str) and "displacement" in v.lower():
                m = re.search(r"([0-9]+(?:\.[0-9]+)?)", v)
                if m:
                    return float(m.group(1))
    return None


def _find_waterline_heights(df: pd.DataFrame) -> tuple[list[str], np.ndarray] | None:
    """Locate the top "WL height" table, return (labels, heights)."""
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            v = df.iat[r, c]
            if isinstance(v, str) and "wlheight" in _norm(v):
                # heights live to the right on the same row
                vals: list[float] = []
                for cc in range(c + 1, df.shape[1]):
                    try:
                        vals.append(float(df.iat[r, cc]))
                    except (TypeError, ValueError):
                        break
                # waterline labels live two rows above (where "Waterline" sits)
                # find row containing "Waterline"
                labels: list[str] = []
                for rr in range(max(0, r - 5), r):
                    for cc in range(c + 1, c + 1 + len(vals)):
                        cell = df.iat[rr, cc]
                        if cell is None:
                            labels = []
                            break
                        labels.append(str(cell))
                    if len(labels) == len(vals):
                        break
                    labels = []
                if len(vals) > 0:
                    if not labels:
                        labels = [chr(ord("A") + i) for i in range(len(vals))]
                    return labels, np.asarray(vals, dtype=float)
    return None


def _find_offsets_table(df: pd.DataFrame, wl_labels: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Find the offsets table by locating the row whose header contains
    the waterline labels A, B, … and a leading column header like
    'STN/WL'. Return (station_numbers, offsets_matrix) where the matrix
    is shape (n_stations, n_waterlines)."""
    nrows, ncols = df.shape
    for r in range(nrows):
        row_strs = [_norm(df.iat[r, c]) for c in range(ncols)]
        if any("stn" in s or "station" in s for s in row_strs):
            # find columns matching wl labels
            wl_cols: list[int] = []
            for label in wl_labels:
                for c in range(ncols):
                    if _norm(df.iat[r, c]) == _norm(label):
                        wl_cols.append(c)
                        break
            if len(wl_cols) == len(wl_labels):
                # station number column is the one with "stn"/"station"
                stn_col = next(c for c, s in enumerate(row_strs) if "stn" in s or "station" in s)
                # collect rows below until the data ends (first non-numeric station)
                stn_vals: list[float] = []
                offsets_rows: list[list[float]] = []
                for rr in range(r + 1, nrows):
                    try:
                        stn = float(df.iat[rr, stn_col])
                    except (TypeError, ValueError):
                        break
                    row_offsets: list[float] = []
                    for cc in wl_cols:
                        v = df.iat[rr, cc]
                        try:
                            row_offsets.append(float(v))
                        except (TypeError, ValueError):
                            row_offsets.append(0.0)
                    stn_vals.append(stn)
                    offsets_rows.append(row_offsets)
                return np.asarray(stn_vals, dtype=float), np.asarray(offsets_rows, dtype=float)
    raise ValueError("Could not locate the offsets table.")


# ---------------------------------------------------------------------
def load_bulk_carrier(
    path: str | Path,
    *,
    KG: float,
    rho: float = DEFAULT_RHO_SEAWATER,
    add_keel_row: bool = True,
) -> ShipInput:
    """Read the Bulk_Carrier-style workbook into a :class:`ShipInput`.

    Parameters
    ----------
    path : str | Path
        Workbook path.
    KG : float
        Vertical centre of gravity in metres (the workbook does not
        provide this; supply your own).
    rho : float, default 1.025
        Fluid density (t/m³).
    add_keel_row : bool, default True
        If the lowest waterline is above the baseline, prepend an
        extra waterline at z = 0 with the same half-breadths as the
        lowest provided waterline. This represents a flat-bottom
        approximation for the volume below the lowest tabulated WL.
    """
    book = openpyxl.load_workbook(path, data_only=True)
    sheet = book.worksheets[0]
    df = pd.DataFrame(sheet.values)

    # 1) Particulars -----------------------------------------------------
    LBP = _find_value(df, "LBP", "Length", "Length Between Perpendiculars")
    B   = _find_value(df, "B", "Beam", "Breadth")
    D   = _find_value(df, "D", "Depth")
    T   = _find_value(df, "T", "Draft", "Draught")
    CB_listed = _find_value(df, "C_B", "CB", "Block Coefficient")
    Delta_listed = _find_displacement(df)
    if None in (LBP, B, D, T):
        raise ValueError(f"Missing particulars: LBP={LBP}, B={B}, D={D}, T={T}")

    # 2) Waterline heights ----------------------------------------------
    found = _find_waterline_heights(df)
    if found is None:
        raise ValueError("Could not find a 'WL height' row.")
    wl_labels, wl_heights = found

    # 3) Offsets table ---------------------------------------------------
    stn_numbers, offsets = _find_offsets_table(df, wl_labels)
    # convert station numbers (0..10) to physical x. Convention: stn=0 at AP, stn=N_max at FP.
    stn_max = float(np.max(stn_numbers))
    if stn_max <= 0:
        raise ValueError("Could not infer station numbering from the table.")
    x = (stn_numbers - stn_numbers.min()) * (LBP / stn_max)

    # 4) Optional keel-row prepend --------------------------------------
    z = wl_heights.copy()
    if add_keel_row and z[0] > 1e-9:
        z = np.insert(z, 0, 0.0)
        # flat-bottom assumption: half-breadth at the keel = half-breadth at WL A
        offsets = np.column_stack([offsets[:, 0], offsets])

    hull = Hull(stations=x, waterlines=z, offsets=offsets)

    extra: dict[str, float] = {}
    if CB_listed is not None:
        extra["CB_listed"] = CB_listed
    if Delta_listed is not None:
        extra["Delta_listed"] = Delta_listed

    return ShipInput(
        hull=hull,
        LBP=float(LBP), B=float(B), D=float(D), T=float(T),
        rho=float(rho), KG=float(KG),
        extra=extra,
    )
