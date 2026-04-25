"""Interactive 3-D views of the hull, built on Plotly.

Two figure factories:

* :func:`make_3d_lines_figure` — overlays all three lines-plan view types
  (sections, waterlines, and buttocks) in a single interactive 3-D scene.

* :func:`make_3d_hull_mesh_figure` — builds a triangle-mesh of the hull
  surface from the offset matrix and renders it as a shaded ``Mesh3d``.

Both figures support full pan / rotate / zoom out of the box; Streamlit
embeds them via ``st.plotly_chart``. Plotly is a visualization library —
no hydrostatics or stability logic is delegated to it.
"""

from __future__ import annotations
import numpy as np
import matplotlib.cm as cm
import plotly.graph_objects as go

from .geometry import Hull


def _mpl_to_rgb(cmap_name: str, t: float) -> str:
    c = cm.get_cmap(cmap_name)(t)
    return f"rgb({int(c[0]*255)},{int(c[1]*255)},{int(c[2]*255)})"


# ---------------------------------------------------------------------
# Lines plan in 3D
# ---------------------------------------------------------------------
def make_3d_lines_figure(
    hull: Hull,
    n_buttocks: int = 5,
    show_sections: bool = True,
    show_waterlines: bool = True,
    show_buttocks: bool = True,
) -> go.Figure:
    """Render every station, waterline, and buttock as a 3-D line trace."""
    fig = go.Figure()
    n_x, n_z = hull.n_stations, hull.n_waterlines

    # ---- Sections (constant x) ----
    if show_sections:
        for i in range(n_x):
            x = float(hull.stations[i])
            y = hull.offsets[i, :]
            z = hull.waterlines
            color = _mpl_to_rgb("viridis", i / max(n_x - 1, 1))
            fig.add_trace(go.Scatter3d(
                x=[x] * n_z, y=y, z=z,
                mode="lines", line=dict(color=color, width=3),
                name=f"section x={x:.2f}",
                legendgroup="sections", showlegend=(i == 0),
                hovertemplate=f"x={x:.2f}<br>y=%{{y:.2f}}<br>z=%{{z:.2f}}<extra></extra>",
            ))
            fig.add_trace(go.Scatter3d(
                x=[x] * n_z, y=-y, z=z,
                mode="lines", line=dict(color=color, width=3),
                name=f"section x={x:.2f} (port)",
                legendgroup="sections", showlegend=False,
            ))

    # ---- Waterlines (constant z) ----
    if show_waterlines:
        for j in range(n_z):
            z = float(hull.waterlines[j])
            x = hull.stations
            y = hull.offsets[:, j]
            color = _mpl_to_rgb("plasma", j / max(n_z - 1, 1))
            fig.add_trace(go.Scatter3d(
                x=x, y=y, z=[z] * n_x,
                mode="lines", line=dict(color=color, width=3),
                name=f"waterline z={z:.2f}",
                legendgroup="waterlines", showlegend=(j == 0),
            ))
            fig.add_trace(go.Scatter3d(
                x=x, y=-y, z=[z] * n_x,
                mode="lines", line=dict(color=color, width=3),
                name=f"waterline z={z:.2f} (port)",
                legendgroup="waterlines", showlegend=False,
            ))

    # ---- Buttocks (constant y) ----
    if show_buttocks and n_buttocks > 0:
        y_max = float(hull.offsets.max())
        if y_max > 0:
            fractions = np.linspace(1.0, n_buttocks, n_buttocks) / (n_buttocks + 1)
            buttocks = fractions * y_max
            for k, y_b in enumerate(buttocks):
                x_pts: list[float] = []
                z_pts: list[float] = []
                for i in range(n_x):
                    y_prof = hull.offsets[i, :]
                    z_prof = hull.waterlines
                    for jj in range(z_prof.size - 1):
                        a, b = float(y_prof[jj]), float(y_prof[jj + 1])
                        if (a < y_b <= b) or (a > y_b >= b):
                            t = (y_b - a) / (b - a) if abs(b - a) > 1e-12 else 0.0
                            z_pts.append(float(z_prof[jj] + t * (z_prof[jj + 1] - z_prof[jj])))
                            x_pts.append(float(hull.stations[i]))
                            break
                if not x_pts:
                    continue
                color = _mpl_to_rgb("inferno", k / max(n_buttocks - 1, 1))
                fig.add_trace(go.Scatter3d(
                    x=x_pts, y=[y_b] * len(x_pts), z=z_pts,
                    mode="lines", line=dict(color=color, width=4),
                    name=f"buttock y={y_b:.2f}",
                    legendgroup="buttocks", showlegend=True,
                ))
                fig.add_trace(go.Scatter3d(
                    x=x_pts, y=[-y_b] * len(x_pts), z=z_pts,
                    mode="lines", line=dict(color=color, width=4),
                    name=f"buttock y={-y_b:.2f}",
                    legendgroup="buttocks", showlegend=False,
                ))

    fig.update_layout(
        scene=dict(
            xaxis_title="x  (m)",
            yaxis_title="y  (m)",
            zaxis_title="z  (m)",
            aspectmode="data",   # equal axis scaling so the hull looks right
            xaxis=dict(backgroundcolor="rgb(245,247,250)", gridcolor="rgb(225,228,235)"),
            yaxis=dict(backgroundcolor="rgb(245,247,250)", gridcolor="rgb(225,228,235)"),
            zaxis=dict(backgroundcolor="rgb(245,247,250)", gridcolor="rgb(225,228,235)"),
        ),
        margin=dict(l=0, r=0, b=0, t=30),
        height=720,
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
        title=dict(text="Lines plan in 3-D — pan / rotate / zoom",
                   x=0.5, xanchor="center"),
    )
    return fig


# ---------------------------------------------------------------------
# Triangle-mesh hull
# ---------------------------------------------------------------------
def make_3d_hull_mesh_figure(
    hull: Hull,
    opacity: float = 0.92,
    fill_bottom: bool = False,
    fill_top: bool = False,
    shade_mode: str = "depth",
) -> go.Figure:
    """Build a triangle mesh from the offset table and shade it.

    Parameters
    ----------
    shade_mode : {"depth", "grey"}
        ``"depth"`` colours by z using the Tealrose colorscale and
        applies the supplied ``opacity``. ``"grey"`` paints the whole
        mesh in a uniform opaque grey, which is useful for inspecting
        hull form without the colour gradient confounding shape reading.

    Optional closures
    -----------------
    * ``fill_bottom`` — flat keel cap at z = lowest waterline.
    * ``fill_top``    — flat deck cap at z = highest waterline.
    """
    n_x, n_z = hull.n_stations, hull.n_waterlines

    X_list: list[np.ndarray] = []
    Y_list: list[np.ndarray] = []
    Z_list: list[np.ndarray] = []
    faces: list[list[int]] = []

    def _add_verts(xs, ys, zs) -> int:
        base = sum(a.size for a in X_list)
        X_list.append(np.asarray(xs, dtype=float).flatten())
        Y_list.append(np.asarray(ys, dtype=float).flatten())
        Z_list.append(np.asarray(zs, dtype=float).flatten())
        return base

    # ---- Hull surface (starboard + port) -----------------------------
    base_s = _add_verts(
        np.repeat(hull.stations[:, None], n_z, axis=1),
        hull.offsets,
        np.tile(hull.waterlines, n_x),
    )
    for i in range(n_x - 1):
        for j in range(n_z - 1):
            v00 = base_s + i * n_z + j
            v01 = base_s + i * n_z + (j + 1)
            v10 = base_s + (i + 1) * n_z + j
            v11 = base_s + (i + 1) * n_z + (j + 1)
            faces.append([v00, v10, v11])
            faces.append([v00, v11, v01])

    base_p = _add_verts(
        np.repeat(hull.stations[:, None], n_z, axis=1),
        -hull.offsets,
        np.tile(hull.waterlines, n_x),
    )
    for i in range(n_x - 1):
        for j in range(n_z - 1):
            v00 = base_p + i * n_z + j
            v01 = base_p + i * n_z + (j + 1)
            v10 = base_p + (i + 1) * n_z + j
            v11 = base_p + (i + 1) * n_z + (j + 1)
            faces.append([v00, v11, v10])
            faces.append([v00, v01, v11])

    # ---- Bottom (keel) cap at z = waterlines[0] ----------------------
    if fill_bottom:
        z_b = float(hull.waterlines[0])
        for i in range(n_x - 1):
            x0, x1 = float(hull.stations[i]), float(hull.stations[i + 1])
            yA = float(hull.offsets[i, 0])
            yB = float(hull.offsets[i + 1, 0])
            base = _add_verts(
                [x0, x0, x1, x1],
                [+yA, -yA, -yB, +yB],
                [z_b, z_b, z_b, z_b],
            )
            # normals pointing down
            faces.append([base + 0, base + 1, base + 2])
            faces.append([base + 0, base + 2, base + 3])

    # ---- Top (deck) cap at z = waterlines[-1] ------------------------
    if fill_top:
        z_t = float(hull.waterlines[-1])
        for i in range(n_x - 1):
            x0, x1 = float(hull.stations[i]), float(hull.stations[i + 1])
            yA = float(hull.offsets[i, -1])
            yB = float(hull.offsets[i + 1, -1])
            base = _add_verts(
                [x0, x0, x1, x1],
                [+yA, -yA, -yB, +yB],
                [z_t, z_t, z_t, z_t],
            )
            # normals pointing up
            faces.append([base + 0, base + 2, base + 1])
            faces.append([base + 0, base + 3, base + 2])

    # ---- Compose mesh -----------------------------------------------
    X = np.concatenate(X_list)
    Y = np.concatenate(Y_list)
    Z = np.concatenate(Z_list)
    faces_arr = np.asarray(faces, dtype=np.int64)

    closures: list[str] = []
    if fill_bottom: closures.append("bottom")
    if fill_top:    closures.append("top")
    closure_text = (" · closures: " + ", ".join(closures)) if closures else ""

    if shade_mode == "grey":
        mesh_kwargs = dict(
            color="rgb(168, 172, 178)",
            opacity=1.0,
            flatshading=False,
            lighting=dict(ambient=0.55, diffuse=0.75, specular=0.18, fresnel=0.10),
            lightposition=dict(x=200, y=400, z=800),
            showscale=False,
        )
    else:
        mesh_kwargs = dict(
            intensity=Z,
            colorscale="Tealrose",
            opacity=opacity,
            flatshading=False,
            lighting=dict(ambient=0.45, diffuse=0.65, specular=0.25, fresnel=0.2),
            lightposition=dict(x=200, y=400, z=800),
            showscale=True,
            colorbar=dict(title="z  (m)", thickness=12, len=0.6, x=0.98),
        )

    fig = go.Figure(data=[
        go.Mesh3d(
            x=X, y=Y, z=Z,
            i=faces_arr[:, 0], j=faces_arr[:, 1], k=faces_arr[:, 2],
            name="hull",
            hovertemplate="x=%{x:.2f}<br>y=%{y:.2f}<br>z=%{z:.2f}<extra></extra>",
            **mesh_kwargs,
        ),
    ])
    fig.update_layout(
        scene=dict(
            xaxis_title="x  (m)",
            yaxis_title="y  (m)",
            zaxis_title="z  (m)",
            aspectmode="data",
            xaxis=dict(backgroundcolor="rgb(245,247,250)", gridcolor="rgb(225,228,235)"),
            yaxis=dict(backgroundcolor="rgb(245,247,250)", gridcolor="rgb(225,228,235)"),
            zaxis=dict(backgroundcolor="rgb(245,247,250)", gridcolor="rgb(225,228,235)"),
        ),
        margin=dict(l=0, r=0, b=0, t=30),
        height=720,
        title=dict(text=f"3-D hull mesh — {faces_arr.shape[0]} triangles{closure_text}",
                   x=0.5, xanchor="center"),
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
    )
    return fig
