"""Excel I/O for ship offset data.

The competition rulebook does not commit to a fixed schema — it only
states the file will contain offsets, station/waterline spacing, main
particulars, draft, density, and KG. The reader here is therefore
*flexible*: it scans every sheet, looks up a list of synonymous keys for
the scalar particulars, and finds the largest contiguous numeric block
to use as the offset matrix.

After the real input file is released, ``ShipInput`` may be tightened or
the auto-detection bypassed via :func:`load_explicit`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import openpyxl

from .geometry import Hull


# Aliases for the scalar particulars we expect on a "Particulars" sheet.
# Lower-cased; whitespace and underscores are stripped before matching.
KEY_ALIASES: dict[str, list[str]] = {
    "LBP":              ["lbp", "lengthbetweenperpendiculars", "length", "l"],
    "B":                ["b", "beam", "breadth", "moldedbreadth", "breadthmolded"],
    "D":                ["d", "depth", "moldeddepth", "depthmolded"],
    "T":                ["t", "draft", "draught", "designdraft", "designdraught", "specifieddraft"],
    "rho":              ["rho", "density", "waterdensity", "fluiddensity", "ρ"],
    "KG":               ["kg", "verticalcenterofgravity", "vcg"],
    "station_spacing":  ["stationspacing", "δx", "deltax", "dx", "spacingstation"],
    "waterline_spacing":["waterlinespacing", "δz", "deltaz", "dz", "spacingwaterline", "wlspacing"],
    "n_stations":       ["nstations", "numberofstations", "numstations"],
    "n_waterlines":     ["nwaterlines", "numberofwaterlines", "numwaterlines"],
    "LCG":              ["lcg", "longitudinalcenterofgravity"],
}


def _norm_key(s: object) -> str:
    """Normalize a cell value for fuzzy key matching."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    return re.sub(r"[\s_().,/-]+", "", str(s)).lower()


@dataclass
class ShipInput:
    """All parameters extracted from the input workbook."""

    hull: Hull
    LBP: float
    B: float
    D: float
    T: float
    rho: float
    KG: float
    LCG: float | None = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------
# Particulars discovery
# ---------------------------------------------------------------------
def _scan_particulars(book: openpyxl.Workbook) -> dict[str, float]:
    """Walk every sheet looking for ``key | value`` pairs that match
    :data:`KEY_ALIASES`. Returns a dict of canonical-key → float."""
    found: dict[str, float] = {}
    for sheet in book.worksheets:
        for row in sheet.iter_rows(values_only=True):
            cells = list(row)
            for i, cell in enumerate(cells):
                key = _norm_key(cell)
                if not key:
                    continue
                for canonical, aliases in KEY_ALIASES.items():
                    if canonical in found:
                        continue
                    if key in aliases:
                        # take the next non-blank numeric cell on the same row
                        for v in cells[i + 1:]:
                            try:
                                fv = float(v)
                                found[canonical] = fv
                                break
                            except (TypeError, ValueError):
                                continue
                        break
    return found


# ---------------------------------------------------------------------
# Offset-matrix discovery
# ---------------------------------------------------------------------
def _largest_numeric_block(df: pd.DataFrame) -> tuple[int, int, int, int] | None:
    """Find the bounding box (r0, c0, r1, c1) of the largest rectangular
    block of finite numbers in ``df``. Returns ``None`` if no block of
    size ≥ 3×3 is present."""
    arr = df.to_numpy()
    nrows, ncols = arr.shape
    is_num = np.zeros_like(arr, dtype=bool)
    for r in range(nrows):
        for c in range(ncols):
            try:
                v = float(arr[r, c])
                is_num[r, c] = np.isfinite(v)
            except (TypeError, ValueError):
                is_num[r, c] = False

    best: tuple[int, tuple[int, int, int, int]] | None = None
    visited = np.zeros_like(is_num, dtype=bool)
    for r in range(nrows):
        for c in range(ncols):
            if not is_num[r, c] or visited[r, c]:
                continue
            # find largest rectangle starting at (r, c) by greedy expansion
            r1 = r
            while r1 + 1 < nrows and is_num[r1 + 1, c:].all() and (r1 + 1 - r) < nrows:
                # break when row no longer fully numeric across same column span
                if not is_num[r1 + 1, c]:
                    break
                r1 += 1
            c1 = c
            while c1 + 1 < ncols and is_num[r : r1 + 1, c1 + 1].all():
                c1 += 1
            # shrink rows so all of [r..r1] are fully numeric across [c..c1]
            while r1 > r and not is_num[r : r1 + 1, c : c1 + 1].all():
                r1 -= 1
            block = (r, c, r1, c1)
            area = (r1 - r + 1) * (c1 - c + 1)
            if area >= 9 and (best is None or area > best[0]):
                best = (area, block)
            visited[r : r1 + 1, c : c1 + 1] = True
    return best[1] if best else None


def _try_extract_axis(values: Iterable, expected_n: int) -> np.ndarray | None:
    """Try to interpret a row/column as a numeric axis (station x or waterline z)."""
    arr = []
    for v in values:
        try:
            arr.append(float(v))
        except (TypeError, ValueError):
            return None
    arr = np.asarray(arr, dtype=float)
    if arr.size == expected_n and np.all(np.isfinite(arr)):
        return arr
    return None


def _looks_like_axis(values: np.ndarray) -> bool:
    """An axis label row/column is monotone (typically increasing) and
    its first value is often 0. Used to decide whether to peel off the
    first row/column of an auto-detected numeric block."""
    if values.size < 2 or not np.all(np.isfinite(values)):
        return False
    diffs = np.diff(values)
    monotone = np.all(diffs > 0) or np.all(diffs < 0)
    # near-uniform spacing tightens the heuristic for offsets tables
    if monotone and np.std(diffs) < 1e-6 + 1e-3 * abs(np.mean(diffs)):
        return True
    return monotone


_OFFSET_SHEET_HINTS = ("offset", "lines", "breadth", "halfbreadth", "table")


def _scan_offsets(book: openpyxl.Workbook) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Locate the offset matrix and (best-effort) its axis labels.

    Strategy
    --------
    1. Prefer sheets whose name contains an offsets-related hint.
    2. On the chosen sheet, find the largest contiguous numeric block.
    3. If that block's first row OR first column looks like a monotone
       axis (constant spacing, starts near 0), peel it off as a header.
    4. Otherwise look one cell above/left of the block.
    """
    candidate_sheets = [s for s in book.worksheets
                        if any(h in s.title.lower() for h in _OFFSET_SHEET_HINTS)]
    if not candidate_sheets:
        candidate_sheets = list(book.worksheets)

    best: tuple[int, np.ndarray, np.ndarray | None, np.ndarray | None] | None = None
    for sheet in candidate_sheets:
        df = pd.DataFrame(sheet.values)
        if df.empty:
            continue
        bbox = _largest_numeric_block(df)
        if bbox is None:
            continue
        r0, c0, r1, c1 = bbox
        block = df.iloc[r0 : r1 + 1, c0 : c1 + 1].to_numpy(dtype=float)

        row_hdr = None
        col_hdr = None
        # how many leading rows/cols were peeled from the detected block —
        # used to align the original-sheet adjacent cells for fallback axis lookup.
        row_peel = 0
        col_peel = 0

        # 3a. peel first column if it looks like an axis
        if block.shape[1] > 1 and _looks_like_axis(block[:, 0]):
            row_hdr = block[:, 0].copy()
            block = block[:, 1:]
            col_peel = 1
        # 3b. peel first row if it looks like an axis
        if block.shape[0] > 1 and _looks_like_axis(block[0, :]):
            col_hdr = block[0, :].copy()
            block = block[1:, :]
            row_peel = 1
            # if we just peeled both, the corner element of row_hdr is junk.
            if row_hdr is not None and row_hdr.size == block.shape[0] + 1:
                row_hdr = row_hdr[1:]

        # 4. fallback: look at the cell immediately above/left of the (possibly peeled) block
        body_r0 = r0 + row_peel
        body_c0 = c0 + col_peel
        if row_hdr is None and body_c0 > 0:
            row_hdr = _try_extract_axis(
                df.iloc[body_r0 : body_r0 + block.shape[0], body_c0 - 1],
                block.shape[0],
            )
        if col_hdr is None and body_r0 > 0:
            col_hdr = _try_extract_axis(
                df.iloc[body_r0 - 1, body_c0 : body_c0 + block.shape[1]],
                block.shape[1],
            )
        # also try the row/col above/left of the *original* (unpeeled) block in case
        # the axis labels live there even though we already peeled in-block headers.
        if col_hdr is None and r0 > 0:
            col_hdr = _try_extract_axis(
                df.iloc[r0 - 1, body_c0 : body_c0 + block.shape[1]],
                block.shape[1],
            )
        if row_hdr is None and c0 > 0:
            row_hdr = _try_extract_axis(
                df.iloc[body_r0 : body_r0 + block.shape[0], c0 - 1],
                block.shape[0],
            )

        size = block.size
        if best is None or size > best[0]:
            best = (size, block, row_hdr, col_hdr)
    if best is None:
        raise ValueError("No offset matrix could be located in the workbook.")
    _, offsets, row_hdr, col_hdr = best
    return offsets, row_hdr, col_hdr


# ---------------------------------------------------------------------
# Top-level loader
# ---------------------------------------------------------------------
def load_workbook(path: str | Path) -> ShipInput:
    """Read an offset-table workbook and return :class:`ShipInput`.

    Auto-detects the offset matrix and main particulars. The orientation
    of the matrix (stations × waterlines vs. waterlines × stations) is
    inferred from the axis sizes — whichever dimension matches a
    "station spacing" header gets x.
    """
    book = openpyxl.load_workbook(path, data_only=True)
    pars = _scan_particulars(book)
    offsets, row_hdr, col_hdr = _scan_offsets(book)

    # Decide orientation: rows could be stations or waterlines.
    # Heuristic: there are usually MORE stations than waterlines on a
    # typical offsets table (e.g. 21 stations × 6–10 waterlines), so the
    # longer axis is x. If the user supplied n_stations explicitly use that.
    n_rows, n_cols = offsets.shape
    n_stations = int(pars.get("n_stations", 0)) or None
    n_waterlines = int(pars.get("n_waterlines", 0)) or None

    if n_stations and n_stations == n_rows:
        # rows = stations, cols = waterlines
        pass
    elif n_stations and n_stations == n_cols:
        offsets = offsets.T
    elif n_rows >= n_cols:
        # default: rows are stations
        pass
    else:
        offsets = offsets.T

    # Recover axis values
    if offsets.shape[0] == n_rows:
        x = row_hdr
        z = col_hdr
    else:
        x = col_hdr
        z = row_hdr

    n_x, n_z = offsets.shape

    # Fill missing axes from spacing
    if x is None:
        dx = pars.get("station_spacing")
        if dx is None and "LBP" in pars and n_x > 1:
            dx = pars["LBP"] / (n_x - 1)
        if dx is None:
            raise ValueError("Cannot determine station x-axis: provide 'station spacing' or 'LBP'.")
        x = dx * np.arange(n_x, dtype=float)
    if z is None:
        dz = pars.get("waterline_spacing")
        if dz is None and "D" in pars and n_z > 1:
            dz = pars["D"] / (n_z - 1)
        if dz is None:
            raise ValueError("Cannot determine waterline z-axis: provide 'waterline spacing' or 'D'.")
        z = dz * np.arange(n_z, dtype=float)

    hull = Hull(stations=x, waterlines=z, offsets=offsets)

    LBP = pars.get("LBP", float(x[-1] - x[0]))
    B = pars.get("B", 2.0 * float(np.max(offsets)))
    D = pars.get("D", float(z[-1] - z[0]))
    T = pars.get("T")
    rho = pars.get("rho")
    KG = pars.get("KG")
    LCG = pars.get("LCG")
    if T is None or rho is None or KG is None:
        missing = [k for k, v in [("T", T), ("rho", rho), ("KG", KG)] if v is None]
        raise ValueError(f"Missing required particulars: {missing}")

    return ShipInput(
        hull=hull,
        LBP=float(LBP),
        B=float(B),
        D=float(D),
        T=float(T),
        rho=float(rho),
        KG=float(KG),
        LCG=float(LCG) if LCG is not None else None,
        extra={k: v for k, v in pars.items() if k not in {"LBP","B","D","T","rho","KG","LCG"}},
    )


def load_explicit(
    offsets: np.ndarray,
    *,
    station_spacing: float,
    waterline_spacing: float,
    LBP: float,
    B: float,
    D: float,
    T: float,
    rho: float,
    KG: float,
    LCG: float | None = None,
) -> ShipInput:
    """Bypass discovery — construct ShipInput directly from explicit args."""
    n_x, n_z = offsets.shape
    x = station_spacing * np.arange(n_x, dtype=float)
    z = waterline_spacing * np.arange(n_z, dtype=float)
    hull = Hull(stations=x, waterlines=z, offsets=np.asarray(offsets, dtype=float))
    return ShipInput(hull=hull, LBP=LBP, B=B, D=D, T=T, rho=rho, KG=KG, LCG=LCG)
