"""Hull geometry: offsets table → numerical hull.

Coordinate system
-----------------
* x — longitudinal, measured from AP (aft perpendicular). +x → forward.
* y — transverse half-breadth, ≥ 0 in the offsets table (port is mirror).
* z — vertical, measured from baseline (keel) upward.

The :class:`Hull` object stores stations (x positions), waterlines
(z positions), and the offset matrix ``y[i, j]`` (half-breadth at
station ``i``, waterline ``j``). All hydrostatic calculations build on
these primitives.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass
class Hull:
    """Discrete hull description.

    Parameters
    ----------
    stations : (n_x,) array
        x-positions of stations, monotonically increasing, metres.
    waterlines : (n_z,) array
        z-positions of waterlines (heights above baseline), metres.
    offsets : (n_x, n_z) array
        Half-breadths y(x_i, z_j) ≥ 0, metres. ``offsets[:, 0]`` is the
        bottom waterline (typically the keel).
    """

    stations: np.ndarray
    waterlines: np.ndarray
    offsets: np.ndarray

    def __post_init__(self) -> None:
        self.stations = np.asarray(self.stations, dtype=float)
        self.waterlines = np.asarray(self.waterlines, dtype=float)
        self.offsets = np.asarray(self.offsets, dtype=float)
        assert self.offsets.shape == (self.stations.size, self.waterlines.size), (
            f"offsets shape {self.offsets.shape} does not match "
            f"({self.stations.size}, {self.waterlines.size})"
        )
        assert np.all(np.diff(self.stations) > 0), "stations must be strictly increasing"
        assert np.all(np.diff(self.waterlines) > 0), "waterlines must be strictly increasing"

    # ---------------------------------------------------------------
    # Convenience accessors
    # ---------------------------------------------------------------
    @property
    def n_stations(self) -> int:
        return self.stations.size

    @property
    def n_waterlines(self) -> int:
        return self.waterlines.size

    @property
    def dx(self) -> float:
        """Uniform station spacing (assumes uniform — common for offsets tables)."""
        return float(self.stations[1] - self.stations[0])

    @property
    def dz(self) -> float:
        """Uniform waterline spacing."""
        return float(self.waterlines[1] - self.waterlines[0])

    @property
    def LBP(self) -> float:
        """Length between perpendiculars = span of station x-positions."""
        return float(self.stations[-1] - self.stations[0])

    # ---------------------------------------------------------------
    # Interpolation
    # ---------------------------------------------------------------
    def half_breadth_at(self, station_idx: int, draft: float) -> float:
        """Linear-interpolate half-breadth at z = ``draft`` for one station.

        Below the lowest waterline returns ``offsets[i, 0]``; above the
        highest returns ``offsets[i, -1]`` (caller should ensure draft is
        within bounds).
        """
        z = self.waterlines
        b = self.offsets[station_idx]
        if draft <= z[0]:
            return float(b[0])
        if draft >= z[-1]:
            return float(b[-1])
        j = int(np.searchsorted(z, draft) - 1)
        t = (draft - z[j]) / (z[j + 1] - z[j])
        return float((1.0 - t) * b[j] + t * b[j + 1])

    def waterline_breadths(self, draft: float) -> np.ndarray:
        """Half-breadths along the length at the waterline z = draft."""
        return np.array(
            [self.half_breadth_at(i, draft) for i in range(self.n_stations)],
            dtype=float,
        )

    # ---------------------------------------------------------------
    # Section polygons (used by the heeled / GZ solver)
    # ---------------------------------------------------------------
    # ---------------------------------------------------------------
    # Densification — bilinear interpolation for smoother visualisations
    # ---------------------------------------------------------------
    def densify(self, n_x_factor: int = 4, n_z_factor: int = 4) -> "Hull":
        """Return a new ``Hull`` with bilinearly-interpolated offsets on
        a finer grid.

        ``n_x_factor`` subdivides each station-to-station interval into
        that many sub-intervals (so the densified grid has
        ``(n_stations - 1) * n_x_factor + 1`` points). ``n_z_factor``
        does the same for the waterline grid. Original samples are
        preserved exactly — only intermediate points are inserted.

        Used purely for visualisation (smoother body plan, half-breadth
        plan, and profile plan). The hydrostatic computation path uses
        the original, un-densified hull.
        """
        nxf = max(1, int(n_x_factor))
        nzf = max(1, int(n_z_factor))
        if nxf == 1 and nzf == 1:
            return self

        # New axis grids — subdivide each existing interval uniformly.
        if nxf > 1:
            x_pieces = [
                np.linspace(self.stations[i], self.stations[i + 1], nxf + 1)[:-1]
                for i in range(self.stations.size - 1)
            ]
            x_new = np.concatenate(x_pieces + [self.stations[-1:]])
        else:
            x_new = self.stations.copy()

        if nzf > 1:
            z_pieces = [
                np.linspace(self.waterlines[j], self.waterlines[j + 1], nzf + 1)[:-1]
                for j in range(self.waterlines.size - 1)
            ]
            z_new = np.concatenate(z_pieces + [self.waterlines[-1:]])
        else:
            z_new = self.waterlines.copy()

        # Bilinear interpolation in two passes:
        #   1) for each original station, interpolate the half-breadth profile
        #      onto the new z-grid;
        #   2) for each new z, interpolate along x onto the new x-grid.
        rows_z = np.array([
            np.interp(z_new, self.waterlines, self.offsets[i, :])
            for i in range(self.n_stations)
        ])  # shape (n_stations_orig, n_z_new)
        offsets_new = np.array([
            np.interp(x_new, self.stations, rows_z[:, j])
            for j in range(z_new.size)
        ]).T  # shape (n_x_new, n_z_new)

        return Hull(stations=x_new, waterlines=z_new, offsets=offsets_new)

    def section_polygon(self, station_idx: int) -> np.ndarray:
        """Return the closed full-section polygon at one station.

        Vertices are emitted counter-clockwise as (y, z) pairs:

        1. starboard contour from the keel up,
        2. across the deck to the centerline at ``z = z_max``,
        3. mirrored port contour back down to the keel.

        The polygon is closed implicitly (last vertex → first vertex
        edge across the keel at ``z = z_min``). No vertex is duplicated.
        """
        y_st = self.offsets[station_idx]
        z = self.waterlines
        verts: list[tuple[float, float]] = []

        # Starboard contour, bottom to top
        for yi, zi in zip(y_st, z):
            verts.append((float(yi), float(zi)))
        # Across the deck on the starboard side to centerline (close the top)
        verts.append((0.0, float(z[-1])))
        # Mirror to port: top-1 down to bottom (skip both the deck point at z_max
        # and — if the starboard side ended at the centerline — the keel point too)
        for yi, zi in zip(y_st[::-1], z[::-1]):
            if zi == z[-1]:
                continue  # deck centerline already placed
            verts.append((-float(yi), float(zi)))
        return np.asarray(verts, dtype=float)
