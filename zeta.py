"""
[A] zeta.py — ζ(t), σ(t), ρ(t) computation for Pseta.

Implements the Φ kernel and coincidence density from Taranova's Ψ-model
(PCT/IB2025/055633, §3). All timestamps in seconds internally.

Falsification condition:
    If ζ(t) on aligned streams (drummer locking in) does not exceed the
    permutation baseline by ≥1 std at known groove-lock events, and does
    not drop toward baseline when the drummer diverges, the coincidence
    detection approach requires revision.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Core kernel
# ---------------------------------------------------------------------------

def phi(x: float, y: float, sigma: float) -> float:
    """[A] Gaussian coincidence kernel: Φ(x, y) = exp(−|x−y|²/2σ²)."""
    d = x - y
    return math.exp(-d * d / (2.0 * sigma * sigma))


def _binary_gate(x: float, y: float, epsilon: float) -> bool:
    """[A] Binary intersection: X_ij = 1 iff |x−y| < ε. Prevents micro-fluctuation corruption."""
    return abs(x - y) < epsilon


# ---------------------------------------------------------------------------
# ζ₂ — pairwise coincidence density
# ---------------------------------------------------------------------------

def zeta2_pair(
    onsets_a: list[float],
    onsets_b: list[float],
    t: float,
    sigma: float,
    epsilon: float,
    horizon: float,
) -> float:
    """
    [A] Windowed onset coincidence density ζ₂(t) for one stream pair.

    Operationalization of the paper's temporal proximity criterion
    (Methodology §2: |t_i - t_j| < ε) over a sliding window.

    The paper defines ζ(t) = Σ_{i<j} Φ(S_i(t), S_j(t)) for continuous
    streams evaluated at a point. For discrete onset streams this becomes
    a windowed sum: count of Φ-weighted coincident onset pairs, normalized
    by min(|A|, |B|) so that perfect one-to-one sync yields ζ ≈ 1.

    Normalization rationale: paper's Statistical Stability section gives
    ζ = (1/n) Σ X_ij with n = N(N−1)/2. For one pair n=1 and X ∈ {0,1}.
    For windowed onsets, min(|A|, |B|) is the maximum number of
    non-overlapping coincidences achievable — the correct capacity denominator.

    Parameters:
        onsets_a, onsets_b: onset timestamps (seconds)
        t:        current time (seconds)
        sigma:    Φ kernel width (recognition radius, seconds)
        epsilon:  binary gate threshold (seconds); typically epsilon_factor × sigma
        horizon:  lookback window (seconds)

    Returns:
        ζ₂ ∈ [0, 1]: Φ-weighted coincidence rate. 0 if either stream is empty.

    Falsification condition:
        Real-stream ζ₂ must exceed permutation baseline by ≥1 std at events
        where the two players are known to be synchronized.
    """
    t_lo = t - horizon
    a = [o for o in onsets_a if t_lo <= o <= t]
    b = [o for o in onsets_b if t_lo <= o <= t]

    if not a or not b:
        return 0.0

    weighted = 0.0
    for oa in a:
        for ob in b:
            if _binary_gate(oa, ob, epsilon):
                weighted += phi(oa, ob, sigma)

    # Normalize by capacity: max achievable coincidences = min(|A|, |B|)
    return weighted / min(len(a), len(b))


# ---------------------------------------------------------------------------
# ζ₄ — quartet coincidence (ρ / resonance proxy)
# ---------------------------------------------------------------------------

def zeta4_quartet(
    stream_pairs: list[tuple[list[float], list[float]]],
    t: float,
    sigma: float,
    epsilon: float,
    horizon: float,
) -> float:
    """
    [C] Multi-stream amplification proxy — not defined in the paper.

    The paper defines ζ(t) = Σ_{i<j} Φ(S_i(t), S_j(t)) as a single sum over
    all pairs for any N. There is no separate ζ₄ quantity in the text.
    This function is an extrapolation from CLAUDE.md (which references
    "ζ₄" and "quartet amplification") not directly from PCT/IB2025/055633.

    Implementation approximates the fraction of epsilon-width time bins in
    the window where ≥3 distinct streams fire simultaneously. Returns 0 when
    fewer than 2 stream pairs exist (< 4 total streams).
    """
    if len(stream_pairs) < 2:
        return 0.0

    t_lo = t - horizon
    # Collect all onsets per stream
    all_streams: list[list[float]] = []
    for a, b in stream_pairs:
        all_streams.append([o for o in a if t_lo <= o <= t])
        all_streams.append([o for o in b if t_lo <= o <= t])

    # Build epsilon-width bins from all onset times
    all_onsets = sorted(o for s in all_streams for o in s)
    if not all_onsets:
        return 0.0

    # Count bins with ≥3 simultaneous stream hits
    n_bins   = 0
    n_filled = 0
    i = 0
    while i < len(all_onsets):
        bin_start = all_onsets[i]
        streams_hit = set()
        j = i
        while j < len(all_onsets) and all_onsets[j] < bin_start + epsilon:
            for sid, s in enumerate(all_streams):
                if all_onsets[j] in s:
                    streams_hit.add(sid)
            j += 1
        n_bins += 1
        if len(streams_hit) >= 3:
            n_filled += 1
        i = j

    if n_bins == 0:
        return 0.0
    return n_filled / n_bins


# ---------------------------------------------------------------------------
# σ (symmetry) — 5-dim structural vector
# ---------------------------------------------------------------------------

class Symmetry(NamedTuple):
    """
    [A/C] Five-dimensional symmetry vector V_ij(t) = [Δφ, Δf, Δτ, Δs, Δm].
    [A] Δf, Δτ computed.  [C] Δφ, Δs, Δm require beat tracker — not yet implemented.
    """
    delta_phase:   float  # [C] beat-phase difference — 0.0 until beat tracker exists
    delta_freq:    float  # [A] ratio of onset rates (saturates at 1.0)
    delta_timing:  float  # [A] mean coincidence offset, normalized to sigma
    delta_struct:  float  # [C] structural similarity — 0.0 placeholder
    delta_motif:   float  # [C] motif similarity — 0.0 placeholder

    def scalar(self) -> float:
        """[A] Euclidean norm of implemented dimensions (Δf, Δτ), normalized to [0,1]."""
        return math.sqrt(self.delta_freq ** 2 + self.delta_timing ** 2) / math.sqrt(2)


def symmetry_pair(
    onsets_a: list[float],
    onsets_b: list[float],
    t: float,
    sigma: float,
    epsilon: float,
    horizon: float,
) -> Symmetry:
    """
    [A/C] Compute the 5-dim symmetry vector for one stream pair.
    Δf and Δτ are implemented; Δφ, Δs, Δm are [C] stubs returning 0.0.
    """
    t_lo = t - horizon
    a = [o for o in onsets_a if t_lo <= o <= t]
    b = [o for o in onsets_b if t_lo <= o <= t]

    # [A] Δf: onset-rate ratio, saturated to [0, 1]
    rate_a = len(a) / horizon if horizon > 0 else 0.0
    rate_b = len(b) / horizon if horizon > 0 else 0.0
    if max(rate_a, rate_b) > 0:
        delta_freq = 1.0 - min(rate_a, rate_b) / max(rate_a, rate_b)
    else:
        delta_freq = 0.0

    # [A] Δτ: mean coincident-pair offset normalized to sigma
    offsets = []
    for oa in a:
        for ob in b:
            if _binary_gate(oa, ob, epsilon):
                offsets.append(abs(oa - ob))
    if offsets and sigma > 0:
        delta_timing = (sum(offsets) / len(offsets)) / sigma
        delta_timing = min(1.0, delta_timing)
    else:
        # No coincident pairs: maximum timing dissimilarity regardless of onset count
        delta_timing = 1.0

    return Symmetry(
        delta_phase=0.0,    # [C]
        delta_freq=delta_freq,
        delta_timing=delta_timing,
        delta_struct=0.0,   # [C]
        delta_motif=0.0,    # [C]
    )


# ---------------------------------------------------------------------------
# Top-level: ζ across stream pair pool
# ---------------------------------------------------------------------------

class ZetaResult(NamedTuple):
    density:   float   # ζ₂ ∈ [0,1]: mean pairwise coincidence density
    resonance: float   # ζ₄ ∈ [0,1]: quartet amplification proxy
    symmetry:  float   # σ scalar ∈ [0,1]: structural mirroring (partial)


def compute(
    stream_pairs: list[tuple[list[float], list[float]]],
    t: float,
    sigma: float,
    epsilon: float,
    horizon: float,
) -> ZetaResult:
    """
    [A] Compute ζ₂, ζ₄, σ across the full stream pair pool.

    Parameters:
        stream_pairs: list of (onsets_a, onsets_b) in seconds
        t:       current time (seconds)
        sigma:   Φ kernel width (seconds)
        epsilon: binary gate (seconds)
        horizon: lookback window (seconds)

    Returns:
        ZetaResult(density, resonance, symmetry)

    Falsification condition:
        density on real data must exceed permutation_baseline(density) by ≥1 std
        at known groove-lock events. Without this, result is not scientifically
        reportable.

    # WARNING: No baseline — result is not yet scientifically reportable
    """
    if not stream_pairs:
        return ZetaResult(0.0, 0.0, 0.0)

    densities  = [zeta2_pair(a, b, t, sigma, epsilon, horizon) for a, b in stream_pairs]
    symmetries = [symmetry_pair(a, b, t, sigma, epsilon, horizon).scalar() for a, b in stream_pairs]

    density  = sum(densities)  / len(densities)
    symmetry = sum(symmetries) / len(symmetries)
    resonance = zeta4_quartet(stream_pairs, t, sigma, epsilon, horizon)

    return ZetaResult(density=density, resonance=resonance, symmetry=symmetry)


# ---------------------------------------------------------------------------
# Ψ(t) = dζ/dt — coincidence derivative
# ---------------------------------------------------------------------------

def psi(
    zeta_history: list[tuple[float, float]],
) -> float:
    """
    [C] Ψ(t) = dζ/dt — rate of change of coincidence density.

    The paper (§3.2) defines:
        Ψ(t) = d/dt ζ(t) = Σ_{i<j} [⟨∇₁Φ(Sᵢ,Sⱼ), Ṡᵢ⟩ + ⟨∇₂Φ(Sᵢ,Sⱼ), Ṡⱼ⟩]

    This is the primary synchronization signal: positive Ψ means streams
    are converging, negative means diverging.

    Current implementation: finite difference over the last two ζ samples.
    Not yet the analytic gradient form from the paper — requires continuous
    stream derivatives Ṡᵢ which are not tracked for onset streams.

    Parameters:
        zeta_history: list of (timestamp_s, zeta_density) ordered by time

    Returns:
        Approximate Ψ(t) in ζ-units/second. 0.0 if fewer than 2 samples.
    """
    if len(zeta_history) < 2:
        return 0.0
    t1, z1 = zeta_history[-1]
    t0, z0 = zeta_history[-2]
    dt = t1 - t0
    if dt <= 0:
        return 0.0
    return (z1 - z0) / dt


# ---------------------------------------------------------------------------
# Permutation baseline
# ---------------------------------------------------------------------------

def permutation_baseline(
    onsets_a: list[float],
    onsets_b: list[float],
    t: float,
    sigma: float,
    epsilon: float,
    horizon: float,
    n_shuffles: int = 20,
) -> tuple[float, float]:
    """
    [A] Permutation baseline for one stream pair.

    Shuffles inter-onset intervals of stream b, preserving its marginal
    distribution. Returns (mean_zeta, std_zeta) over n_shuffles.

    This is the required companion to every ζ₂ measurement on real data.
    Without it, ζ > 0 has no statistical meaning.
    """
    t_lo  = t - horizon
    a     = [o for o in onsets_a if t_lo <= o <= t]
    b_win = [o for o in onsets_b if t_lo <= o <= t]

    if len(b_win) < 2 or not a:
        return 0.0, 0.0

    iois = [b_win[i + 1] - b_win[i] for i in range(len(b_win) - 1)]
    vals = []
    for _ in range(n_shuffles):
        iois_shuf = iois[:]
        random.shuffle(iois_shuf)
        b_shuf = [b_win[0]]
        for ioi in iois_shuf:
            b_shuf.append(b_shuf[-1] + ioi)
        vals.append(zeta2_pair(a, b_shuf, t, sigma, epsilon, horizon))

    mean = sum(vals) / len(vals)
    std  = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    return mean, std


# ---------------------------------------------------------------------------
# Stream pair pool extraction helper
# ---------------------------------------------------------------------------

def pairs_from_active_pads(
    pad_colors: dict[tuple[str, int], tuple[int, int, int]],
    onset_streams: dict[tuple[str, int], list[float]],
) -> list[tuple[list[float], list[float]]]:
    """
    [A] Build all-pairs stream list from the set of active pads.

    A pad is active iff it has any color assigned. Color is purely visual
    (determines timeline rendering); it carries no analytical grouping.
    All N active pads are cross-compared: N*(N-1)/2 pairs total.

    Returns list of (onsets_a, onsets_b) for every unique active-pad pair.
    """
    active = list(pad_colors.keys())
    pairs = []
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            onsets_a = onset_streams.get(active[i], [])
            onsets_b = onset_streams.get(active[j], [])
            pairs.append((onsets_a, onsets_b))
    return pairs
