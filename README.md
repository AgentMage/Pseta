# Pseta

**A coincidence-driven groove agent and open measurement framework for synchrony**

Pseta (Ψζ) listens to a musician playing a drum pad and begins grooving with them — not by predicting what comes next, but by detecting when coincidence density across streams is high enough that action increases it further. The name fuses Ψ (psi — the signal) and ζ (zeta — the density metric the system maximizes).

The drum groove agent is the first experiment. The measurement framework underneath it is the actual contribution: a real-time, stream-agnostic implementation of Anna Taranova's Ψ-model that quantifies synchrony as a computable quantity across any set of timestamped data streams — acoustic, physiological, or neural.

---

## The core idea

Most AI agents act by predicting the future. Pseta acts by measuring the present — specifically, how much independent streams of data are coinciding right now, and whether that coincidence is accumulating.

The measurement is grounded in the Ψ-model (Anna Taranova, PCT/IB2025/055633, github.com/psi-model/psi-model-by-anna-taranova):

```
Ψ(t) = ∂/∂t Σ [Sᵢ(t) ∩ Sⱼ(t)] → R(t)
```

Where `Sᵢ(t)` are independent temporal streams, `∩` is a coincidence operator, and `R(t)` is system response when coincidence density crosses a threshold. The primary quantity is:

- **ζ (density)** — rate of active cross-stream coincidences per unit time. The metric everything optimizes.

Two further quantities are defined in the model and will be operationalized in later stages:

- **σ (symmetry)** — structural mirroring between streams. Coherence multiplier. Deferred until ζ is validated.
- **ρ (resonance)** — amplification when three or more streams coincide. Requires N ≥ 3 streams. Deferred.

Once measurement is validated, the agent runs one continuous loop:

```
observe → update state → score actions → act
```

At every tick it scores three possible actions — `listen`, `mirror`, `complement` — by their expected gain in ζ. No mode switches. No programmed transitions. The behavioral arc (silence early, mimicry as pattern stabilizes, gap-filling as the model fills in) emerges from the scoring function because each action's expected ζ contribution changes as knowledge accumulates.

---

## Build arc

### Stage 1 — Music (current)
*Validate that ζ is computable and statistically meaningful across two MIDI streams*

**Immediate target — measurement proof of concept:**

Two streams run simultaneously:
- Stream 1: MIDI playback from `datasets/groove-v1.0.0-midionly/` (known timing)
- Stream 2: Live drum pad input captured by the Rust binary (nanosecond timestamps)

`zeta.py` computes ζ(t) across both streams in real time using temporal proximity as the coincidence criterion: two onsets coincide if `|t₁ − t₂| < ε`, weighted by Φ(x) = exp(−x²/2σ²). A pygame UI displays both streams and the live ζ(t) plot. Every tick is logged to JSONL.

**The one validation question:** Does ζ rise measurably above permutation baseline when the drummer locks in with the groove, and drop when they don't?

This must be answered before the agent layer is built. Action scoring (listen/mirror/complement) comes after measurement is validated. Every session logs to JSONL as training data for the policy that follows.

**Validation criteria:**
- ζ on real streams exceeds phase-shuffled surrogate baseline by at least one standard deviation at known lock-in events
- `bpm_variance` < 0.05 after 4–8 bars
- Mirror notes land within 30ms of player hits (once agent is active)
- Action distribution shifts listen → mirror → complement over session time (once agent is active)

### Stage 2 — Biometrics (distant future)
*Validate that ζ generalizes across stream types*

Contingent on Stage 1 producing valid results. Add HRV, EDA, respiration as additional streams. ζ measures coincidence across acoustic and physiological domains simultaneously. The framework requires no modification — only new stream interpreters. Validated against WESAD dataset baselines.

### Stage 3 — Neural signals (distant future)
*Validate noninvasive cross-modal synchrony detection*

Contingent on Stage 2 validation. Consumer-grade EEG as an additional stream. ζ computed across acoustic, physiological, and neural signals simultaneously. This is the stage at which Pseta becomes a scientific instrument for studying human-machine synchrony without invasive hardware.

Stages 2 and 3 are a multi-year research direction. Do not design current code around them.

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│  Rust binary: midi_capture                       │
│  · Plays groove from dataset (Stream 1)          │
│  · Captures live pad input (Stream 2)            │
│  · Nanosecond wall-clock timestamps              │
│  · No musical intelligence — pure I/O            │
└────────────────────┬─────────────────────────────┘
                     │ JSONL stream (both streams tagged)
┌────────────────────▼─────────────────────────────┐
│  Python: main.py (pygame UI + agent host)        │
│                                                  │
│  UI layer                                        │
│  ├── Timeline: groove + user lanes + ζ(t) band  │
│  ├── Pad grids: 4×4 GM drum pads per stream     │
│  ├── MIDI monitors: scrolling per-source logs   │
│  ├── Transport: load/play/stop/loop, port sel.  │
│  └── Stream config: pad color = stream pair     │
│                                                  │
│  zeta.py — measurement                          │
│  ├── S₁(t): groove playback onset timestamps    │
│  ├── S₂(t): live pad hit timestamps             │
│  ├── coincidence: |t₁ − t₂| < ε                │
│  ├── Φ(x) = exp(−x²/2σ²), x = time difference  │
│  ├── ζ(t): rolling coincidence density          │
│  └── R(t): threshold activation                 │
│                                                  │
│  state.py — accumulates session knowledge        │
│  ├── bpm_estimate, bpm_variance                  │
│  ├── beat_phase, bar_phase                       │
│  ├── groove_map, recurrence                      │
│  └── ζ_current, ζ_history                       │
│                                                  │
│  actions.py — scores and executes [post-Stage 1] │
│  ├── gain(listen)     = f(uncertainty)           │
│  ├── gain(mirror)     = f(confidence, ζ)         │
│  └── gain(complement) = f(recurrence, ζ)         │
└────────────────────┬─────────────────────────────┘
                     │ JSONL session log
┌────────────────────▼─────────────────────────────┐
│  sessions/YYYY-MM-DD_HH-MM-SS.jsonl              │
│  Every tick: both stream states, ζ, R(t),        │
│  action scores, action taken, ζ before/after     │
└──────────────────────────────────────────────────┘
```

Pads represent streams. The comparisons panel lists which pad pairs are being compared for coincidence — one entry per color shared across a groove pad and a user pad. Assign the same color to two pads to add a comparison; the ζ computation tracks coincidences across each listed pair.

---

## Nomenclature

Pseta uses a unified vocabulary across math, code, and natural language:

| Canonical | Math | Code | Speech |
|---|---|---|---|
| stream | Sᵢ(t) | `stream` | stream |
| coincidence | Sᵢ ∩ Sⱼ | `coincidence` | hit |
| density | ζ(t) | `density` | density |
| symmetry | σ(t) | `symmetry` | symmetry |
| resonance | ρ(t) | `resonance` | resonance |
| context | H(t) | `context` | context |
| gain | ΔΨ | `gain` | gain |
| signal | Ψ(t) | `signal` | the signal |
| activation | R(t) | `activation` | activation |
| groove map | — | `groove_map` | groove map |
| recurrence | — | `recurrence` | recurrence |
| horizon | W | `horizon` | horizon |
| action | a(t) | `action` | action |
| listen / mirror / complement | — | `Action.LISTEN` `.MIRROR` `.COMPLEMENT` | listen, mirror, complement |

---

## Stack

**Rust** — `midir`, `serde_json`
Real-time MIDI capture and output. Wall-clock timestamping at nanosecond precision. Dumb pipe: no musical intelligence, just timing accuracy.

**Python** — `numpy`, `pygame`, `tomllib`
All musical intelligence: state management, ζ computation, action scoring, session logging. pygame UI is the operator surface. Fast to iterate. Runs on Pi.

**Config** — `config.toml`
All tunable parameters externalized: MIDI ports, tempo range, subdivision, ζ window, action scoring weights.

**Session logs** — `./sessions/YYYY-MM-DD_HH-MM-SS.jsonl`
Every tick logged. Queryable with DuckDB. Foundation for all future ML work.

---

## Project structure

```
pseta/
├── Cargo.toml
├── src/
│   └── main.rs          # Rust MIDI capture + playback
├── main.py              # pygame UI + agent host
├── zeta.py              # ζ, σ, ρ computation  [not yet built]
├── state.py             # State dataclass + update functions  [not yet built]
├── actions.py           # Action scoring + MIDI execution  [not yet built]
├── logger.py            # Session JSONL logger  [not yet built]
├── config.toml          # All tunable parameters
├── run.sh               # One-command launch
├── sessions/            # Session logs (auto-created)
└── datasets/            # Groove MIDI Dataset (not committed)
```

---

## Running

```bash
# Build Rust binary
cargo build --release

# Install Python dependencies
pip install pygame tomli

# Run
./run.sh
```

`run.sh` builds the Rust binary, activates the Python venv, and launches the pygame UI. The UI starts the Rust subprocess and manages the MIDI pipe. Deploys to Pi with `rsync` + `systemctl restart pseta`.

---

## Session logs

Every tick produces one JSONL line:

```json
{
  "t": 1234567890123456789,
  "event_type": "tick",
  "state": {
    "bpm_estimate": 120.4,
    "bpm_variance": 0.02,
    "density_map_confidence": 0.73,
    "recurrence": 0.81,
    "zeta_current": 0.34
  },
  "action_scores": {
    "listen": 0.12,
    "mirror": 0.31,
    "complement": 0.57
  },
  "action_taken": "complement",
  "zeta_before": 0.34,
  "zeta_after": 0.38
}
```

Queryable with DuckDB:
```sql
SELECT action_taken, AVG(zeta_after - zeta_before) as mean_gain
FROM read_ndjson_auto('sessions/*.jsonl')
WHERE event_type = 'tick'
GROUP BY action_taken;
```

---

## What success looks like

**Stage 1 measurement validation (current target):**

- ζ on real streams exceeds phase-shuffled surrogate baseline at known lock-in events
- ζ drops toward baseline when the drummer is out of sync
- Both conditions reproducible across independent sessions

**Stage 1 agent validation (follows measurement):**

- `bpm_variance` < 0.05 after 4–8 bars
- `density_map_confidence` > 0.5 after 4–8 bars
- Mirror notes landing within 30ms of player hits
- Action distribution visibly shifted toward `complement` over session time
- ζ trending upward in session log

The session log makes all of this queryable. Without surrogate baselines, ζ > 0 has no statistical meaning.

---

## Scientific context

Anna Taranova's Ψ-model (patent PCT/IB2025/055633) is the mathematical foundation. The paper is the authoritative source for all implementation decisions — when CLAUDE.md, the scope doc, or any other document conflicts with it, the paper governs.

Design decisions, their reasoning, rejected alternatives, and falsification conditions are tracked in `lab/decisions.md`. This is a rolling log — failures are first-class entries and are never deleted.

Pseta is an open-source engineering implementation of the Ψ-model, beginning with music as a controlled experimental domain where ground truth (locked-in vs. unlocked drumming) is perceptually verifiable.

---

## Collaborators

**Anna Taranova** — Ψ-model author (PCT/IB2025/055633). Theoretical framework. Active collaboration.

**Bernard Beitman MD** — Yale Medical School / University of Virginia. Synchronicity research. Introduced Lilly and Anna.

**Dmytro Maidaniuk** — Signal processing engineer. Independent Ψ-model validation on WESAD biometric dataset.

**Jonathan Zap** — Author, *The Singularity Archetype*. AI and consciousness research.

---

## Open science commitment

All code is MIT licensed. Session log schema, measurement library, and experimental protocols are published as living documents updated with each iteration. Failures are documented in real time alongside successes — what the hand-crafted scoring function gets wrong is as useful to the field as what it gets right.

The goal is replicability by anyone with a Pi, a MIDI controller, and a consumer EEG — not replicability only in a fully equipped lab.

---

## Status

**April 2026** — Rust MIDI binary and pygame UI complete. Both streams (groove playback + live capture) flow into the UI with real-time visualization. Stream pair configuration via pad color assignment is working. Next: `zeta.py` — the Φ kernel, ζ(t) computation, and permutation baseline. Agent layer (action scoring) follows measurement validation.

---

*Pseta is a public good. The framework, the code, the data, and the experimental protocols belong to the field.*

*Lilly Fiorino · lilly.fiorino@gmail.com*
*Theoretical anchor: Anna Taranova, Ψ-Model PCT/IB2025/055633*
