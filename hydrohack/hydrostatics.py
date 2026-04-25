"""Upright hydrostatics: displacement, waterplane, centres, BM/GM.

Everything in this module assumes the ship is floating upright at a
fixed draft ``T``. Sectional areas come from polygon clipping of each
station's cross-section against the horizontal plane ``z = T`` — this
handles arbitrary, non-uniform waterline spacing without resorting to
piecewise Simpson's. Length-wise integrals dispatch through
:func:`hydrohack.integration.integrate`, which picks Simpson uniform or
non-uniform Simpson based on the station grid.

Glossary
--------
* ∇ (V)   — displaced volume of the underwater hull
* Aw      — waterplane area at the design draft
* LCB     — longitudinal centre of buoyancy (centroid of underwater hull)
* LCF     — longitudinal centre of flotation (centroid of waterplane)
* KB      — vertical centre of buoyancy above keel
* IT, IL  — transverse / longitudinal moments of area of the waterplane
* BMt     — transverse metacentric radius (IT / ∇)
* BMl     — longitudinal metacentric radius (IL / ∇)
* GMt     — transverse metacentric height = KB + BMt − KG
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np

from .geometry import Hull
from .integration import integrate


# Acceleration due to gravity, m/s² (only used to express weight in kN).
G = 9.80665


# ---------------------------------------------------------------------
# Polygon utilities (kept here so this module is self-contained for
# upright work; the heeled solver re-uses these).
# ---------------------------------------------------------------------
def _polygon_area_centroid(poly: np.ndarray) -> tuple[float, float, float]:
    """Signed area and centroid (cx, cy) of a closed polygon by shoelace."""
    if poly.shape[0] < 3:
        return 0.0, 0.0, 0.0
    x = poly[:, 0]
    y = poly[:, 1]
    xn = np.roll(x, -1)
    yn = np.roll(y, -1)
    cross = x * yn - xn * y
    A = 0.5 * cross.sum()
    if abs(A) < 1e-15:
        return 0.0, 0.0, 0.0
    cx = ((x + xn) * cross).sum() / (6.0 * A)
    cy = ((y + yn) * cross).sum() / (6.0 * A)
    return float(A), float(cx), float(cy)


def _clip_below(poly: np.ndarray, z_cut: float) -> np.ndarray:
    """Sutherland-Hodgman half-plane clip to ``z ≤ z_cut``."""
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


# ---------------------------------------------------------------------
# Per-station underwater area + first vertical moment (polygon based)
# ---------------------------------------------------------------------
def _section_area_and_zcentroid(hull: Hull, station_idx: int, draft: float) -> tuple[float, float]:
    """Return (A, z̄) of the underwater section at one station.

    Builds the full station polygon, clips below ``z = draft``, then
    runs shoelace. ``A`` is the absolute area and ``z̄`` is the height
    of the area centroid above the baseline.
    """
    poly = hull.section_polygon(station_idx)
    clipped = _clip_below(poly, draft)
    if clipped.shape[0] < 3:
        return 0.0, 0.0
    A, _cx, cy = _polygon_area_centroid(clipped)
    if A < 0:
        A = -A
    return A, cy


def sectional_areas(hull: Hull, draft: float) -> np.ndarray:
    """Cross-sectional area A(x_i) of the underwater hull at draft = T."""
    return np.array(
        [_section_area_and_zcentroid(hull, i, draft)[0] for i in range(hull.n_stations)],
        dtype=float,
    )


def vertical_moment_section(hull: Hull, draft: float) -> np.ndarray:
    """First moment about the baseline of each underwater section,
    ``M_z(x_i) = ∫ z·b(z) dz`` (used for KB)."""
    out = np.zeros(hull.n_stations)
    for i in range(hull.n_stations):
        A, zc = _section_area_and_zcentroid(hull, i, draft)
        out[i] = A * zc
    return out


def section_wetted_girth(hull: Hull, station_idx: int, draft: float) -> float:
    """Wetted girth of one section — arc length of the underwater
    polygon, **excluding** the horizontal chord at z = draft.

    Used for the wetted-surface integral ``Sw = ∫ girth(x) dx``.
    """
    poly = hull.section_polygon(station_idx)
    clipped = _clip_below(poly, draft)
    if clipped.shape[0] < 3:
        return 0.0
    girth = 0.0
    n = clipped.shape[0]
    for k in range(n):
        a = clipped[k]
        b = clipped[(k + 1) % n]
        # Skip the waterline chord (both endpoints at z = draft, within tolerance).
        if abs(a[1] - draft) < 1e-9 and abs(b[1] - draft) < 1e-9:
            continue
        dx = b[0] - a[0]
        dz = b[1] - a[1]
        girth += float(np.hypot(dx, dz))
    return girth


def wetted_girths(hull: Hull, draft: float) -> np.ndarray:
    """Wetted girth of each station section at ``draft``."""
    return np.array(
        [section_wetted_girth(hull, i, draft) for i in range(hull.n_stations)],
        dtype=float,
    )


@dataclass
class TrimResult:
    """Result of a trim balance solve."""
    T_mean: float       # mean draft at midship pivot, m
    theta_rad: float    # trim angle (positive = bow-down), radians
    theta_deg: float    # ditto in degrees
    trim_m: float       # T_fwd − T_aft over the LBP, metres (positive = bow-down)
    T_aft: float        # draft at the aft perpendicular
    T_fwd: float        # draft at the forward perpendicular
    V: float            # displaced volume at the trimmed waterline
    LCB: float          # longitudinal centre of buoyancy at trimmed eqm
    LCG: float          # supplied LCG (must equal LCB at convergence)
    converged: bool


def _trimmed_section_area(hull: Hull, station_idx: int,
                          T_m: float, theta_rad: float, x_pivot: float,
                          poly: np.ndarray | None = None) -> float:
    """Sectional area of one station with a trimmed waterline.

    Local waterline at this station is ``z_local = T_m + (x − x_pivot)·tan θ``.
    Caller may supply the precomputed body-frame polygon to avoid rebuilding
    it inside an iterative loop.
    """
    if poly is None:
        poly = hull.section_polygon(station_idx)
    x = float(hull.stations[station_idx])
    z_local = T_m + (x - x_pivot) * np.tan(theta_rad)
    clipped = _clip_below(poly, float(z_local))
    if clipped.shape[0] < 3:
        return 0.0
    A, _, _ = _polygon_area_centroid(clipped)
    return abs(float(A))


def _trimmed_volume_lcb(hull: Hull, T_m: float, theta_rad: float, x_pivot: float,
                        polys: list[np.ndarray]) -> tuple[float, float]:
    A_arr = np.array([
        _trimmed_section_area(hull, i, T_m, theta_rad, x_pivot, polys[i])
        for i in range(hull.n_stations)
    ])
    V = float(integrate(hull.stations, A_arr))
    if V <= 0:
        return 0.0, 0.0
    LCB = float(integrate(hull.stations, hull.stations * A_arr) / V)
    return V, LCB


def trim_balance(
    hull: Hull, rho: float, displacement: float, LCG: float,
    LBP: float | None = None,
    theta_max_deg: float = 8.0,
    tol_LCB_m: float = 0.01,
    tol_V_rel: float = 1e-5,
    max_outer_iter: int = 60,
) -> TrimResult:
    """Solve for the equilibrium trim and mean-draft.

    Conditions imposed
    ------------------
    1. Volume balance:   ρ · ∇(T_m, θ) = displacement
    2. Moment balance:   LCB(T_m, θ) = LCG

    The pivot for the trim rotation is taken at midship (LBP/2) — its choice
    only affects the parameterisation of (T_m, θ); the resulting waterline
    plane is identical for any pivot.

    Algorithm
    ---------
    Outer bisection on θ; for each candidate θ, an inner bisection finds
    the T_m that satisfies the volume balance, and the resulting LCB is
    returned. Bracket on θ is auto-expanded if the initial range does
    not contain a sign change of ``LCB − LCG``.
    """
    if LBP is None:
        LBP = float(hull.LBP)
    x_pivot = float(hull.stations[0]) + 0.5 * LBP
    V_target = float(displacement) / float(rho)
    polys = [hull.section_polygon(i) for i in range(hull.n_stations)]
    z_max = float(hull.waterlines[-1])

    def solve_T_for_theta(theta_rad: float) -> tuple[float, float, float]:
        """Bisect T_m for given θ to match V_target. Returns (T_m, V, LCB)."""
        T_lo, T_hi = 0.0, z_max
        for _ in range(80):
            T_m = 0.5 * (T_lo + T_hi)
            V, LCB = _trimmed_volume_lcb(hull, T_m, theta_rad, x_pivot, polys)
            if V < V_target:
                T_lo = T_m
            else:
                T_hi = T_m
            if T_hi - T_lo < 1e-7 * max(z_max, 1.0):
                break
            if V > 0 and abs(V - V_target) < tol_V_rel * V_target:
                break
        T_m = 0.5 * (T_lo + T_hi)
        V, LCB = _trimmed_volume_lcb(hull, T_m, theta_rad, x_pivot, polys)
        return T_m, V, LCB

    theta_lo = -np.deg2rad(theta_max_deg)
    theta_hi = +np.deg2rad(theta_max_deg)
    T_lo, V_lo, LCB_lo = solve_T_for_theta(theta_lo)
    T_hi, V_hi, LCB_hi = solve_T_for_theta(theta_hi)
    f_lo = LCB_lo - LCG
    f_hi = LCB_hi - LCG

    expansions = 0
    while np.sign(f_lo) == np.sign(f_hi) and expansions < 4:
        theta_lo *= 1.6
        theta_hi *= 1.6
        T_lo, V_lo, LCB_lo = solve_T_for_theta(theta_lo)
        T_hi, V_hi, LCB_hi = solve_T_for_theta(theta_hi)
        f_lo = LCB_lo - LCG
        f_hi = LCB_hi - LCG
        expansions += 1

    converged = False
    if np.sign(f_lo) == np.sign(f_hi):
        # No sign change — pick the side with the smaller |f| as a best-effort answer.
        if abs(f_lo) < abs(f_hi):
            theta = theta_lo; T_m = T_lo; V = V_lo; LCB = LCB_lo
        else:
            theta = theta_hi; T_m = T_hi; V = V_hi; LCB = LCB_hi
    else:
        theta = 0.5 * (theta_lo + theta_hi)
        for _ in range(max_outer_iter):
            theta = 0.5 * (theta_lo + theta_hi)
            T_m, V, LCB = solve_T_for_theta(theta)
            f_mid = LCB - LCG
            if abs(f_mid) < tol_LCB_m:
                converged = True
                break
            if np.sign(f_mid) == np.sign(f_lo):
                theta_lo = theta; f_lo = f_mid
            else:
                theta_hi = theta; f_hi = f_mid
        T_m, V, LCB = solve_T_for_theta(theta)

    # Convert to engineering quantities
    half_LBP = 0.5 * LBP
    T_aft = T_m + (float(hull.stations[0]) - x_pivot) * np.tan(theta)
    T_fwd = T_m + (float(hull.stations[-1]) - x_pivot) * np.tan(theta)
    trim_m = T_fwd - T_aft   # positive = bow-down

    return TrimResult(
        T_mean=float(T_m),
        theta_rad=float(theta),
        theta_deg=float(np.rad2deg(theta)),
        trim_m=float(trim_m),
        T_aft=float(T_aft), T_fwd=float(T_fwd),
        V=float(V), LCB=float(LCB), LCG=float(LCG),
        converged=bool(converged),
    )


def bonjean_table(hull: Hull, drafts) -> np.ndarray:
    """Sectional area A(x_i, T) for each station and each draft in ``drafts``.

    Returns an array of shape ``(n_drafts, n_stations)``. Each station's
    polygon is built once and clipped at every requested draft.
    """
    drafts = np.asarray(drafts, dtype=float)
    polys = [hull.section_polygon(i) for i in range(hull.n_stations)]
    out = np.zeros((drafts.size, hull.n_stations))
    for k, T in enumerate(drafts):
        for i, poly in enumerate(polys):
            clipped = _clip_below(poly, float(T))
            if clipped.shape[0] < 3:
                continue
            A, _, _ = _polygon_area_centroid(clipped)
            out[k, i] = abs(A)
    return out


# ---------------------------------------------------------------------
# Waterplane integrals
# ---------------------------------------------------------------------
def waterplane(hull: Hull, draft: float) -> dict[str, float]:
    """Compute waterplane area and its longitudinal/transverse moments.

    The waterplane half-breadth is ``b(x) = y(x, z=T)``. Length-wise
    integrals dispatch on whether the station grid is uniform.

    * Aw  = 2 ∫ b(x) dx                           — waterplane area
    * Mx  = 2 ∫ x · b(x) dx                       — first moment about x=0
    * IT  = (2/3) ∫ b(x)³ dx                      — transverse MoI about CL
    * Ixx = 2 ∫ x² · b(x) dx                      — long. MoI about x=0
    """
    x = hull.stations
    b = hull.waterline_breadths(draft)

    Aw = 2.0 * integrate(x, b)
    Mx = 2.0 * integrate(x, x * b)
    LCF = Mx / Aw if Aw > 0 else 0.0

    IT = (2.0 / 3.0) * integrate(x, b**3)
    Ixx_about_origin = 2.0 * integrate(x, x**2 * b)
    # parallel-axis shift to LCF
    IL = Ixx_about_origin - Aw * LCF**2
    return {"Aw": float(Aw), "LCF": float(LCF), "IT": float(IT), "IL": float(IL)}


# ---------------------------------------------------------------------
# Aggregated upright result
# ---------------------------------------------------------------------
@dataclass
class UprightResult:
    """Upright hydrostatic results at a single draft."""

    T: float
    rho: float
    KG: float

    V: float            # displaced volume ∇ [m³]
    Delta: float        # mass displacement ρ·∇ [tonnes if ρ in t/m³, kg if kg/m³]
    W: float            # weight ρ·∇·g

    Aw: float           # waterplane area [m²]
    LCB: float          # longitudinal centre of buoyancy [m, from origin]
    LCF: float          # longitudinal centre of flotation [m, from origin]
    KB: float           # vertical centre of buoyancy [m]

    IT: float           # transverse MoI of waterplane about CL [m⁴]
    IL: float           # longitudinal MoI of waterplane about LCF [m⁴]

    BMt: float          # transverse metacentric radius [m]
    BMl: float          # longitudinal metacentric radius [m]
    KMt: float          # KB + BMt
    KMl: float          # KB + BMl
    GMt: float          # KMt − KG
    GMl: float          # KMl − KG

    CB:  float          # block coefficient ∇ / (LBP·B·T)
    CWP: float          # waterplane coefficient Aw / (LBP·B)
    CM:  float          # midship (max) section coefficient (A_max) / (B·T)
    Cp:  float          # prismatic coefficient ∇ / (A_max · LBP)

    Sw:  float          # wetted-surface area  ∫ girth(x) dx       [m²]

    def as_dict(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def compute_upright_sweep(
    hull: Hull, drafts, rho: float, KG: float,
    LBP: float | None = None, B: float | None = None,
) -> list["UprightResult"]:
    """Run :func:`compute_upright` at every draft in ``drafts``.

    Drafts above the topmost waterline are clamped to ``hull.waterlines[-1]``
    (no offset data exists above the moulded depth — the hull is taken as
    fully submerged from there on). A draft of 0 returns a zero-filled
    result rather than dividing by zero.
    """
    z_max = float(hull.waterlines[-1])
    out: list[UprightResult] = []
    for T in drafts:
        T_clamped = max(0.0, min(float(T), z_max))
        if T_clamped <= 0.0:
            # avoid the zero-volume division branch — emit a zero record
            out.append(UprightResult(
                T=float(T), rho=float(rho), KG=float(KG),
                V=0.0, Delta=0.0, W=0.0,
                Aw=0.0, LCB=0.0, LCF=0.0, KB=0.0,
                IT=0.0, IL=0.0,
                BMt=0.0, BMl=0.0, KMt=0.0, KMl=0.0,
                GMt=-float(KG), GMl=-float(KG),
                CB=0.0, CWP=0.0, CM=0.0, Cp=0.0,
                Sw=0.0,
            ))
            continue
        up = compute_upright(hull, T_clamped, rho, KG, LBP=LBP, B=B)
        # restore the *requested* draft on the record so the row labels read cleanly
        up.T = float(T)
        out.append(up)
    return out


def compute_upright(
    hull: Hull, draft: float, rho: float, KG: float,
    LBP: float | None = None, B: float | None = None,
) -> UprightResult:
    """Run the full upright hydrostatic computation at one draft."""
    x = hull.stations
    A = sectional_areas(hull, draft)
    Mz_section = vertical_moment_section(hull, draft)

    # Volume integrals along the length (auto-picks uniform vs non-uniform Simpson)
    V = integrate(x, A)
    Mx_vol = integrate(x, x * A)
    LCB = Mx_vol / V if V > 0 else 0.0
    KB = integrate(x, Mz_section) / V if V > 0 else 0.0

    wp = waterplane(hull, draft)
    Aw, LCF, IT, IL = wp["Aw"], wp["LCF"], wp["IT"], wp["IL"]

    BMt = IT / V if V > 0 else 0.0
    BMl = IL / V if V > 0 else 0.0
    KMt = KB + BMt
    KMl = KB + BMl
    GMt = KMt - KG
    GMl = KMl - KG

    LBP = float(LBP if LBP is not None else hull.LBP)
    B   = float(B   if B   is not None else 2.0 * float(np.max(hull.offsets)))
    A_max = float(A.max()) if A.size else 0.0
    CB  = V / (LBP * B * draft)        if (LBP > 0 and B > 0 and draft > 0) else 0.0
    CWP = Aw / (LBP * B)               if (LBP > 0 and B > 0) else 0.0
    CM  = A_max / (B * draft)          if (B > 0 and draft > 0) else 0.0
    Cp  = V / (A_max * LBP)            if (A_max > 0 and LBP > 0) else 0.0

    # Wetted surface = ∫ underwater-section girth dx
    girths = wetted_girths(hull, draft)
    Sw = float(integrate(x, girths))

    return UprightResult(
        T=float(draft), rho=float(rho), KG=float(KG),
        V=float(V), Delta=float(rho * V), W=float(rho * V * G),
        Aw=float(Aw), LCB=float(LCB), LCF=float(LCF), KB=float(KB),
        IT=float(IT), IL=float(IL),
        BMt=float(BMt), BMl=float(BMl),
        KMt=float(KMt), KMl=float(KMl),
        GMt=float(GMt), GMl=float(GMl),
        CB=float(CB), CWP=float(CWP), CM=float(CM), Cp=float(Cp),
        Sw=float(Sw),
    )
