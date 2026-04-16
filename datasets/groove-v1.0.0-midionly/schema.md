# Schema — Groove MIDI Dataset v1.0.0 (MIDI-only)

**Source:** Google Magenta · https://magenta.tensorflow.org/datasets/groove  
**License:** CC BY 4.0  
**Version:** 1.0.0, MIDI-only (no audio)  
**Role in Pseta:** Prior for groove_map initialization. Provides density map distributions across styles and tempos before any live session data is collected.

## Obtaining the data

```bash
# Download MIDI-only version (~60MB)
wget https://storage.googleapis.com/magentadata/datasets/groove/groove-v1.0.0-midionly.zip
unzip groove-v1.0.0-midionly.zip -d datasets/
```

## Contents

| Property | Value |
|---|---|
| Drummers | 10 (drummer1–drummer10) |
| Files | 1,150 MIDI files |
| Split | 897 train / 124 validation / 129 test |
| Styles | 40+ (funk, jazz, rock, soul, afrobeat, hiphop, reggae, …) |
| Tempo range | ~60–215 BPM |
| Time signatures | Primarily 4-4; some 3-4, 5-4, 6-8 |
| Beat types | beat, fill |

## Directory structure

```
groove-v1.0.0-midionly/
├── schema.md               # this file
└── groove/
    ├── README              # upstream documentation
    ├── LICENSE             # CC BY 4.0
    ├── info.csv            # metadata index (see fields below)
    └── drummer{1-10}/
        ├── eval_session/   # held-out evaluation recordings
        ├── session1/
        ├── session2/
        └── session3/
```

## info.csv fields

| Field | Type | Description |
|---|---|---|
| drummer | string | Drummer ID (drummer1–drummer10) |
| session | string | Session path |
| id | string | Unique clip ID |
| style | string | Genre/style label (e.g. `funk/groove1`, `jazz-swing`) |
| bpm | int | Tempo in BPM |
| beat_type | string | `beat` or `fill` |
| time_signature | string | e.g. `4-4`, `3-4` |
| midi_filename | string | Relative path to .mid file |
| audio_filename | string | Relative path to .wav (not present in MIDI-only version) |
| duration | float | Clip duration in seconds |
| split | string | `train`, `validation`, or `test` |

## Intended use in Pseta

- **groove_map initialization:** Extract onset density histograms by subdivision position from the training split to seed `groove_map` priors before live session data accumulates.
- **ζ annotation target:** Annotate clips with ζ/σ values as a labeled resource for validating the measurement framework offline before live experiments.
- **Style stratification:** Use `style` and `bpm` fields to select priors appropriate to the live session's detected tempo and feel.

## [B] Notes

- [B] Audio files not included in this version — rhythm structure only, no timbre or dynamics
- [B] All recordings are isolated drummers (no ensemble context); ζ across streams cannot be computed from this dataset alone — it is a single-stream prior source
- [C] TODO: annotate training split with ζ/σ values and publish as open resource (Stage 1 open science output)
