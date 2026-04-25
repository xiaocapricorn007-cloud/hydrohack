"""Heeled stability: GZ and KN curves.

Approach (large-angle, fixed-trim)
----------------------------------
For each heel angle ``φ`` we work in the *world frame*: gravity is along
``−z_world``, the free surface is the horizontal plane ``z_world = T_h``,
and the hull is rotated about its keel-centerline by angle ``φ`` so the
starboard side dips. For each station the cross-section polygon is
rotated, then clipped below the free surface, and its area + centroid
are collected. Bisection on ``T_h`` enforces conservation of displaced
volume (= the upright ∇).

The transverse position of the buoyancy centroid ``y_B′`` is then the
arm from the keel-centerline to the line of buoyancy:

    KN(φ) = y_B′(φ)
    GZ(φ) = KN(φ) − KG · sin φ          (assuming LCG ≈ LCB; no trim)

Performance notes
-----------------
* Per heel angle, every station polygon is rotated **once** and then
  re-used by every bisection iteration on the equilibrium waterline.
* The bisection's final iteration returns the wetted section properties
  alongside the chosen waterline, so callers do not re-clip the same
  polygons a second time after the bisection converges.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
import numpy as np

from .geometry import Hull
from .integration import integrate


# ---------------------------------------------------------------------
# 2-D polygon utilities (closed polygons, vertices Nx2)
# ---------------------------------------------------------------------
def _polygon_area_centroid(poly: np.ndarray) -> tuple[float, float, float]:
    """Signed area and (cx, cy) centroid by the shoelace formula."""
    if poly.shape[0] < 3:
        return 0.0, 0.0, 0.0
    x = poly[:, 0]
    y = poly[:, 1]
    x_next = np.roll(x, -1)
    y_next = np.roll(y, -1)
    cross = x * y_next - x_next * y
    A = 0.5 * cross.sum()
    if abs(A) < 1e-15:
        return 0.0, 0.0, 0.0
    cx = ((x + x_next) * cross).sum() / (6.0 * A)
    cy = ((y + y_next) * cross).sum() / (6.0 * A)
    return float(A), float(cx), float(cy)


def _clip_below(poly: np.ndarray, z_cut: float) -> np.ndarray:
    """Clip a closed polygon to the half-plane ``z ≤ z_cut`` (Sutherland-Hodgman)."""
    if poly.shape[0] == 0:
        return poly
    out: list[tuple[float, float]] = []
    n = poly.shape[0]
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        a_in = a[1] <= z_cut
        b_in = b[1] <= z_cut
        if a_in:
            out.append((float(a[0]), float(a[1])))
            if not b_in:
                t = (z_cut - a[1]) / (b[1] - a[1])
                out.append((float(a[0] + t * (b[0] - a[0])), float(z_cut)))
        else:
            if b_in:
                t = (z_cut - a[1]) / (b[1] - a[1])
                out.append((float(a[0] + t * (b[0] - a[0])), float(z_cut)))
    return np.asarray(out, dtype=float) if out else np.empty((0, 2), dtype=float)


def _rotate_polys(polys: list[np.ndarray], phi_rad: float) -> list[np.ndarray]:
    """Rotate every polygon (in y–z plane) by angle ``φ`` about the origin.

    Positive ``φ`` heels the ship to starboard (the +y deck-edge dips).
    Rotation matrix in world frame:

        [y']   [ cosφ   sinφ ] [y]
        [z'] = [-sinφ   cosφ ] [z]
    """
    c, s = np.cos(phi_rad), np.sin(phi_rad)
    R = np.array([[c, s], [-s, c]])
    return [p @ R.T for p in polys]


# ---------------------------------------------------------------------
# Wetted-section props at a candidate waterline
# ---------------------------------------------------------------------
def _wetted_section_props(
    polys_world: list[np.ndarray],
    z_cut: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ``(areas, y_centroids, z_centroids)`` per station for clip below ``z_cut``."""
    n = len(polys_world)
    areas = np.zeros(n)
    yc = np.zeros(n)
    zc = np.zeros(n)
    for i, poly in enumerate(polys_world):
        clipped = _clip_below(poly, z_cut)
        if clipped.shape[0] < 3:
            continue
        A, cx, cy = _polygon_area_centroid(clipped)
        if A < 0:
            A = -A
        areas[i] = A
        yc[i] = cx
        zc[i] = cy
    return areas, yc, zc


def _equilibrium_waterline(
    polys_world: list[np.ndarray],
    x_stations: np.ndarray,
    target_volume: float,
    z_min: float,
    z_max: float,
    tol: float = 1e-8,
    max_iter: int = 80,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """Bisect on the world-frame waterline height to match displaced volume.

    Returns ``(T_h, areas, yc, zc)`` — the chosen waterline plus the
    wetted section properties at it, so the caller does not re-clip.
    """
    lo, hi = z_min, z_max
    A_lo, _, _ = _wetted_section_props(polys_world, lo)
    A_hi, yc_hi, zc_hi = _wetted_section_props(polys_world, hi)
    V_lo = float(integrate(x_stations, A_lo))
    V_hi = float(integrate(x_stations, A_hi))
    if V_hi <= target_volume:
        return float(hi), A_hi, yc_hi, zc_hi
    if V_lo >= target_volume:
        A0, yc0, zc0 = _wetted_section_props(polys_world, lo)
        return float(lo), A0, yc0, zc0

    A_mid = yc_mid = zc_mid = None
    mid = 0.5 * (lo + hi)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        A_mid, yc_mid, zc_mid = _wetted_section_props(polys_world, mid)
        V_mid = float(integrate(x_stations, A_mid))
        if abs(V_mid - target_volume) < tol * max(target_volume, 1.0):
            return float(mid), A_mid, yc_mid, zc_mid
        if V_mid < target_volume:
            lo = mid
        else:
            hi = mid
    return float(mid), A_mid, yc_mid, zc_mid


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
@dataclass
class StabilityCurve:
    phi_deg: np.ndarray   # heel angles, degrees
    KN: np.ndarray        # cross-curve arm from K, m
    GZ: np.ndarray        # righting arm, m
    T_world: np.ndarray   # equilibrium waterline (world frame), m
    yB: np.ndarray        # transverse buoyancy centroid (world frame), m
    zB: np.ndarray        # vertical buoyancy centroid (world frame), m


def gz_curve(
    hull: Hull,
    draft_upright: float,
    rho: float,
    KG: float,
    angles_deg: Sequence[float] | None = None,
) -> StabilityCurve:
    """Compute the GZ and KN curves over a list of heel angles.

    The displaced volume is held equal to the upright value at
    ``draft_upright``. Trim is held fixed (longitudinal balance is not
    iterated — we assume LCG = LCB, i.e. zero trim).
    """
    from .hydrostatics import sectional_areas
    if angles_deg is None:
        angles_deg = np.arange(0.0, 91.0, 5.0)
    angles_deg = np.asarray(angles_deg, dtype=float)

    # 1) Compute the upright displaced volume — this is conserved.
    A_up = sectional_areas(hull, draft_upright)
    V_target = float(integrate(hull.stations, A_up))

    # 2) Build all station polygons in body frame once.
    body_polys = [hull.section_polygon(i) for i in range(hull.n_stations)]

    n = angles_deg.size
    KN = np.zeros(n)
    GZ = np.zeros(n)
    T_world = np.zeros(n)
    yB = np.zeros(n)
    zB = np.zeros(n)

    for k, phi_deg in enumerate(angles_deg):
        phi = np.deg2rad(float(phi_deg))
        polys_w = _rotate_polys(body_polys, phi)

        # Bracket: after rotation, the world-frame z range can extend on both sides.
        all_z_w = np.concatenate([p[:, 1] for p in polys_w])
        z_lo = float(all_z_w.min()) - 1e-6
        z_hi = float(all_z_w.max()) + 1e-6

        T_w, A_w, yc_w, zc_w = _equilibrium_waterline(
            polys_w, hull.stations, V_target, z_lo, z_hi,
        )
        T_world[k] = T_w

        V_check = float(integrate(hull.stations, A_w))
        if V_check <= 0:
            continue
        yB[k] = float(integrate(hull.stations, A_w * yc_w) / V_check)
        zB[k] = float(integrate(hull.stations, A_w * zc_w) / V_check)
        KN[k] = yB[k]
        GZ[k] = yB[k] - KG * np.sin(phi)

    return StabilityCurve(
        phi_deg=angles_deg,
        KN=KN, GZ=GZ, T_world=T_world, yB=yB, zB=zB,
    )


# ---------------------------------------------------------------------
# Cross curves of stability — KN(φ) for a family of displacements
# ---------------------------------------------------------------------
def cross_curves_kn(
    hull: Hull,
    drafts: Sequence[float],
    rho: float,
    angles_deg: Sequence[float] | None = None,
) -> dict[str, np.ndarray]:
    """Compute cross-curves of stability KN(φ) at several drafts.

    Returns
    -------
    dict with keys:

        "drafts"     — (n_T,)        the input draft grid
        "displacements" — (n_T,)     ρ · ∇ at each draft
        "phi_deg"    — (n_phi,)      heel-angle grid
        "KN"         — (n_T, n_phi)  KN values

    KN is independent of KG, so this function does not need a KG input
    (we pass KG = 0 to the inner GZ solver and read the KN array out).
    """
    if angles_deg is None:
        angles_deg = np.arange(0.0, 91.0, 5.0)
    angles_deg = np.asarray(angles_deg, dtype=float)
    drafts = np.asarray(drafts, dtype=float)

    KN = np.zeros((drafts.size, angles_deg.size))
    Vs = np.zeros(drafts.size)
    Deltas = np.zeros(drafts.size)
    for k, T in enumerate(drafts):
        from .hydrostatics import sectional_areas
        A_up = sectional_areas(hull, float(T))
        Vs[k] = float(integrate(hull.stations, A_up))
        Deltas[k] = float(rho * Vs[k])
        # KG = 0 ⇒ GZ equals KN, so we can grab KN directly
        curve = gz_curve(hull, draft_upright=float(T), rho=rho, KG=0.0,
                         angles_deg=angles_deg)
        KN[k, :] = curve.KN

    return {
        "drafts": drafts, "displacements": Deltas, "volumes": Vs,
        "phi_deg": angles_deg, "KN": KN,
    }
