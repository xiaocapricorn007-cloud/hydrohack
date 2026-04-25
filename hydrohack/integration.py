"""Numerical-integration primitives.

Implemented from scratch — no scipy.integrate. Only NumPy is used for
array arithmetic. These functions are the core quadrature engine used by
every hydrostatic calculation in the package.

Conventions
-----------
Uniform-spacing variants take ``y`` (samples) and ``dx`` (spacing).
Non-uniform variants take ``x`` and ``y`` of equal length.
"""

from __future__ import annotations
import numpy as np


def trapezoidal(y, dx: float) -> float:
    """Composite trapezoidal rule on uniformly-spaced samples.

    ``∫ y dx ≈ dx · (½y₀ + y₁ + ... + y_{n-2} + ½y_{n-1})``
    """
    y = np.asarray(y, dtype=float)
    if y.size < 2:
        return 0.0
    return float(dx * (0.5 * (y[0] + y[-1]) + y[1:-1].sum()))


def simpson(y, dx: float) -> float:
    """Composite Simpson's 1/3 rule on uniformly-spaced samples.

    Simpson's rule needs an *even* number of intervals (odd number of
    samples). When the user supplies an even sample count we apply
    Simpson's on the first ``n-1`` samples and close out with a trapezoid
    on the final interval — a common pragmatic fallback.
    """
    y = np.asarray(y, dtype=float)
    n = y.size
    if n < 2:
        return 0.0
    if n == 2:
        return 0.5 * dx * float(y[0] + y[1])
    if n % 2 == 0:
        # Simpson over first n-1 samples (which is odd) + trapezoid on the tail.
        return simpson(y[:-1], dx) + 0.5 * dx * float(y[-2] + y[-1])
    # n is odd ⇒ (n-1) intervals, which is even ⇒ pure Simpson 1/3.
    s = float(y[0] + y[-1])
    s += 4.0 * float(y[1:-1:2].sum())
    s += 2.0 * float(y[2:-1:2].sum())
    return s * dx / 3.0


def trapezoidal_nonuniform(x, y) -> float:
    """Trapezoidal rule on arbitrary (sorted) ``x`` samples."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 2:
        return 0.0
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(x)))


def first_moment(x, y, method: str = "simpson") -> float:
    """Compute ``∫ x · y(x) dx`` for samples on a uniform grid ``x``.

    ``method`` can be ``"simpson"`` or ``"trap"``. Useful for centroid
    integrations such as LCB and LCF.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    integrand = x * y
    dx = float(x[1] - x[0]) if x.size >= 2 else 0.0
    if method == "trap":
        return trapezoidal(integrand, dx)
    return simpson(integrand, dx)


def quadrature(y, dx: float, method: str = "simpson") -> float:
    """Dispatch helper: ``method ∈ {"simpson","trap"}``."""
    if method == "trap":
        return trapezoidal(y, dx)
    if method == "simpson":
        return simpson(y, dx)
    raise ValueError(f"Unknown integration method: {method!r}")


# =====================================================================
# Non-uniform Simpson's rule (parabolic-fit composite)
# =====================================================================
def simpson_nonuniform(x, y) -> float:
    """Composite Simpson's-1/3 rule generalised to non-uniform sample
    spacing.

    For each consecutive triplet ``(x₂ₖ, x₂ₖ₊₁, x₂ₖ₊₂)`` the unique
    parabola through the three samples is integrated exactly; the
    contributions are summed. When the number of intervals is odd we
    apply this rule on the first ``n-1`` intervals and a trapezoid on
    the trailing interval (preserves O(h²)).

    Reference: standard generalisation of Simpson's rule, e.g.
    Press, Teukolsky, Vetterling & Flannery, *Numerical Recipes*, §4.2.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = x.size
    if n != y.size:
        raise ValueError("x and y must have the same length")
    if n < 2:
        return 0.0
    if n == 2:
        return float(0.5 * (y[0] + y[1]) * (x[1] - x[0]))

    total = 0.0
    i = 0
    # Process pairs of intervals (triplets of points)
    while i + 2 < n:
        x0, x1, x2 = x[i], x[i + 1], x[i + 2]
        y0, y1, y2 = y[i], y[i + 1], y[i + 2]
        h0 = x1 - x0
        h1 = x2 - x1
        if h0 <= 0 or h1 <= 0:
            raise ValueError("x must be strictly increasing")
        s = (h0 + h1) / 6.0 * (
            (2.0 - h1 / h0) * y0
            + (h0 + h1) ** 2 / (h0 * h1) * y1
            + (2.0 - h0 / h1) * y2
        )
        total += s
        i += 2
    # Trapezoid on the trailing single interval, if any
    if i + 1 < n:
        total += 0.5 * (y[i] + y[i + 1]) * (x[i + 1] - x[i])
    return float(total)


def is_uniform(x, rel_tol: float = 1e-6) -> bool:
    """Return True if ``x`` has (essentially) uniform spacing."""
    x = np.asarray(x, dtype=float)
    if x.size < 3:
        return True
    d = np.diff(x)
    return bool(np.std(d) <= rel_tol * abs(np.mean(d)))


def integrate(x, y, method: str = "auto") -> float:
    """Dispatcher: integrate ``y`` over ``x`` using the best method.

    ``method`` ∈ ``{"auto", "simpson", "simpson_nu", "trap", "trap_nu"}``.
    ``"auto"`` chooses Simpson uniform if ``x`` is evenly spaced, else
    non-uniform Simpson.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if method == "auto":
        method = "simpson" if is_uniform(x) else "simpson_nu"
    if method == "simpson":
        dx = float(x[1] - x[0]) if x.size >= 2 else 0.0
        return simpson(y, dx)
    if method == "trap":
        dx = float(x[1] - x[0]) if x.size >= 2 else 0.0
        return trapezoidal(y, dx)
    if method == "simpson_nu":
        return simpson_nonuniform(x, y)
    if method == "trap_nu":
        return trapezoidal_nonuniform(x, y)
    raise ValueError(f"Unknown integration method: {method!r}")
