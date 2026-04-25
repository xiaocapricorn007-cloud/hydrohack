"""Plot helpers: GZ, KN, body plan, sectional-area, hydrostatic curves.

Each plot has a ``make_*_figure`` factory that returns a matplotlib
``Figure`` and a thin ``plot_*`` wrapper that also saves to disk.
A single style block at the top keeps all plots visually consistent.
"""

from __future__ import annotations
from pathlib import Path
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from .geometry import Hull
from .stability import StabilityCurve

# ---------------------------------------------------------------------
# Visual style — single source of truth
# ---------------------------------------------------------------------
PRIMARY = "#0E7C86"
ACCENT  = "#F59E0B"
DANGER  = "#EF4444"
OK      = "#10B981"
SUBTLE  = "#94A3B8"
INK     = "#0F172A"

_STYLE = {
    "figure.facecolor":  "white",
    "axes.facecolor":    "white",
    "axes.edgecolor":    "#CBD5E1",
    "axes.labelcolor":   INK,
    "axes.titlecolor":   INK,
    "axes.titlesize":    13,
    "axes.titleweight":  "bold",
    "axes.labelsize":    11,
    "xtick.color":       INK,
    "ytick.color":       INK,
    "grid.color":        "#E2E8F0",
    "grid.linestyle":    "-",
    "grid.linewidth":    0.8,
    "axes.grid":         True,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "font.family":       "sans-serif",
    "legend.frameon":    False,
    "legend.fontsize":   10,
}

def _apply_style(fig: Figure) -> None:
    for k, v in _STYLE.items():
        plt.rcParams[k] = v


# ---------------------------------------------------------------------
# GZ curve — annotated with GMt slope, peak, vanishing angle, IMO areas
# ---------------------------------------------------------------------
def make_gz_figure(curve: StabilityCurve, GMt: float | None = None) -> Figure:
    fig, ax = plt.subplots(figsize=(8.5, 5.0))
    _apply_style(fig)

    phi = curve.phi_deg
    gz = curve.GZ
    ax.plot(phi, gz, marker="o", lw=2.2, color=PRIMARY, label="GZ", zorder=3)
    ax.axhline(0.0, color=INK, lw=0.7)

    # Shade IMO area criteria where the heel grid covers them
    for lo, hi, label in [(0, 30, "0°–30°"), (30, 40, "30°–40°")]:
        mask = (phi >= lo) & (phi <= hi)
        if mask.sum() >= 2:
            ax.fill_between(phi[mask], 0, gz[mask], alpha=0.10, color=ACCENT)
    # Add a subtle band caption
    if (phi <= 40).sum() >= 2:
        ax.text(20, ax.get_ylim()[1] * 0.95, "IMO area zones",
                ha="center", color=ACCENT, fontsize=8, alpha=0.9)

    # Initial slope = GMt (in radians) overlay
    if GMt is not None and gz.size >= 2:
        phi_slope = np.linspace(0, 30.0, 50)
        gz_slope = GMt * np.sin(np.deg2rad(phi_slope))
        ax.plot(phi_slope, gz_slope, "--", color=SUBTLE, lw=1.2,
                label=f"GMt·sinφ  (GMt={GMt:.3f} m)", zorder=2)

    # Peak marker
    if gz.size:
        i = int(np.argmax(gz))
        ax.plot(phi[i], gz[i], "o", color=ACCENT, markersize=9, zorder=4)
        ax.annotate(
            f"max GZ = {gz[i]:.3f} m\nat φ = {phi[i]:.1f}°",
            xy=(phi[i], gz[i]), xytext=(10, 10),
            textcoords="offset points", fontsize=9, color=ACCENT, fontweight="bold",
        )

    # Vanishing-angle marker (first zero-crossing after the peak)
    if gz.size >= 2 and gz.max() > 0:
        i_peak = int(np.argmax(gz))
        for j in range(i_peak, gz.size - 1):
            if gz[j] >= 0 >= gz[j + 1]:
                t = gz[j] / (gz[j] - gz[j + 1])
                phi_v = phi[j] + t * (phi[j + 1] - phi[j])
                ax.axvline(phi_v, color=DANGER, lw=1.0, ls=":", zorder=2)
                ax.annotate(
                    f"vanishing\nφ = {phi_v:.1f}°",
                    xy=(phi_v, 0.0), xytext=(6, 8),
                    textcoords="offset points", fontsize=9, color=DANGER,
                )
                break

    ax.set_xlabel("Heel angle  φ  (deg)")
    ax.set_ylabel("Righting arm  GZ  (m)")
    ax.set_title("Static Stability Curve")
    ax.legend(loc="upper left")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# KN curve
# ---------------------------------------------------------------------
def make_kn_figure(curve: StabilityCurve) -> Figure:
    fig, ax = plt.subplots(figsize=(8.5, 5.0))
    _apply_style(fig)
    ax.plot(curve.phi_deg, curve.KN, marker="s", lw=2.2, color=OK, label="KN")
    ax.set_xlabel("Heel angle  φ  (deg)")
    ax.set_ylabel("KN  (m)")
    ax.set_title("Cross Curve of Stability")
    ax.legend(loc="upper left")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# Cross curves of stability — KN(Δ) per heel angle
# ---------------------------------------------------------------------
def make_cross_curves_figure(
    displacements: np.ndarray,
    phi_deg: np.ndarray,
    KN: np.ndarray,
    Delta_design: float | None = None,
) -> Figure:
    """Cross-curves of stability — KN vs displacement, one curve per heel angle.

    ``KN`` has shape ``(n_displacements, n_angles)``. Conventional
    naval-arch chart layout: displacement on the x-axis, KN on the
    y-axis, separate curve per heel angle (annotated).
    """
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    _apply_style(fig)
    cmap = plt.get_cmap("plasma")
    n_phi = phi_deg.size

    # plot only a subset of angles to keep the chart readable (every 10° + endpoints)
    show_idx = list(range(n_phi))
    if n_phi > 12:
        show_idx = [k for k, p in enumerate(phi_deg) if int(p) % 10 == 0]
        if show_idx[-1] != n_phi - 1:
            show_idx.append(n_phi - 1)

    for k in show_idx:
        c = cmap(k / max(n_phi - 1, 1))
        ax.plot(displacements, KN[:, k], lw=1.5, color=c,
                label=f"φ = {phi_deg[k]:.0f}°")

    if Delta_design is not None:
        ax.axvline(Delta_design, color=ACCENT, lw=1.0, ls="--",
                   label=f"Δ_design = {Delta_design:,.0f}")

    ax.set_xlabel("Displacement  Δ  (mass)")
    ax.set_ylabel("KN  (m)")
    ax.set_title("Cross curves of stability  ·  KN(Δ) per heel angle")
    ax.legend(loc="upper left", fontsize=8, ncol=2, title="heel φ")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# Body plan
# ---------------------------------------------------------------------
def make_body_plan_figure(hull: Hull) -> Figure:
    fig, ax = plt.subplots(figsize=(7.0, 6.5))
    _apply_style(fig)
    n_x = hull.n_stations
    cmap = plt.get_cmap("viridis")
    for i in range(n_x):
        y = hull.offsets[i]
        z = hull.waterlines
        side = 1.0 if i >= n_x // 2 else -1.0
        color = cmap(i / max(n_x - 1, 1))
        ax.plot(side * y, z, color=color, lw=1.0, alpha=0.9)
    ax.axvline(0.0, color=INK, lw=0.7)
    ax.set_xlabel("y  (m)")
    ax.set_ylabel("z  (m)")
    ax.set_title("Body plan  ·  forward stations right, aft mirrored left")
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# Half-breadth (waterline) plan  ·  top-down view
# ---------------------------------------------------------------------
def make_waterline_plan_figure(hull: Hull) -> Figure:
    """Half-breadth plan: looking straight down on the hull. One curve
    per waterline, mirrored across the centerline. Forward to the right.
    """
    fig, ax = plt.subplots(figsize=(11.5, 4.4))
    _apply_style(fig)
    n_z = hull.n_waterlines
    cmap = plt.get_cmap("viridis")
    for j in range(n_z):
        y = hull.offsets[:, j]
        c = cmap(j / max(n_z - 1, 1))
        ax.plot(hull.stations,  y, color=c, lw=1.1)
        ax.plot(hull.stations, -y, color=c, lw=1.1)
    ax.axhline(0.0, color=INK, lw=0.5)
    ax.set_xlabel("x  along length  (m)")
    ax.set_ylabel("y  half-breadth  (m)")
    ax.set_title("Half-breadth (waterline) plan  ·  one curve per waterline (z low → high)")
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# Profile / sheer plan  ·  buttocks at constant y
# ---------------------------------------------------------------------
def make_profile_plan_figure(hull: Hull, n_buttocks: int = 5) -> Figure:
    """Profile (sheer) plan with buttocks. Each buttock is the locus
    z(x) along which the hull surface satisfies y(x, z) = y_buttock,
    giving a side-on view of the hull at that constant transverse offset.

    Buttocks are placed at evenly-spaced fractions of the maximum
    half-breadth on the hull, e.g. 1/6, 2/6, …, 5/6 of B/2 for n=5.
    """
    fig, ax = plt.subplots(figsize=(11.5, 4.4))
    _apply_style(fig)

    y_max = float(hull.offsets.max())
    if y_max <= 0:
        ax.text(0.5, 0.5, "no hull breadth — cannot build profile plan",
                ha="center", va="center", transform=ax.transAxes)
        return fig

    fractions = np.linspace(1.0, n_buttocks, n_buttocks) / (n_buttocks + 1)
    buttocks = fractions * y_max
    cmap = plt.get_cmap("plasma")

    for k, y_b in enumerate(buttocks):
        x_pts: list[float] = []
        z_pts: list[float] = []
        for i in range(hull.n_stations):
            y_prof = hull.offsets[i, :]
            z_prof = hull.waterlines
            # Find the lowest crossing of y_b from below as z increases.
            for j in range(z_prof.size - 1):
                a, b = float(y_prof[j]), float(y_prof[j + 1])
                if (a < y_b <= b) or (a > y_b >= b):  # straddles y_b
                    t = (y_b - a) / (b - a) if abs(b - a) > 1e-12 else 0.0
                    z_pts.append(float(z_prof[j] + t * (z_prof[j + 1] - z_prof[j])))
                    x_pts.append(float(hull.stations[i]))
                    break
        if x_pts:
            ax.plot(x_pts, z_pts, color=cmap(k / max(n_buttocks - 1, 1)),
                    lw=1.4, label=f"y = {y_b:.2f} m")

    # Deck and baseline reference lines
    z_top = float(hull.waterlines[-1])
    z_bot = float(hull.waterlines[0])
    x_lo, x_hi = float(hull.stations[0]), float(hull.stations[-1])
    ax.plot([x_lo, x_hi], [z_top, z_top], color=INK, lw=0.9, ls="--", alpha=0.55)
    ax.plot([x_lo, x_hi], [z_bot, z_bot], color=INK, lw=0.9, ls="--", alpha=0.55)
    ax.text(x_hi, z_top, " deck", va="bottom", ha="left", fontsize=8, color=INK)
    ax.text(x_hi, z_bot, " baseline", va="top",    ha="left", fontsize=8, color=INK)

    ax.set_xlabel("x  along length  (m)")
    ax.set_ylabel("z  (m)")
    ax.set_title(f"Profile (sheer) plan  ·  {n_buttocks} buttocks at constant y")
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# Sectional area curve
# ---------------------------------------------------------------------
def make_section_areas_figure(stations: np.ndarray, areas: np.ndarray, T: float) -> Figure:
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    _apply_style(fig)
    ax.fill_between(stations, areas, alpha=0.18, color=PRIMARY)
    ax.plot(stations, areas, marker="o", lw=2.0, color=PRIMARY)
    ax.set_xlabel("x  along length  (m)")
    ax.set_ylabel(f"section area at T = {T:.3f} m  (m²)")
    ax.set_title("Sectional Area Curve")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# Bonjean curves
# ---------------------------------------------------------------------
def make_bonjean_figure(
    stations: np.ndarray, drafts: np.ndarray, areas: np.ndarray,
    T_design: float | None = None,
) -> Figure:
    """Bonjean curves: sectional area A(x_i, T) vs draft, one curve per station.

    Parameters
    ----------
    stations : (n_stations,)
        Longitudinal x-positions, used only for the legend / colour
        ordering.
    drafts   : (n_drafts,)
        Draft sweep values (m).
    areas    : (n_drafts, n_stations)
        ``areas[k, i]`` is the sectional area at station ``i`` and
        draft ``drafts[k]``.
    """
    fig, ax = plt.subplots(figsize=(9.5, 6.5))
    _apply_style(fig)
    n = stations.size
    cmap = plt.get_cmap("viridis")

    # Plot every station — area on x-axis, draft on y-axis (the conventional
    # naval-architecture orientation, since draft is the independent quantity).
    for i in range(n):
        c = cmap(i / max(n - 1, 1))
        ax.plot(areas[:, i], drafts, color=c, lw=1.3,
                label=f"x={stations[i]:.2f}" if (i % max(n // 10, 1) == 0) else None)

    if T_design is not None:
        ax.axhline(T_design, color=ACCENT, lw=1.0, ls="--",
                   label=f"T_design = {T_design:.3f} m")

    ax.set_xlabel("section area  A(x, T)  (m²)")
    ax.set_ylabel("draft  T  (m)")
    ax.set_title("Bonjean curves  ·  one line per station")
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="lower right", fontsize=8, ncol=2,
                  title="station x  (m)")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# Hydrostatic curves over a draft sweep
# ---------------------------------------------------------------------
def make_hydrostatic_curves_figure(
    drafts: np.ndarray, V: np.ndarray, Aw: np.ndarray,
    KMt: np.ndarray, LCB: np.ndarray, LCF: np.ndarray, LBP: float,
) -> Figure:
    """Classic four-up plot of hydrostatic quantities vs draft."""
    fig, axes = plt.subplots(1, 4, figsize=(16, 4.2), sharey=True)
    _apply_style(fig)

    for ax in axes:
        ax.set_ylabel("")
    axes[0].set_ylabel("Draft  T  (m)")

    axes[0].plot(V, drafts, lw=2, color=PRIMARY)
    axes[0].set_xlabel("Displacement ∇  (m³)")
    axes[0].set_title("∇")

    axes[1].plot(Aw, drafts, lw=2, color=OK)
    axes[1].set_xlabel("Aw  (m²)")
    axes[1].set_title("Aw")

    axes[2].plot(KMt, drafts, lw=2, color=ACCENT)
    axes[2].set_xlabel("KMt  (m)")
    axes[2].set_title("KMt")

    axes[3].plot(LCB, drafts, lw=2, color=PRIMARY, label="LCB")
    axes[3].plot(LCF, drafts, lw=2, color=DANGER, label="LCF")
    axes[3].axvline(LBP / 2, color=SUBTLE, lw=0.8, ls="--", label="midship")
    axes[3].set_xlabel("LCB / LCF  (m)")
    axes[3].set_title("Centres")
    axes[3].legend(loc="lower right", fontsize=8)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------
# Save-to-disk wrappers
# ---------------------------------------------------------------------
def plot_gz_curve(curve: StabilityCurve, path: str | Path, GMt: float | None = None) -> None:
    fig = make_gz_figure(curve, GMt=GMt)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_kn_curve(curve: StabilityCurve, path: str | Path) -> None:
    fig = make_kn_figure(curve)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_body_plan(hull: Hull, path: str | Path) -> None:
    fig = make_body_plan_figure(hull)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_section_areas(stations: np.ndarray, areas: np.ndarray, T: float, path: str | Path) -> None:
    fig = make_section_areas_figure(stations, areas, T)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_waterline_plan(hull: Hull, path: str | Path) -> None:
    fig = make_waterline_plan_figure(hull)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_profile_plan(hull: Hull, path: str | Path, n_buttocks: int = 5) -> None:
    fig = make_profile_plan_figure(hull, n_buttocks=n_buttocks)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
