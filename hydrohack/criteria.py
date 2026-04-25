"""Intact-stability criteria — IS-Code (IMO A.749(18) Ch. 3.1.2.1).

For each criterion we report the threshold, the actual computed value,
and a pass/fail flag. Areas under the GZ curve are computed by
trapezoidal integration on the supplied heel-angle grid (radians).
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np

from .stability import StabilityCurve


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def area_under_gz(curve: StabilityCurve, phi_lo_deg: float, phi_hi_deg: float) -> float:
    """∫ GZ dφ between two angles (in radians on the integration). The
    result has units of m·rad — the conventional unit for IMO areas."""
    mask = (curve.phi_deg >= phi_lo_deg - 1e-9) & (curve.phi_deg <= phi_hi_deg + 1e-9)
    phi = np.deg2rad(curve.phi_deg[mask])
    gz = curve.GZ[mask]
    if phi.size < 2:
        return 0.0
    return float(np.trapezoid(gz, phi))


def vanishing_angle(curve: StabilityCurve) -> float | None:
    """Heel angle at which the GZ curve first returns to zero after its
    peak. Linear interpolation between the bracketing samples."""
    GZ = curve.GZ
    phi = curve.phi_deg
    if GZ.size < 2 or GZ.max() <= 0:
        return None
    i_peak = int(np.argmax(GZ))
    for i in range(i_peak, GZ.size - 1):
        if GZ[i] >= 0 >= GZ[i + 1]:
            t = GZ[i] / (GZ[i] - GZ[i + 1])
            return float(phi[i] + t * (phi[i + 1] - phi[i]))
    return None


def angle_of_max_gz(curve: StabilityCurve) -> tuple[float, float]:
    """Return ``(phi_max, GZ_max)``."""
    if curve.GZ.size == 0:
        return 0.0, 0.0
    i = int(np.argmax(curve.GZ))
    return float(curve.phi_deg[i]), float(curve.GZ[i])


# ---------------------------------------------------------------------
# Criterion records
# ---------------------------------------------------------------------
@dataclass
class Criterion:
    name: str
    threshold: float
    value: float
    units: str
    passed: bool
    note: str = ""


def evaluate_criteria(curve: StabilityCurve, GMt: float) -> list[Criterion]:
    """Apply the standard IS-Code intact-stability criteria.

    Reference thresholds (IMO A.749(18), Ch. 3, "Intact Stability Code"):

    * GMt ≥ 0.15 m
    * GZ_max ≥ 0.20 m, attained at φ ≥ 30°
    * Vanishing angle ≥ 60°  (range of positive righting arm)
    * Area under GZ from 0–30° ≥ 0.055 m·rad
    * Area under GZ from 0–40° ≥ 0.090 m·rad
    * Area under GZ between 30–40° ≥ 0.030 m·rad
    """
    phi_max, gz_max = angle_of_max_gz(curve)
    vanishing = vanishing_angle(curve)

    A_30 = area_under_gz(curve, 0.0, 30.0)
    A_40 = area_under_gz(curve, 0.0, 40.0)
    A_30_40 = area_under_gz(curve, 30.0, 40.0)

    out: list[Criterion] = [
        Criterion(
            name="Initial GMt",
            threshold=0.15, value=GMt, units="m",
            passed=GMt >= 0.15,
            note="transverse metacentric height at zero heel",
        ),
        Criterion(
            name="Maximum GZ",
            threshold=0.20, value=gz_max, units="m",
            passed=gz_max >= 0.20,
            note=f"peak at φ = {phi_max:.1f}°  (must be ≥ 30°)",
        ),
        Criterion(
            name="Angle of max GZ",
            threshold=30.0, value=phi_max, units="deg",
            passed=phi_max >= 30.0,
            note="heel at which the righting arm peaks",
        ),
        Criterion(
            name="Vanishing angle",
            threshold=60.0, value=vanishing if vanishing is not None else max(curve.phi_deg) if curve.phi_deg.size else 0.0,
            units="deg",
            passed=(vanishing is None) or (vanishing >= 60.0),
            note=("GZ does not return to zero in computed range — pass" if vanishing is None
                  else "first heel at which GZ returns to zero after peak"),
        ),
        Criterion(
            name="Area 0°–30°",
            threshold=0.055, value=A_30, units="m·rad",
            passed=A_30 >= 0.055,
            note="∫₀³⁰ GZ dφ",
        ),
        Criterion(
            name="Area 0°–40°",
            threshold=0.090, value=A_40, units="m·rad",
            passed=A_40 >= 0.090,
            note="∫₀⁴⁰ GZ dφ",
        ),
        Criterion(
            name="Area 30°–40°",
            threshold=0.030, value=A_30_40, units="m·rad",
            passed=A_30_40 >= 0.030,
            note="∫₃₀⁴⁰ GZ dφ",
        ),
    ]
    return out


def overall_pass(criteria: list[Criterion]) -> bool:
    return all(c.passed for c in criteria)
