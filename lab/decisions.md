# Scientific Decision Log

Rolling record of design decisions, their reasoning, and outcomes.
Each entry is tagged per CLAUDE.md conduct standards: [A] reproducible · [B] interpretive · [C] planned.

Failures are first-class entries. Do not delete superseded decisions — mark them [SUPERSEDED] and add what broke.

---

## Format

```
### DECISION-NNN — Short title
Date: YYYY-MM-DD
Status: ACTIVE | SUPERSEDED | OPEN
Supersedes: DECISION-NNN (if applicable)

**Decision:** What was chosen.
**Reasoning:** [A/B/C] Why.
**Alternatives rejected:** What else was considered and why not.
**Falsification condition:** What result would force revision.
**Outcome:** (filled in later) What happened. If SUPERSEDED, why it failed.
```

---

## Decisions

### DECISION-001 — S_i(t) is a scalar onset timestamp stream (d=1)
Date: 2026-04-15
Status: ACTIVE

**Decision:** Treat each MIDI stream as a 1D series of onset timestamps. S_i(t) ∈ ℝ¹. No velocity, pitch, or other features in the feature vector for Stage 1.

**Reasoning:** [A] The paper's Methodology section (§2, Data Preparation) explicitly states "S_i(t) are 1D time series." The temporal proximity criterion `|t_i − t_j| < ε` operates on scalar time differences. The abstract ℝ^d framing is general; the working implementation is scalar. [B] For drum synchrony, timing is the primary alignment signal — velocity and pitch are secondary and would require calibrated per-dimension thresholds we cannot set without pilot data.

**Alternatives rejected:**
- d=2 (onset time + velocity): Rejected because velocity threshold θ_velocity has no empirical prior for this instrument/player combination. Adding dimensions without calibrated thresholds inflates false negatives.
- d=3 (onset time + velocity + beat phase): Same issue, plus beat phase requires BPM estimation which introduces a second unvalidated dependency.

**Falsification condition:** [A] If ζ on real locked-in data does not exceed permutation baseline, but a d=2 (time+velocity) representation does, then d=1 was too lossy and this decision must be revised.

**Outcome:** Pending first session data.

---

### DECISION-002 — Coincidence criterion is temporal proximity |t_i − t_j| < ε
Date: 2026-04-15
Status: ACTIVE

**Decision:** Two onsets (one per stream) coincide if their timestamps differ by less than ε. Binary intersection: X_{ij} = 1 iff |t_i − t_j| < ε. The Gaussian kernel Φ(x) = exp(−x²/2σ²) then weights the coincidence by how close they are, where x = |t_i − t_j|.

**Reasoning:** [A] Paper §2 (Coincidence Operator Computation) lists temporal proximity `|t_i − t_j| < ε` as the primary criterion for event streams. [B] MIDI drum hits are discrete events; phase coherence (the alternative criterion) applies to oscillatory signals. Temporal proximity is the correct structural match for onset-based rhythm.

**Alternatives rejected:**
- Phase coherence (PLV): Requires continuous oscillatory signals. MIDI events are discrete; computing instantaneous phase from a sparse event stream requires interpolation that introduces noise.
- Template/structural similarity: Requires a prior groove template per bar. Not available in real time at Stage 1.

**Falsification condition:** [A] If surrogate tests (jittered streams with known ε-proximity events removed) show ζ does not drop relative to real streams, then the temporal proximity criterion is not detecting what it claims. Requires redesign of the coincidence operator.

**Outcome:** Pending implementation.

---

### DECISION-003 — ζ₄ deferred; not computable with N=2 streams
Date: 2026-04-15
Status: ACTIVE

**Decision:** Do not implement ζ₄ (quartet coincidences) for Stage 1. Compute only ζ₂ (pairwise).

**Reasoning:** [A] ζ₄ requires at least 4 streams by definition — it counts simultaneous coincidences across 4-tuples. Stage 1 has N=2 streams. The paper does not mention ζ₄ anywhere. CLAUDE.md references it from Anna's verbal recommendations, not the patent document. With N=2, there are zero 4-tuples. [C] ζ₄ is planned for when N ≥ 4 (e.g., after biometric streams are added).

**Alternatives rejected:**
- Computing ζ₄ over sub-streams (left hand / right hand split): Requires splitting the MIDI stream by pitch/channel, adding a dependency on instrument mapping. Not worth the complexity before ζ₂ is validated.

**Falsification condition:** N/A — this is a scoping decision, not an empirical claim. Revisit when N grows.

**Outcome:** Deferred by design.

---

### DECISION-004 — Symmetry vector σ deferred for Stage 1
Date: 2026-04-15
Status: ACTIVE

**Decision:** Do not implement the five-dimensional symmetry vector V_{ij}(t) = [Δφ, Δf, Δτ, Δs, Δm] for Stage 1. Compute only ζ₂.

**Reasoning:** [B] The five-dimensional vector appears in CLAUDE.md but is not defined in the paper for any specific domain, including music. The paper's coincidence map V_{ij}(t) = [D_1, ..., D_K] names dimensions for audio/visual/somatic contexts but not for MIDI rhythm streams. Implementing σ now would require defining "structure" and "motif" dimensions (Δs, Δm) without empirical grounding. [C] Planned once ζ₂ validation produces a baseline.

**Falsification condition:** If ζ₂ alone cannot distinguish locked-in from unlocked drumming above permutation baseline, then additional dimensions (σ) may be needed to capture structural alignment not captured by timing alone.

**Outcome:** Deferred by design.

---

### DECISION-005 — R(t) uses single threshold ζ_c for Stage 1
Date: 2026-04-15
Status: ACTIVE

**Decision:** Implement R(t) with a single threshold ζ_c (paper's formulation), not the two-threshold hysteresis (θ_on/θ_off) described in CLAUDE.md.

**Reasoning:** [A] The paper defines R(t) = 1 if ζ(t) ≥ ζ_c, else 0. The two-threshold design in CLAUDE.md is from Anna's verbal recommendations (not the patent) and is better engineering — but introduces a second free parameter before we have any empirical sense of ζ's range or noise characteristics. Start with the simpler form; hysteresis is a refinement once we know how much ζ fluctuates near threshold. [C] Upgrade to θ_on/θ_off after first session shows flicker behavior.

**Alternatives rejected:**
- Continuous sigmoid R(t) = 1/(1+exp(−k(ζ−ζ_c))): Also valid, also introduces parameter k without calibration data.

**Falsification condition:** [A] If R(t) flickers (switches on/off within a single bar at known lock-in events), the single-threshold design is insufficient and must be upgraded to hysteresis.

**Outcome:** Pending first session data.

---

### DECISION-006 — Measurement validation precedes agent layer
Date: 2026-04-15
Status: ACTIVE

**Decision:** Stage 1 is sequenced in two phases. Phase A: validate that ζ rises above permutation baseline when the drummer locks in. Phase B: build action scoring (listen/mirror/complement) on top. Phase B does not begin until Phase A produces a statistically meaningful result.

**Reasoning:** [A] The one falsifiable question for Stage 1 is whether ζ is a real signal — not whether the agent behaves well. Building the agent on an unvalidated ζ would make it impossible to distinguish agent failures from measurement failures. [B] This mirrors standard scientific practice: validate the instrument before using it. [A] Permutation surrogate test is the instrument validation criterion: ζ on real streams must exceed shuffled-stream baseline by at least one standard deviation at known lock-in events.

**Alternatives rejected:**
- Build agent and measurement simultaneously: Rejected because a ζ failure and an action-scoring failure look identical in session logs. Isolating the measurement layer first produces clean falsification.
- Use correlation as a proxy metric during development: Rejected because ζ and correlation are not equivalent (ζ is threshold-based and local; correlation is global and linear). Validating correlation does not validate ζ.

**Falsification condition:** [A] If ζ does not exceed surrogate baseline after multiple sessions with a drummer who self-reports locking in, Phase A has failed and the coincidence criterion or kernel must be revised before proceeding to Phase B.

**Outcome:** Pending first session data.

---

### DECISION-007 — Φ kernel is Gaussian with σ as the primary free parameter
Date: 2026-04-15
Status: ACTIVE

**Decision:** Use the Gaussian kernel Φ(x) = exp(−x²/2σ²) where x = |t_i − t_j| (inter-onset time difference in milliseconds). σ is the recognition radius — the most important tunable parameter. Initial prior: σ = 30 ms, derived from the 30 ms mirror-note success criterion in the project spec. Treat σ as the first sweep parameter once session data exists.

**Reasoning:** [A] The paper lists the Gaussian as the canonical example kernel (§2.2). The paper's methodology section confirms x is a scalar distance. The 30 ms prior is grounded in the CLAUDE.md Stage 1 success criterion ("mirror notes landing within 30ms"), which defines the perceptual threshold of rhythmic alignment for this instrument context. [B] 30 ms is also consistent with published thresholds for beat synchrony perception (~20–50 ms window).

**Alternatives rejected:**
- Laplace kernel exp(−|x|/σ): Heavier tails, less sensitive to tight coincidences. No empirical reason to prefer it over Gaussian for onset timing.
- Fixed-window binary kernel (Φ = 1 if |x| < ε, else 0): Loses graded information about how close onsets are. The Gaussian preserves that gradient, which is important for computing Ψ(t) = dζ/dt.

**Falsification condition:** [A] If sweeping σ across [10 ms, 100 ms] produces no setting where ζ_real > ζ_surrogate + σ_surrogate at known lock-in events, the Gaussian kernel is insufficient for this domain and must be replaced or the coincidence criterion revised.

**Outcome:** Pending pilot data. σ = 30 ms is the initial value; sweep begins after first session.

---

### DECISION-008 — Paper governs over CLAUDE.md when they conflict
Date: 2026-04-15
Status: ACTIVE

**Decision:** Anna Taranova's patent document (PCT/IB2025/055633) is the authoritative source for implementation decisions. CLAUDE.md is the engineering spec derived from the paper plus Anna's verbal recommendations; where they diverge, the paper wins.

**Reasoning:** [A] Confirmed by reading both documents in full on 2026-04-15. Three specific divergences found: (1) CLAUDE.md specifies two-threshold R(t) — paper has one threshold (resolved by DECISION-005); (2) CLAUDE.md specifies ζ₄ — paper does not mention it (resolved by DECISION-003); (3) CLAUDE.md specifies five-dimensional symmetry vector — paper does not define this for any domain (resolved by DECISION-004). In each case the paper's simpler formulation was adopted for Stage 1.

**Alternatives rejected:**
- Treat Anna's verbal recommendations as equal authority to the paper: Rejected because verbal recommendations are not falsifiable in the same way — they lack the formal structure that makes the paper's definitions testable.

**Falsification condition:** N/A — this is an authority hierarchy decision, not an empirical claim.

**Outcome:** Applied. DECISION-003, DECISION-004, DECISION-005 are downstream of this.

---

### DECISION-009 — σ is a runtime config parameter; default 30 ms absolute, tempo-relative mode planned
Date: 2026-04-15
Status: ACTIVE

**Decision:** σ (Gaussian kernel width / recognition radius) is exposed as a tunable parameter in `config.toml`, not hardcoded. Two modes:

```toml
[kernel]
sigma_ms = 30.0          # absolute recognition radius in milliseconds
sigma_mode = "absolute"  # "absolute" | "tempo_relative"
sigma_tempo_factor = 0.1 # σ = factor × IOI (beat duration); used only when mode = "tempo_relative"
```

Stage 1 runs `sigma_mode = "absolute"` with `sigma_ms = 30.0`. Tempo-relative mode is implemented but gated — not used until the absolute sweep produces a baseline to compare against. When tempo-relative mode is active, σ = `sigma_tempo_factor` × (60000 / bpm_estimate) ms, where bpm_estimate is the groove's known playback BPM (not the live player's estimated BPM).

**Reasoning:** [A] σ cannot be set without pilot data (OQ-001). Making it a config param rather than a constant means the sweep (DECISION-007) requires only a config edit, not a code change. [B] Tempo-relative mode is structurally appealing because it makes "coincidence" mean the same fraction of a beat regardless of tempo — but it introduces BPM estimation as a dependency before ζ₂ is validated. Deferring it to post-sweep avoids coupling two unvalidated systems. [C] Tempo-relative mode is planned; absolute mode ships first.

**Alternatives rejected:**
- Hardcode σ = 30 ms: Rejected because the sweep will require changing σ repeatedly. A config param eliminates that friction without adding complexity.
- Tempo-relative only (no absolute mode): Rejected because absolute mode is simpler, has no BPM dependency, and must be validated first.

**Falsification condition:** [A] If the σ sweep (absolute mode, range [10, 100] ms) produces no setting where ζ_real > ζ_surrogate + 1 std at known lock-in events, then absolute σ is insufficient and tempo-relative mode (or a different kernel) must be tried.

**Outcome:** Pending implementation of `config.toml` and `zeta.py`.

---

### DECISION-010 — ζ window is exponential decay with tempo-scaled τ, capped by event count
Date: 2026-04-15
Status: ACTIVE

**Decision:** ζ(t) is computed over a triple-gated window: exponential decay weighting, a hard event-count cap, and a decay floor.

A pair (i, j) is included in the ζ sum iff all three conditions hold:
1. Both events are within the last N onsets per stream (event cap — complexity bound)
2. The pair passed the binary coincidence gate at the time it occurred: |t_i − t_j| < ε (DECISION-002)
3. exp(−(t − t_{ij}) / τ(bpm)) ≥ weight_floor (decay floor — prevents hover from stale pairs)

```
ζ(t) = (1/n) Σ_{qualifying pairs} Φ(x_{ij}) · exp(−(t − t_{ij}) / τ(bpm))
```

Where:
- `τ(bpm) = tau_bars × (60000 / bpm)` — decay half-life in milliseconds, tempo-scaled
- N = `horizon_events` — hard cap on onsets retained per stream
- `weight_floor` — minimum decay weight; pairs below this are pruned from the sum

```toml
[window]
tau_bars = 1.0       # τ = tau_bars × bar_duration ms; decay half-life
horizon_events = 64  # hard cap: last N onsets per stream considered
weight_floor = 0.05  # prune pairs where exp(−age/τ) < this value (~3τ effective memory)
```

At 120 BPM: τ = 2000 ms → events from 2 bars ago weight ~0.14, 3 bars ago ~0.05 (floor boundary), older than 3 bars pruned. `horizon_events = 64` at ~16 onsets/bar covers ~4 bars before the count cap binds.

The ε gate (condition 2) and the weight_floor (condition 3) operate on different axes: ε asks "was this pair a coincidence?" (inter-onset proximity between streams); weight_floor asks "is this coincidence recent enough to still count?" A pair can pass ε legitimately but become negligible with age — weight_floor evicts it cleanly so ζ drops to zero during sparse or unlocked passages.

For Stage 1, bpm is the groove's known playback BPM from the dataset file — not a live estimate. No BPM estimation dependency.

**Reasoning:** [B] Exponential decay provides smooth recency weighting without a hard temporal cutoff. Tempo-scaling τ means "one bar ago" carries the same weight at 80 BPM and 160 BPM; ζ values are musically comparable across tempos. [A] The event-count cap bounds computation to O(N²) worst case. The weight_floor closes the hover problem: without it, genuine-but-stale coincident pairs sitting in the event-count buffer hold ζ above zero during unlocked passages, making the permutation baseline harder to interpret. weight_floor = 0.05 is a numerical negligibility cutoff, not a free musical parameter — it means "less than 5% of full weight is not worth counting."

**Alternatives rejected:**
- Fixed time window (W ms): ζ values incomparable across tempos without normalization.
- Fixed bar window (hard cutoff): Discontinuity at the boundary; decay is smoother.
- Exponential decay without event cap: Sum grows O(session_length²).
- Event count only (no decay): All N events weighted equally regardless of age.
- Decay + event cap without weight_floor: Stale pairs hover; ζ does not cleanly approach zero during unlocked passages.

**Falsification condition:** [A] If surrogate tests show ζ computed with this window does not track known lock-in/unlock events more accurately than a fixed 2000 ms window, the tempo-scaled decay adds complexity without benefit and should be simplified to Option A.

**Outcome:** Pending implementation of `zeta.py`.

---

### DECISION-011 — Rust emits single tagged JSONL stream; note_off inclusion is a config flag
Date: 2026-04-15
Status: ACTIVE

**Decision:** Rust emits a single JSONL stream on stdout. Every MIDI event — both playback and live capture — is a line in that stream, tagged with a `source` field. Playback event timestamps are the actual send time (when Rust handed the note to the MIDI output), not the scheduled time from the MIDI file.

Schema:
```json
{"t": 1718300000000000, "source": "playback", "note": 38, "velocity": 100, "type": "note_on"}
{"t": 1718300000005000, "source": "capture",  "note": 38, "velocity": 127, "type": "note_on"}
```

Fields:
- `t` — nanosecond wall-clock timestamp (same clock for all events)
- `source` — `"playback"` | `"capture"`
- `note` — MIDI note number (0–127)
- `velocity` — MIDI velocity (0–127)
- `type` — `"note_on"` | `"note_off"`

Stream separation in `zeta.py`:
```python
event_types = {"note_on", "note_off"} if config.include_note_off else {"note_on"}
stream_1 = [e for e in events if e["source"] == "playback" and e["type"] in event_types]
stream_2 = [e for e in events if e["source"] == "capture"  and e["type"] in event_types]
```

`include_note_off` is a config flag in `config.toml`:
```toml
[streams]
include_note_off = false  # include note_off timestamps as onsets in ζ computation
                          # meaningful for sustained instruments; noise for drum pads
```

Default: `false`. For drum pads, note_off timing is dominated by pad release mechanics — not musical intent — and adds noise. For sustained instruments (future streams), note_off carries duration information and should be enabled.

**Reasoning:** [A] Single pipe with tagged events preserves chronological interleaving for free and requires no additional IPC complexity. Two-pipe alternatives introduce ordering ambiguity. [A] Playback timestamps must be actual emission times: scheduler jitter, USB latency, and system load all shift when Rust sends the note relative to when it was scheduled. ζ is computed against what actually happened, not what was planned. [B] note_off carries rhythmic intent only when the musician controls note duration. Drum pad note_off is a mechanical artifact; including it by default would inflate event count with noise and create spurious coincidences between streams' release times.

**Alternatives rejected:**
- Two-pipe schema (capture on stdout, playback on fd 3): Chronological ordering between pipes requires merge logic. No benefit for two streams.
- Python owns the groove schedule from MIDI file (no playback events from Rust): Breaks ζ computation if Rust plays notes late due to system load. Ground truth must come from Rust's clock.
- Always include note_off: Adds mechanical noise for drum streams. Config flag costs nothing and preserves the option for sustained instruments.
- Always exclude note_off: Forecloses future use with melodic streams without a code change.

**Falsification condition:** [A] If playback jitter measured across sessions is consistently < 1 ms (below ε), then using scheduled times instead of actual emission times would be equivalent, and Rust need not emit playback events at all. Measure jitter in first session log.

**Outcome:** Pending Rust implementation. Resolves OQ-003.

---

### DECISION-012 — ε is derived as epsilon_sigma_factor × σ; both are independently sweepable config params
Date: 2026-04-15
Status: ACTIVE

**Decision:** ε (binary coincidence threshold) is not set directly. It is derived at runtime as:

```
ε = epsilon_sigma_factor × sigma_ms
```

Both `epsilon_sigma_factor` and `sigma_ms` are independently tunable parameters in `config.toml`:

```toml
[kernel]
sigma_ms = 30.0
sigma_mode = "absolute"
sigma_tempo_factor = 0.1
epsilon_sigma_factor = 2.0  # ε = factor × sigma_ms; sweep independently of σ
```

At defaults: ε = 2.0 × 30 ms = 60 ms. A pair at the boundary contributes Φ = exp(−2) ≈ 0.14 — small but non-negligible.

`epsilon_sigma_factor` is a sweep parameter in its own right. The σ sweep (DECISION-007) and the ε sweep are independent: σ controls kernel width, `epsilon_sigma_factor` controls where the binary gate falls relative to that kernel. They interact but are not redundant — changing `epsilon_sigma_factor` at fixed σ shifts how much of the Gaussian's tail is admitted before the binary gate cuts off.

**Reasoning:** [A] Deriving ε from σ keeps the sweep coherent — when σ changes, ε follows by default and the binary gate always falls at a consistent fraction of the kernel width. [A] Making `epsilon_sigma_factor` independently variable allows testing whether a tighter or looser gate (e.g., 1.5σ vs. 3σ) changes ζ's ability to distinguish locked-in from unlocked passages, without conflating that with σ's effect on kernel shape. [B] The two-layer design (binary gate + Gaussian weighting, DECISION-002) requires ε to be meaningful as a gate — if `epsilon_sigma_factor` is too large (≥ 3σ), Φ at the boundary is ~0.01 and the binary layer is effectively vestigial. If too small (≤ 1σ), the gate cuts off pairs that would contribute 61% of a perfect coincidence. The 2.0 default sits at a principled midpoint.

**Alternatives rejected:**
- Hardcode ε = 2σ: Prevents testing whether `epsilon_sigma_factor` matters. A config param costs nothing.
- ε fully independent of σ (fixed ms value): Two parameters with no structural relationship; sweep space grows without a principled axis of variation.
- ε = 3σ fixed: Collapses the binary layer into near-redundancy. Contradicts the two-layer design intent.

**Falsification condition:** [A] If sweeping `epsilon_sigma_factor` across [1.0, 3.0] at fixed σ produces no difference in ζ_real vs. ζ_surrogate separation, then the binary gate is redundant and the two-layer design can be simplified to a pure Gaussian (no ε).

**Outcome:** Pending implementation. Resolves OQ-004.

---

### DECISION-013 — UI is pygame; audio is pygame.mixer with 99Sounds WAV samples; timeline scrolls left to right
Date: 2026-04-15
Status: ACTIVE

**Decision:** The main UI is written in Python using pygame. Audio playback uses pygame.mixer with individual WAV one-shot files from the [99Sounds 99 Drum Samples](https://99sounds.org/drum-samples/) collection (219 samples, 24-bit WAV, royalty-free). The timeline scrolls left to right — oldest events at left, newest at right, present entering from the right edge (DAW orientation).

**UI layout:**
- Top region: scrolling timeline — groove lane (top), user lane (bottom), ζ band reserved below (rendered when zeta.py is wired in)
- Middle region: two pad grids — groove pads (left) and user pads (right), each flashing on note events
- Bottom region: kit panel (pad → WAV mapping), Pseta options (σ, ε, τ, etc.), transport controls (load, play, stop, loop, BPM display)

**Kit config in `config.toml`** — GM note number → WAV file path:
```toml
[kit]
36 = "samples/kick.wav"
38 = "samples/snare.wav"
42 = "samples/hihat_closed.wav"
46 = "samples/hihat_open.wav"
49 = "samples/crash.wav"
51 = "samples/ride.wav"
41 = "samples/tom_low.wav"
45 = "samples/tom_mid.wav"
50 = "samples/tom_high.wav"
```

Kit panel edits this mapping live — any WAV can be swapped per pad without restarting.

**Audio routing — dual path:**
- Python pygame.mixer: primary audio engine, triggers WAV on every `note_on` event from either stream
- Rust MIDI out: optional parallel path, enabled via `{"cmd": "midi_out_en", "enabled": true}`, routes to external hardware synth or soft synth independently

**samples/ directory:** WAV files are not committed to the repo. `samples/README.md` documents the source (99Sounds URL) and license. A setup script downloads and places them.

**Reasoning:** [B] pygame is a natural fit for real-time pad animation and a game-loop event model — the main loop drains the Rust JSONL pipe, updates state, and renders each frame. [A] pygame.mixer handles low-latency WAV playback without additional dependencies. [B] 99Sounds provides a complete kit (kick, snare, hi-hat, toms, cymbals) in one download with a permissive royalty-free license; assembling from Freesound CC0 packs would require sourcing and mapping multiple individual files. [B] DAW-orientation timeline (left=past, right=present) is the convention musicians already read; it also positions the ζ analysis band directly below the hit lanes, making the signal-to-groove relationship visually immediate.

**Alternatives rejected:**
- fluidsynth + SF2 soundfont: Adds system library dependency; pygame.mixer + WAV is simpler and sufficient for one-shot drum hits.
- Freesound CC0 packs: Strictly CC0 but requires assembling from multiple sources; 99Sounds is complete in one download.
- Terminal-only readout: Deferred to this UI design per user direction.
- Right-to-left timeline (seismograph style): Less familiar for musicians; DAW orientation chosen.

**Falsification condition:** N/A — UI/audio design decisions. Revisit if pygame.mixer latency causes audible lag relative to MIDI timestamps from Rust.

**Outcome:** Pending implementation.

---

## Open questions (not yet decided)

- **OQ-001** — ~~What value of σ to use?~~ RESOLVED by DECISION-009: σ is a config param, default 30 ms absolute, swept after first session. Tempo-relative mode planned post-sweep.
- **OQ-002** — ~~What is the temporal window W?~~ RESOLVED by DECISION-010: exponential decay with τ = tau_bars × bar_duration, hard-capped at horizon_events onsets per stream.
- **OQ-003** — ~~What is the Rust → Python JSONL schema?~~ RESOLVED by DECISION-011: single tagged stream on stdout; actual emission timestamps; note_off inclusion gated by config flag.
- **OQ-004** — ~~What is the initial value of ε?~~ RESOLVED by DECISION-012: ε = epsilon_sigma_factor × sigma_ms; default factor 2.0; both independently sweepable.
