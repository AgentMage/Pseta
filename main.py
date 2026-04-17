"""
Pseta — main UI
pygame frontend: timeline, pad grids, kit panel, transport, Pseta options.
Launches Rust midi_capture binary as a subprocess; communicates via JSONL pipe.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tomllib
from collections import defaultdict

import pygame

import zeta as zeta_mod

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH   = "config.toml"
SETTINGS_PATH = "settings.json"

def load_config():
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)

def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_settings(state: "AppState"):
    data = {
        "groove_cc":        state.groove_cc,
        "groove_kit_paths": {str(k): v for k, v in state.groove_kit_paths.items()},
        "user_cc":             state.user_cc,
        "user_kit_paths":      {str(k): v for k, v in state.user_kit_paths.items()},
        "user_timeline_notes": sorted(state.user_timeline_notes),
        "groove_midi_path": state.loaded_path,
        "input_port":       state.input_port,
        "output_port":      state.output_port,
        "pad_colors":       {f"{s}_{n}": list(c) for (s, n), c in state.pad_colors.items()},
        "pseta_options": {
            "bpm":            state.opt_bpm,
            "beats_per_bar":  state.opt_beats_per_bar,
            "sigma_ms":       state.opt_sigma_ms,
            "epsilon_factor": state.opt_epsilon_factor,
            "tau_bars":       state.opt_tau_bars,
            "theta_c":        state.opt_theta_c,
        },
    }
    with open(SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

W, H       = 1280, 720
FPS        = 60

# Pseta option metadata: key → (step, lo, hi)
OPT_META = {
    "bpm":            (1.0,  40.0,  240.0),
    "beats_per_bar":  (1,    1,      12),
    "sigma_ms":       (1.0,  1.0,   200.0),
    "epsilon_factor": (0.1,  0.5,     5.0),
    "tau_bars":       (0.25, 0.25,    8.0),
    "theta_c":        (0.05, 0.05,    0.95),
}

# Colours
BG         = (18,  18,  18)
PANEL_BG   = (28,  28,  28)
BORDER     = (50,  50,  50)
TEXT       = (180, 180, 180)
TEXT_DIM   = (90,  90,  90)
GROOVE_HIT = (68,  136, 255)   # blue  — groove / playback lane
USER_HIT   = (255, 136, 68)    # amber — user / capture lane
ZETA_LINE  = (120, 220, 120)   # green — ζ(t) reserved band
BTN_IDLE   = (45,  45,  45)
BTN_HOV    = (65,  65,  65)
BTN_ACT    = (90,  60,  20)
PAD_IDLE   = (40,  40,  40)

# 15 pad colors (+ None = blank/uncolored)
PAD_PALETTE = [
    (220, 60,  60),   # red
    (220, 130, 60),   # orange
    (220, 210, 60),   # yellow
    (130, 210, 60),   # lime
    (60,  200, 100),  # green
    (60,  200, 190),  # teal
    (60,  150, 220),  # sky
    (60,  80,  220),  # blue
    (120, 60,  220),  # indigo
    (190, 60,  220),  # purple
    (220, 60,  190),  # pink
    (220, 60,  110),  # rose
    (210, 210, 210),  # white
    (140, 140, 140),  # silver
    (80,  80,  80),   # gray
]
COLOR_BTN_SIZE  = 11  # swatch button on each pad (px)
COLOR_PICK_SIZE = 18  # each swatch in the picker panel
COLOR_PICK_GAP  = 2
COLOR_PICK_COLS = 4

# Region rects (x, y, w, h)
STATUS_BAR_H = 22
OPT_PANEL_W  = 280                  # right-side Pseta Options panel
_MAIN_W      = W - OPT_PANEL_W     # 1000 — width of the non-options area

TIMELINE_R  = pygame.Rect(0,        0,               W,          int(H * 0.35))
PADS_R      = pygame.Rect(0,        int(H * 0.35),   _MAIN_W,    int(H * 0.32))
CONTROLS_R  = pygame.Rect(0,        int(H * 0.67),   _MAIN_W,    int(H * 0.33) - STATUS_BAR_H)
STATUS_R    = pygame.Rect(0,        H - STATUS_BAR_H, W,         STATUS_BAR_H)
OPT_PANEL_R = pygame.Rect(_MAIN_W,  int(H * 0.35),   OPT_PANEL_W,
                           H - STATUS_BAR_H - int(H * 0.35))

TIMELINE_SECS = 8.0      # seconds of history visible in timeline

# ---------------------------------------------------------------------------
# Pad grid definition (GM note → label, grid position)
# ---------------------------------------------------------------------------

PAD_FLASH  = 12   # frames a pad stays bright after a hit

# GM drum note names (channel 10)
GM_DRUMS = {
    35: "Kick 2",   36: "Kick",
    37: "X-Stick",  38: "Snare",     39: "Clap",      40: "Snare 2",
    41: "Tom 1",    42: "HH Closed", 43: "Tom 2",     44: "HH Pedal",
    45: "Tom 3",    46: "HH Open",   47: "Tom 4",     48: "Tom 5",
    49: "Crash 1",  50: "Tom 6",     51: "Ride 1",    52: "China",
    53: "Ride Bell",54: "Tamb",      55: "Splash",    56: "Cowbell",
    57: "Crash 2",  58: "Vibraslap", 59: "Ride 2",
}

# ---------------------------------------------------------------------------
# Rust subprocess
# ---------------------------------------------------------------------------

RUST_BIN = "./target/release/midi_capture"

class RustBridge:
    def __init__(self):
        self.proc      = None
        self.event_q   = queue.Queue()
        self.cmd_q     = queue.Queue()
        self._alive    = False

    def start(self):
        if not os.path.exists(RUST_BIN):
            print(f"[pseta] Rust binary not found at {RUST_BIN}. Run: cargo build --release")
            return False
        self.proc = subprocess.Popen(
            [RUST_BIN],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
        )
        self._alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._writer, daemon=True).start()
        return True

    def send(self, **kwargs):
        self.cmd_q.put(json.dumps(kwargs))

    def stop(self):
        self._alive = False
        self.send(cmd="quit")
        if self.proc:
            self.proc.wait(timeout=2)

    def _reader(self):
        for raw in self.proc.stdout:
            line = raw.decode().strip()
            if not line:
                continue
            try:
                self.event_q.put(json.loads(line))
            except json.JSONDecodeError:
                pass

    def _writer(self):
        while self._alive:
            try:
                msg = self.cmd_q.get(timeout=0.1)
                self.proc.stdin.write((msg + "\n").encode())
                self.proc.stdin.flush()
            except queue.Empty:
                pass
            except BrokenPipeError:
                break

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

class AudioEngine:
    def __init__(self, config):
        pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(32)
        self.kit        = {}   # note → Sound  (user/capture)
        self.groove_kit = {}   # note → Sound  (groove/playback)

    def play(self, note: int, velocity: int, source: str = "capture"):
        kit   = self.groove_kit if source == "playback" else self.kit
        sound = kit.get(note)
        if sound:
            sound.set_volume(velocity / 127.0)
            sound.play()

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AppState:
    def __init__(self, config):
        self.config        = config
        self.groove_bpm         = 120.0
        self.groove_duration_us = 0
        self.playback_start_ns  = None   # wall-clock ns at last play command
        self.bar_origin_s       = 0.0    # wall-clock seconds at last play start; anchors bar grid
        self.is_playing    = False
        self.loop_enabled  = False
        self.input_ports   = []
        self.output_ports  = []
        saved = load_settings()
        self.loaded_path   = saved.get("groove_midi_path")
        self.input_port    = saved.get("input_port")  or config.get("midi", {}).get("input_port", "")
        self.output_port   = saved.get("output_port") or config.get("midi", {}).get("output_port", "")
        self.midi_out_en   = config.get("midi", {}).get("midi_out_enabled", False)
        self.status_msg    = "Ready"
        self.port_picker   = None   # None | "input" | "output"

        # Pseta options — live-adjustable, loaded from config, overridable via UI
        k = config.get("kernel", {})
        w = config.get("window", {})
        z = config.get("zeta",   {})
        pseta = saved.get("pseta_options", {})
        self.opt_bpm                = float(pseta.get("bpm",                120.0))
        self.opt_beats_per_bar      = int(  pseta.get("beats_per_bar",      4))
        self.opt_sigma_ms           = float(pseta.get("sigma_ms",           k.get("sigma_ms",           30.0)))
        self.opt_epsilon_factor     = float(pseta.get("epsilon_factor",     k.get("epsilon_sigma_factor", 2.0)))
        self.opt_tau_bars           = float(pseta.get("tau_bars",           w.get("tau_bars",            1.0)))
        self.opt_theta_c            = float(pseta.get("theta_c",            z.get("theta_c",             0.5)))

        # Groove pads (16-pad grid)
        self.groove_cc          = saved.get("groove_cc")
        self.groove_remove_mode = False
        self.groove_kit_paths   = {int(k): v for k, v in saved.get("groove_kit_paths", {}).items()}

        # User pads (16-pad grid)
        self.user_cc             = saved.get("user_cc")
        self.user_remove_mode    = False
        self.user_timeline_mode  = False
        self.user_kit_paths      = {int(k): v for k, v in saved.get("user_kit_paths", {}).items()}
        # notes whose hits appear on the timeline; auto-includes any note with a saved sound
        saved_tl = set(saved.get("user_timeline_notes", []))
        self.user_timeline_notes = saved_tl | set(self.user_kit_paths.keys())

        # Pad colors: (source, note) → (r, g, b); absent = no color / not on timeline
        saved_cols = saved.get("pad_colors", {})
        self.pad_colors: dict = {}
        for key, col in saved_cols.items():
            parts = key.rsplit("_", 1)
            if len(parts) == 2:
                self.pad_colors[(parts[0], int(parts[1]))] = tuple(col)

        # Color picker state: (source, note) when open, None when closed
        self.color_picker_open:   tuple | None         = None
        self.color_picker_anchor: pygame.Rect | None   = None

        # Per-note flash counters, keyed by (source, note)
        self.flash = {}    # (source, note) → frames remaining

        # Timeline event buffer: list of {"t": float, "source": str, "note": int}
        self.timeline = []

        # MIDI monitor log: recent raw events, newest last
        self.midi_log = []   # list of event dicts, max 120

        # [A] Per-stream onset timestamps (seconds), keyed by (source, note)
        self.onset_streams: dict[tuple, list[float]] = defaultdict(list)

        # [A] ζ(t) history for timeline rendering: list of (t_sec, ZetaResult)
        self.zeta_history: list[tuple[float, zeta_mod.ZetaResult]] = []
        self._zeta_tick_last: float = 0.0
        self._zeta_tick_interval: float = 0.05   # compute ζ every 50 ms

        # Pseta options slider drag state
        self.opt_drag_key: str | None = None

        # Async file-dialog results: items are {"action": str, "path": str|None, ...}
        self.file_pick_q    = queue.Queue()
        self.file_dialog_open = False   # True while a native file dialog is in flight

    def push_event(self, ev: dict):
        """Process a MIDI note event from Rust."""
        t_sec  = ev["t"] / 1e9
        source = ev["source"]
        note   = ev["note"]
        key    = (source, note)

        if ev["type"] == "note_on":
            self.flash[key] = PAD_FLASH
            # [A] Record onset for ζ computation
            self.onset_streams[key].append(t_sec)
        elif ev["type"] == "note_off":
            self.flash[key] = max(self.flash.get(key, 0), PAD_FLASH // 2)

        pad_col = self.pad_colors.get((source, note))
        if pad_col is not None:
            self.timeline.append({"t": t_sec, "source": source, "note": note,
                                   "type": ev["type"], "velocity": ev["velocity"],
                                   "color": pad_col})
        self.midi_log.append({"t": t_sec, "source": source, "note": note,
                               "type": ev["type"], "velocity": ev["velocity"]})

        # Trim
        cutoff = time.time() - TIMELINE_SECS - 2.0
        self.timeline = [e for e in self.timeline if e["t"] > cutoff]
        if len(self.midi_log) > 120:
            self.midi_log = self.midi_log[-120:]

        # Prune onset streams beyond max horizon (30 s)
        onset_cutoff = t_sec - 30.0
        for k in self.onset_streams:
            lst = self.onset_streams[k]
            if lst and lst[0] < onset_cutoff:
                self.onset_streams[k] = [o for o in lst if o >= onset_cutoff]

    def tick_zeta(self):
        """[A] Recompute ζ(t) from the current stream pair pool if interval has elapsed."""
        now = time.time()
        if now - self._zeta_tick_last < self._zeta_tick_interval:
            return
        self._zeta_tick_last = now

        sigma_s   = self.opt_sigma_ms / 1000.0
        epsilon_s = sigma_s * self.opt_epsilon_factor
        # horizon: tau_bars × bar duration from user-set tempo + time sig
        bpm   = max(40.0, self.opt_bpm)
        bar_s = self.opt_beats_per_bar * 60.0 / bpm
        horizon  = max(2.0, self.opt_tau_bars * bar_s)

        pairs = zeta_mod.pairs_from_active_pads(
            self.pad_colors,
            dict(self.onset_streams),
        )
        result = zeta_mod.compute(pairs, now, sigma_s, epsilon_s, horizon)
        self.zeta_history.append((now, result))

        # Keep only TIMELINE_SECS + 2 s of history
        cutoff = now - TIMELINE_SECS - 2.0
        self.zeta_history = [(t, r) for t, r in self.zeta_history if t > cutoff]

    def tick_flash(self):
        for k in list(self.flash):
            self.flash[k] -= 1
            if self.flash[k] <= 0:
                del self.flash[k]

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def draw_rect_border(surf, rect, bg, border, radius=4):
    pygame.draw.rect(surf, bg,     rect, border_radius=radius)
    pygame.draw.rect(surf, border, rect, width=1, border_radius=radius)

def button(surf, font, rect, label, active=False, hover=False):
    bg = BTN_ACT if active else (BTN_HOV if hover else BTN_IDLE)
    draw_rect_border(surf, rect, bg, BORDER, radius=4)
    txt = font.render(label, True, TEXT)
    surf.blit(txt, txt.get_rect(center=rect.center))
    return rect

# ---------------------------------------------------------------------------
# Draw sections
# ---------------------------------------------------------------------------

def draw_timeline(surf, font_sm, state: AppState):
    r = TIMELINE_R
    draw_rect_border(surf, r, PANEL_BG, BORDER, radius=0)

    now       = time.time()
    tw        = r.width - 4
    lane_h    = int(r.height * 0.30)
    groove_y  = r.y + int(r.height * 0.05)
    user_y    = groove_y + lane_h + 4
    zeta_y    = user_y  + lane_h + 4
    zeta_h    = r.height - (zeta_y - r.y) - 4
    px_per_s  = tw / TIMELINE_SECS

    # Derive option values used in overlays
    sigma_s   = state.opt_sigma_ms / 1000.0
    epsilon_s = sigma_s * state.opt_epsilon_factor
    bpm_safe  = max(40.0, state.opt_bpm)
    bar_s_opt = state.opt_beats_per_bar * 60.0 / bpm_safe
    horizon_s = max(2.0, state.opt_tau_bars * bar_s_opt)

    # Lane backgrounds
    pygame.draw.rect(surf, (22, 30, 45), (r.x+2, groove_y, tw, lane_h))
    pygame.draw.rect(surf, (45, 28, 18), (r.x+2, user_y,   tw, lane_h))
    pygame.draw.rect(surf, (20, 36, 20), (r.x+2, zeta_y,   tw, zeta_h))

    # τ horizon shading — the active ζ analysis window, from right (now) leftward
    lanes_top    = groove_y
    lanes_bottom = zeta_y + zeta_h
    horizon_px = min(tw, int(horizon_s * px_per_s))
    hx = r.x + 2 + tw - horizon_px
    h_surf = pygame.Surface((horizon_px, lanes_bottom - lanes_top), pygame.SRCALPHA)
    h_surf.fill((255, 255, 255, 10))
    surf.blit(h_surf, (hx, lanes_top))
    # Left edge of horizon — thin vertical tick
    pygame.draw.line(surf, (55, 55, 75), (hx, lanes_top), (hx, lanes_bottom), 1)
    # Label at top of tick (only if there's room)
    if horizon_px > 28:
        tau_lbl = font_sm.render(f"τ {horizon_s:.1f}s", True, (70, 70, 100))
        surf.blit(tau_lbl, (hx + 2, lanes_top + 2))

    # Bar markers — vertical lines every bar_duration seconds, counting back from now
    bar_s = state.opt_beats_per_bar * 60.0 / max(40.0, state.opt_bpm)
    bar_col_minor = (40, 40, 50)
    bar_col_major = (60, 60, 75)
    age = (now - state.bar_origin_s) % bar_s   # position within current bar, anchored to play start
    bar_n = 0
    while True:
        bar_age = age + bar_n * bar_s
        if bar_age > TIMELINE_SECS:
            break
        bx = r.x + 2 + int((TIMELINE_SECS - bar_age) * px_per_s)
        col = bar_col_major if bar_n % 4 == 0 else bar_col_minor
        pygame.draw.line(surf, col, (bx, lanes_top), (bx, lanes_bottom))
        bar_n += 1

    # Labels
    surf.blit(font_sm.render("groove", True, GROOVE_HIT), (r.x+4, groove_y+2))
    surf.blit(font_sm.render("user",   True, USER_HIT),   (r.x+4, user_y+2))
    surf.blit(font_sm.render("ζ(t)",   True, ZETA_LINE),  (r.x+4, zeta_y+2))

    # Hit marks
    for ev in state.timeline:
        if ev["type"] not in ("note_on",):
            continue
        age = now - ev["t"]
        if age < 0 or age > TIMELINE_SECS:
            continue
        x     = r.x + 2 + int((TIMELINE_SECS - age) * px_per_s)
        vel_f = ev["velocity"] / 127.0
        col   = ev.get("color", GROOVE_HIT if ev["source"] == "playback" else USER_HIT)
        max_h = lane_h - 18
        h     = max(2, int(max_h * vel_f))
        if ev["source"] == "playback":
            y = groove_y + 16 + (max_h - h)
        else:
            y = user_y + 16 + (max_h - h)
        pygame.draw.rect(surf, col, (x, y, 3, h))

    # [A] ζ(t) curve — density (green), symmetry (teal), resonance (amber)
    if state.zeta_history:
        pts_density   = []
        pts_symmetry  = []
        pts_resonance = []
        for ts, res in state.zeta_history:
            age = now - ts
            if age < 0 or age > TIMELINE_SECS:
                continue
            x = r.x + 2 + int((TIMELINE_SECS - age) * px_per_s)
            pts_density.append((x,  zeta_y + zeta_h - 1 - int(res.density   * (zeta_h - 2))))
            pts_symmetry.append((x, zeta_y + zeta_h - 1 - int(res.symmetry  * (zeta_h - 2))))
            pts_resonance.append((x,zeta_y + zeta_h - 1 - int(res.resonance * (zeta_h - 2))))

        def _draw_curve(pts, col):
            if len(pts) > 1:
                pygame.draw.lines(surf, col, False, pts, 1)

        _draw_curve(pts_symmetry,  (60,  190, 190))   # teal  — σ
        _draw_curve(pts_resonance, (220, 170, 60))    # amber — ρ
        _draw_curve(pts_density,   ZETA_LINE)          # green — ζ₂

        # θ threshold — dashed horizontal line across ζ lane
        theta_y = zeta_y + zeta_h - 1 - int(state.opt_theta_c * (zeta_h - 2))
        THT_COL = (160, 110, 50)
        x = r.x + 2
        while x < r.x + 2 + tw:
            pygame.draw.line(surf, THT_COL, (x, theta_y),
                             (min(x + 5, r.x + 2 + tw), theta_y), 1)
            x += 10
        theta_lbl = font_sm.render(f"θ {state.opt_theta_c:.2f}", True, THT_COL)
        # Place label just above the line, avoiding the top "ζ(t)" label
        tly = max(zeta_y + 14, theta_y - 13)
        surf.blit(theta_lbl, (r.x + 4 + font_sm.size("ζ(t)  ")[0], tly))

        # Live readout
        _, last_res = state.zeta_history[-1]
        rdr = f"ζ {last_res.density:.2f}  σ {last_res.symmetry:.2f}  ρ {last_res.resonance:.2f}"
        surf.blit(font_sm.render(rdr, True, ZETA_LINE),
                  (r.x + 4, zeta_y + zeta_h - 16))

    # σ / ε coincidence windows — semi-transparent vertical bands centred on t=0 (playhead)
    # ε = binary gate (anything outside never registers), σ = Gaussian kernel half-width
    sigma_px   = max(1, int(sigma_s   * px_per_s))
    epsilon_px = max(2, int(epsilon_s * px_per_s))
    x_now      = r.x + 2 + tw          # playhead x
    band_top   = groove_y               # span groove + user lanes only
    band_bot   = user_y + lane_h
    band_h     = band_bot - band_top

    # ε band (outer, violet, very faint)
    eps_surf = pygame.Surface((epsilon_px * 2, band_h), pygame.SRCALPHA)
    eps_surf.fill((140, 80, 200, 28))
    surf.blit(eps_surf, (x_now - epsilon_px, band_top))
    # ε edge lines
    for ex in (x_now - epsilon_px, x_now + epsilon_px):
        pygame.draw.line(surf, (140, 80, 200, 100), (ex, band_top), (ex, band_bot), 1)

    # σ band (inner, blue, slightly brighter)
    sig_surf = pygame.Surface((sigma_px * 2, band_h), pygame.SRCALPHA)
    sig_surf.fill((80, 140, 220, 45))
    surf.blit(sig_surf, (x_now - sigma_px, band_top))
    # σ edge lines
    for sx in (x_now - sigma_px, x_now + sigma_px):
        pygame.draw.line(surf, (80, 140, 220, 160), (sx, band_top), (sx, band_bot), 1)

    # Labels just above the band, inset from each edge
    SIG_COL = (80, 140, 220)
    EPS_COL = (140, 80, 200)
    lbl_y = band_top - 13
    sig_t = font_sm.render(f"σ {state.opt_sigma_ms:.0f}ms", True, SIG_COL)
    eps_t = font_sm.render(f"ε {epsilon_s * 1000:.0f}ms",   True, EPS_COL)
    surf.blit(sig_t, (x_now - sigma_px - sig_t.get_width() - 2, lbl_y))
    surf.blit(eps_t, (x_now + epsilon_px + 2, lbl_y))

    # Playhead line (right edge = now)
    x_now = r.x + 2 + tw
    pygame.draw.line(surf, (200, 200, 200), (x_now, r.y+2), (x_now, r.y+r.height-2))


LEARN_BTN_H  = 24
CC_BTN_W     = 28
PAD_GRID_COLS = 4
PAD_GRID_ROWS = 4
PAD_GRID_GAP  = 4
PAD_CC_ROW_H  = LEARN_BTN_H + 6


def _draw_pad_grid(surf, font, font_sm, state: AppState, rect: pygame.Rect, buttons: dict,
                   source: str, base_cc, kit_paths: dict, hit_col: tuple,
                   pad_pfx: str, cc_dec_key: str, cc_inc_key: str):
    """Shared 4×4 pad grid renderer for groove and user sides."""
    base   = base_cc if base_cc is not None else 0
    grid_h = rect.height - PAD_CC_ROW_H
    pad_w  = (rect.width  - (PAD_GRID_COLS - 1) * PAD_GRID_GAP) // PAD_GRID_COLS
    pad_h  = (grid_h      - (PAD_GRID_ROWS - 1) * PAD_GRID_GAP) // PAD_GRID_ROWS

    for idx in range(PAD_GRID_COLS * PAD_GRID_ROWS):
        col   = idx % PAD_GRID_COLS
        row   = idx // PAD_GRID_COLS
        note  = base + idx
        x     = rect.x + col * (pad_w + PAD_GRID_GAP)
        y     = rect.y + row * (pad_h + PAD_GRID_GAP)
        pad_r = pygame.Rect(x, y, pad_w, pad_h)

        flash  = state.flash.get((source, note), 0)
        t_frac = flash / PAD_FLASH if flash > 0 else 0.0
        c = tuple(int(PAD_IDLE[i] + (hit_col[i] - PAD_IDLE[i]) * t_frac) for i in range(3)) if t_frac > 0 else PAD_IDLE
        draw_rect_border(surf, pad_r, c, BORDER, radius=6)

        # Color swatch button — top-right corner
        pad_col = state.pad_colors.get((source, note))
        cb_r = pygame.Rect(pad_r.right - COLOR_BTN_SIZE - 2, pad_r.y + 2,
                           COLOR_BTN_SIZE, COLOR_BTN_SIZE)
        draw_col = pad_col if pad_col else (55, 55, 55)
        pygame.draw.rect(surf, draw_col, cb_r, border_radius=3)
        buttons[f"{pad_pfx}_colorbtn_{idx}"] = (cb_r, source, note)

        if state.file_dialog_open:
            dim_surf = pygame.Surface((pad_w, pad_h), pygame.SRCALPHA)
            dim_surf.fill((0, 0, 0, 120))
            surf.blit(dim_surf, pad_r.topleft)
            surf.blit(font_sm.render("…", True, TEXT_DIM),
                      font_sm.render("…", True, TEXT_DIM).get_rect(center=pad_r.center))
        else:
            has_sound = note in kit_paths
            txt_col   = TEXT if has_sound else TEXT_DIM
            lbl = font_sm.render(GM_DRUMS.get(note, f"n{note}")[:7], True, txt_col)
            surf.blit(lbl, lbl.get_rect(centerx=pad_r.centerx, top=pad_r.y + 5))
            num = font_sm.render(str(note), True, TEXT_DIM)
            surf.blit(num, num.get_rect(centerx=pad_r.centerx, bottom=pad_r.bottom - 5))

        buttons[f"{pad_pfx}_{idx}"] = (pad_r, note)

    # CC shift row
    by     = rect.y + grid_h + 6
    dec_r  = pygame.Rect(rect.x,                  by, CC_BTN_W,                    LEARN_BTN_H)
    disp_r = pygame.Rect(rect.x + CC_BTN_W + 2,   by, rect.width - 2*CC_BTN_W - 4, LEARN_BTN_H)
    inc_r  = pygame.Rect(rect.right - CC_BTN_W,   by, CC_BTN_W,                    LEARN_BTN_H)
    button(surf, font_sm, dec_r, "◄")
    button(surf, font_sm, inc_r, "►")
    draw_rect_border(surf, disp_r, BTN_IDLE, BORDER, radius=4)
    if base_cc is None:
        cc_lbl = font_sm.render("set base ◄ ►", True, TEXT_DIM)
    else:
        cc_lbl = font_sm.render(f"base {base_cc}–{base_cc + PAD_GRID_COLS * PAD_GRID_ROWS - 1}", True, TEXT)
    surf.blit(cc_lbl, cc_lbl.get_rect(center=disp_r.center))
    buttons[cc_dec_key] = dec_r
    buttons[cc_inc_key] = inc_r


def draw_groove_pads(surf, font, font_sm, state: AppState, rect: pygame.Rect, buttons: dict):
    _draw_pad_grid(surf, font, font_sm, state, rect, buttons,
                   source="playback", base_cc=state.groove_cc,
                   kit_paths=state.groove_kit_paths, hit_col=GROOVE_HIT,
                   pad_pfx="groove_pad", cc_dec_key="groove_cc_dec", cc_inc_key="groove_cc_inc")


def draw_user_pads(surf, font, font_sm, state: AppState, rect: pygame.Rect, buttons: dict):
    _draw_pad_grid(surf, font, font_sm, state, rect, buttons,
                   source="capture", base_cc=state.user_cc,
                   kit_paths=state.user_kit_paths, hit_col=USER_HIT,
                   pad_pfx="user_pad", cc_dec_key="user_cc_dec", cc_inc_key="user_cc_inc")


MONITOR_ROW_H  = 17
MONITOR_HEADER = 20

def draw_midi_monitor(surf, font_sm, state: AppState, rect: pygame.Rect,
                      source_filter: str | None = None):
    """Scrolling MIDI event log, optionally filtered to one source."""
    draw_rect_border(surf, rect, PANEL_BG, BORDER, radius=0)

    now = time.time()
    if source_filter == "playback":
        active_note = None
        hit_col     = GROOVE_HIT
        title       = "GROOVE MIDI"
    elif source_filter == "capture":
        active_note = None
        hit_col     = USER_HIT
        title       = "USER MIDI"
    else:
        active_note = None
        hit_col     = USER_HIT
        title       = "MIDI MONITOR"

    if active_note is not None:
        name   = GM_DRUMS.get(active_note, f"n{active_note}")
        title += f"  —  {name} ({active_note})"
    surf.blit(font_sm.render(title, True, TEXT_DIM), (rect.x + 8, rect.y + 4))

    combined = source_filter is None

    # Column header — extra "src" column when showing all sources
    hy = rect.y + MONITOR_HEADER
    if combined:
        hdrs = ((8, "src"), (34, "  ago"), (84, "type"), (124, "note"), (168, "name"), (240, "vel"))
    else:
        hdrs = ((8, "  ago"), (58, "type"), (100, "note"), (148, "name"), (220, "vel"))
    for x_off, hdr in hdrs:
        surf.blit(font_sm.render(hdr, True, TEXT_DIM), (rect.x + x_off, hy))
    pygame.draw.line(surf, BORDER,
                     (rect.x + 4, hy + 14), (rect.right - 4, hy + 14))

    log = state.midi_log
    if source_filter is not None:
        log = [e for e in log if e["source"] == source_filter]

    max_rows = (rect.height - MONITOR_HEADER - 18) // MONITOR_ROW_H
    events   = log[-max_rows:][::-1]   # newest first, truncated to visible

    for i, ev in enumerate(events):
        ry = hy + 18 + i * MONITOR_ROW_H
        if ry + MONITOR_ROW_H > rect.bottom:
            break

        src_col = GROOVE_HIT if ev["source"] == "playback" else USER_HIT
        is_match = (active_note is not None and ev["note"] == active_note)
        fg  = hit_col if is_match else (src_col if combined else TEXT)
        dim = hit_col if is_match else (src_col if combined else TEXT_DIM)

        age     = now - ev["t"]
        age_str = f"{age:5.1f}s" if age < 60 else f"{age/60:4.1f}m"
        typ     = "ON " if ev["type"] == "note_on" else "OFF"
        name    = GM_DRUMS.get(ev["note"], "---")
        vel     = str(ev["velocity"]) if ev["type"] == "note_on" else ""

        if combined:
            src_str = "GR" if ev["source"] == "playback" else "US"
            cols = (
                (8,   src_str,         src_col),
                (34,  age_str,         dim),
                (84,  typ,             fg),
                (124, str(ev["note"]), fg),
                (168, name,            fg),
                (240, vel,             dim),
            )
        else:
            cols = (
                (8,   age_str,         dim),
                (58,  typ,             fg),
                (100, str(ev["note"]), fg),
                (148, name,            fg),
                (220, vel,             dim),
            )
        for x_off, val, col in cols:
            surf.blit(font_sm.render(val, True, col), (rect.x + x_off, ry))


def _playback_pct(state: AppState) -> float | None:
    if state.playback_start_ns is None or state.groove_duration_us <= 0:
        return None
    elapsed_us = (time.time_ns() - state.playback_start_ns) / 1000
    if state.loop_enabled and state.groove_duration_us > 0:
        elapsed_us = elapsed_us % state.groove_duration_us
    return min(100.0, elapsed_us / state.groove_duration_us * 100)


def draw_controls(surf, font, font_sm, state: AppState, mouse_pos, buttons: dict):
    r = CONTROLS_R
    draw_rect_border(surf, r, PANEL_BG, BORDER, radius=0)

    third = r.width // 3

    # --- Kit panel (groove + user bindings) ---
    kit_r  = pygame.Rect(r.x, r.y, third, r.height)
    half_h = kit_r.height // 2
    rm_w   = 56

    def _draw_kit_section(title, kit_paths, remove_mode, remove_btn_key, row_pfx, top):
        title_w = font_sm.size(title)[0]
        rm_r    = pygame.Rect(kit_r.x + 8 + title_w + 8, top + 2, rm_w, 16)
        surf.blit(font_sm.render(title, True, TEXT_DIM), (kit_r.x+8, top+6))
        button(surf, font_sm, rm_r,
               "● REM" if remove_mode else "REM", active=remove_mode)
        buttons[remove_btn_key] = rm_r
        bottom = top + half_h
        y = top + 24
        if kit_paths:
            for note in sorted(kit_paths):
                path  = kit_paths[note]
                name  = GM_DRUMS.get(note, f"n{note}")
                fname = os.path.basename(path)
                row_r = pygame.Rect(kit_r.x + 4, y - 1, kit_r.width - 8, 15)
                if remove_mode:
                    pygame.draw.rect(surf, (60, 28, 28), row_r, border_radius=2)
                    col = (220, 80, 80)
                else:
                    col = TEXT if os.path.exists(path) else (160, 60, 60)
                surf.blit(font_sm.render(f"{note:3d} {name:8s} {fname}", True, col), (kit_r.x+8, y))
                buttons[f"{row_pfx}_{note}"] = row_r
                y += 16
                if y > bottom - 4:
                    break
        else:
            surf.blit(font_sm.render("no sounds set", True, TEXT_DIM), (kit_r.x+8, y))

    _draw_kit_section("GROOVE KIT", state.groove_kit_paths, state.groove_remove_mode,
                      "groove_remove", "kit_row", kit_r.y)
    pygame.draw.line(surf, BORDER, (kit_r.x+4, kit_r.y + half_h), (kit_r.right-4, kit_r.y + half_h))
    _draw_kit_section("USER KIT",   state.user_kit_paths,   state.user_remove_mode,
                      "user_remove",  "user_kit_row", kit_r.y + half_h + 2)

    # --- Transport ---
    tr_r = pygame.Rect(r.x + third, r.y, third, r.height)
    surf.blit(font_sm.render("TRANSPORT", True, TEXT_DIM), (tr_r.x + 8, tr_r.y + 6))

    bw, bh, bpad = 90, 28, 8
    bx = tr_r.x + 8
    by = tr_r.y + 26

    def mk_btn(label, key, active=False):
        nonlocal by
        rect = pygame.Rect(bx, by, bw, bh)
        hov  = rect.collidepoint(mouse_pos)
        button(surf, font_sm, rect, label, active=active, hover=hov)
        buttons[key] = rect
        by += bh + bpad

    mk_btn("Opening…" if state.file_dialog_open else "LOAD", "load",
           active=state.file_dialog_open)
    mk_btn("PLAY",  "play",  active=state.is_playing)
    mk_btn("STOP",  "stop")
    mk_btn("LOOP",  "loop",  active=state.loop_enabled)

    # --- Pseta info ---
    pairs_r = pygame.Rect(r.x + 2*third, r.y, r.width - 2*third, r.height)
    surf.blit(font_sm.render("PSETA INFO", True, TEXT_DIM), (pairs_r.x + 8, pairs_r.y + 6))

    n = len(state.pad_colors)
    n_pairs = n * (n - 1) // 2
    py = pairs_r.y + 24
    surf.blit(font_sm.render("stream pairs", True, TEXT_DIM), (pairs_r.x + 8, py))
    val = font_sm.render(str(n_pairs), True, TEXT)
    surf.blit(val, (pairs_r.right - val.get_width() - 8, py))

    if state.zeta_history:
        _, last_r = state.zeta_history[-1]
        for label, v, col in (("ζ density",  last_r.density,   ZETA_LINE),
                               ("σ symmetry", last_r.symmetry,  (60, 190, 190)),
                               ("ρ resonance",last_r.resonance, (220, 170, 60))):
            py += 16
            surf.blit(font_sm.render(label, True, TEXT_DIM), (pairs_r.x + 8, py))
            vt = font_sm.render(f"{v:.3f}", True, col)
            surf.blit(vt, (pairs_r.right - vt.get_width() - 8, py))


def _apply_slider_drag(state: "AppState", key: str, mx: int,
                       track_r: pygame.Rect, bridge: "RustBridge"):
    step, lo, hi = OPT_META[key]
    pct     = max(0.0, min(1.0, (mx - track_r.x) / track_r.width))
    raw     = lo + pct * (hi - lo)
    snapped = round(round(raw / step) * step, 4)
    snapped = max(lo, min(hi, snapped))
    setattr(state, f"opt_{key}", snapped)
    if key == "bpm":
        bridge.send(cmd="set_bpm", bpm=state.opt_bpm)
    save_settings(state)


def draw_pseta_options(surf, font_sm, state: AppState, mouse_pos, buttons: dict):
    """Right-side panel — Pseta Options, spanning timeline-bottom to status bar."""
    r = OPT_PANEL_R
    draw_rect_border(surf, r, PANEL_BG, BORDER, radius=0)

    bpm_safe   = max(40.0, state.opt_bpm)
    bar_s_now  = state.opt_beats_per_bar * 60.0 / bpm_safe
    horizon_s  = max(2.0, state.opt_tau_bars * bar_s_now)
    epsilon_ms = state.opt_sigma_ms * state.opt_epsilon_factor

    OPT_GROUPS = [
        ("TEMPO", None, [
            ("bpm",           "BPM",       state.opt_bpm,           "{:.0f}", "",     ""),
            ("beats_per_bar", "beats/bar", state.opt_beats_per_bar, "{:.0f}", "",
             f"→ {bar_s_now:.2f}s/bar"),
        ]),
        ("COINCIDENCE", None, [
            ("sigma_ms",       "radius σ", state.opt_sigma_ms,       "{:.0f}", "ms", ""),
            ("epsilon_factor", "gate ε",   state.opt_epsilon_factor,  "{:.1f}", "×σ",
             f"→ {epsilon_ms:.0f}ms"),
        ]),
        ("WINDOW", None, [
            ("tau_bars", "horizon τ", state.opt_tau_bars, "{:.2f}", "bars",
             f"→ {horizon_s:.1f}s"),
        ]),
        ("ACTIVATION", TEXT_DIM, [
            ("theta_c", "threshold θ", state.opt_theta_c, "{:.2f}", "", "R(t) [C]"),
        ]),
    ]

    pad   = 8
    row_h = 20
    sld_h = 14    # slider row height
    hdr_h = 14
    ann_h = 11
    btn_w = 16
    val_w = 40
    lbl_w = r.width - 2 * btn_w - val_w - pad - 6
    sld_w = r.width - 2 * pad

    SLD_TRACK  = (42, 42, 52)
    SLD_FILL   = (60, 100, 160)
    SLD_THUMB  = (120, 160, 220)
    SLD_HOV    = (160, 200, 255)
    SLD_DRAG   = (200, 220, 255)

    oy = r.y + 4
    surf.blit(font_sm.render("PSETA OPTIONS", True, TEXT_DIM), (r.x + pad, oy + 2))
    oy += hdr_h + 2

    for grp_name, grp_col, rows in OPT_GROUPS:
        col = grp_col if grp_col else (110, 110, 120)
        surf.blit(font_sm.render(grp_name, True, col), (r.x + pad, oy))
        pygame.draw.line(surf, (50, 50, 60),
                         (r.x + pad + font_sm.size(grp_name)[0] + 4, oy + 6),
                         (r.right - pad, oy + 6))
        oy += hdr_h

        for key, lbl, val, fmt, unit, ann in rows:
            lbl_x = r.x + pad
            dec_x = lbl_x + lbl_w
            val_x = dec_x + btn_w + 2
            inc_x = val_x + val_w + 2

            dec_r = pygame.Rect(dec_x, oy, btn_w, row_h - 3)
            val_r = pygame.Rect(val_x, oy, val_w, row_h - 3)
            inc_r = pygame.Rect(inc_x, oy, btn_w, row_h - 3)

            lbl_str = lbl if not unit else f"{lbl} ({unit})"
            surf.blit(font_sm.render(lbl_str, True, TEXT_DIM), (lbl_x, oy + 3))

            button(surf, font_sm, dec_r, "◄")
            draw_rect_border(surf, val_r, BTN_IDLE, BORDER, radius=3)
            vt = font_sm.render(fmt.format(val), True, TEXT)
            surf.blit(vt, vt.get_rect(center=val_r.center))
            button(surf, font_sm, inc_r, "►")

            buttons[f"opt_dec_{key}"] = dec_r
            buttons[f"opt_inc_{key}"] = inc_r
            oy += row_h

            # --- Slider ---
            _, lo, hi = OPT_META[key]
            pct      = (val - lo) / (hi - lo) if hi > lo else 0.0
            track_x  = r.x + pad
            track_y  = oy + (sld_h - 4) // 2
            track_r_ = pygame.Rect(track_x, track_y, sld_w, 4)
            thumb_cx = track_x + int(pct * sld_w)
            thumb_r_ = pygame.Rect(thumb_cx - 5, track_y - 4, 10, 12)

            is_drag  = state.opt_drag_key == key
            is_hov   = thumb_r_.inflate(4, 4).collidepoint(mouse_pos) or \
                       (track_r_.inflate(0, 12).collidepoint(mouse_pos) and not is_drag)

            # Track — filled up to thumb
            pygame.draw.rect(surf, SLD_TRACK, track_r_, border_radius=2)
            if pct > 0:
                pygame.draw.rect(surf, SLD_FILL,
                                 pygame.Rect(track_x, track_y, thumb_cx - track_x, 4),
                                 border_radius=2)
            # Thumb
            thumb_col = SLD_DRAG if is_drag else (SLD_HOV if is_hov else SLD_THUMB)
            pygame.draw.rect(surf, thumb_col, thumb_r_, border_radius=3)

            # Store for hit-testing (track_r for drag zone, thumb_r for thumb)
            buttons[f"opt_slider_{key}"] = (track_r_, thumb_r_, key)
            oy += sld_h

            if ann:
                surf.blit(font_sm.render(ann, True, (80, 80, 95)),
                          (lbl_x + 4, oy - 1))
                oy += ann_h

        oy += 4   # group gap


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------

def draw_status_bar(surf, font_sm, state: AppState, mouse_pos, buttons: dict):
    r = STATUS_R
    pygame.draw.rect(surf, (22, 22, 22), r)
    pygame.draw.line(surf, BORDER, r.topleft, r.topright)

    pad   = 10
    btn_w = 28
    btn_h = r.height - 6
    cy    = r.y + 3

    # IN button + port name
    in_r = pygame.Rect(pad, cy, btn_w, btn_h)
    button(surf, font_sm, in_r, "IN",
           active=state.port_picker == "input",
           hover=in_r.collidepoint(mouse_pos))
    buttons["port_in"] = in_r

    in_name = state.input_port or "(none)"
    surf.blit(font_sm.render(in_name, True, TEXT_DIM), (in_r.right + 4, cy + 2))

    # OUT button + port name — positioned after IN name
    in_name_w = font_sm.size(in_name)[0]
    out_x = in_r.right + 4 + in_name_w + 16
    out_r = pygame.Rect(out_x, cy, btn_w, btn_h)
    button(surf, font_sm, out_r, "OUT",
           active=state.port_picker == "output",
           hover=out_r.collidepoint(mouse_pos))
    buttons["port_out"] = out_r

    out_name = state.output_port or "(none)"
    surf.blit(font_sm.render(out_name, True, TEXT_DIM), (out_r.right + 4, cy + 2))

    # Status message — right-aligned
    msg = font_sm.render(state.status_msg, True, TEXT_DIM)
    surf.blit(msg, (r.right - msg.get_width() - pad, cy + 2))

    # Groove file + progress bar — centred between OUT name and status msg
    if state.loaded_path:
        out_end  = out_r.right + 4 + font_sm.size(out_name)[0] + 12
        msg_x    = r.right - msg.get_width() - pad
        groove_w = msg_x - out_end - 12
        if groove_w > 40:
            fname   = os.path.basename(state.loaded_path)
            max_ch  = groove_w // 7
            if len(fname) > max_ch:
                fname = fname[:max_ch - 1] + "…"
            fx = out_end
            surf.blit(font_sm.render(fname, True, TEXT_DIM), (fx, cy + 2))
            pct = _playback_pct(state)
            if pct is not None:
                bar_y = cy + btn_h - 4
                bar_w = groove_w
                filled = int(bar_w * pct / 100)
                pygame.draw.rect(surf, (45, 45, 45), (fx, bar_y, bar_w, 3), border_radius=1)
                pygame.draw.rect(surf, GROOVE_HIT,   (fx, bar_y, filled, 3), border_radius=1)


# ---------------------------------------------------------------------------
# Port picker overlay
# ---------------------------------------------------------------------------

PICKER_ITEM_H = 24
PICKER_W      = 340
PICKER_BG     = (38, 38, 38)
PICKER_SEL    = (60, 90, 60)
PICKER_HOV    = (55, 55, 55)

def draw_port_picker(surf, font_sm, state: AppState, mouse_pos, buttons: dict):
    """Dropdown overlay for input or output port selection."""
    if state.port_picker is None:
        return

    if state.port_picker == "input":
        ports    = state.input_ports
        anchor   = buttons.get("port_in")
        cur_port = state.input_port
        key_pfx  = "pick_in"
    else:
        ports    = state.output_ports
        anchor   = buttons.get("port_out")
        cur_port = state.output_port
        key_pfx  = "pick_out"

    if anchor is None:
        return

    rows  = ports if ports else ["(no ports found)"]
    total_h = len(rows) * PICKER_ITEM_H + 4
    x = anchor.x
    y = anchor.bottom + 2
    # Flip above if it would go off screen
    if y + total_h > H:
        y = anchor.y - total_h - 2

    bg_r = pygame.Rect(x, y, PICKER_W, total_h)
    pygame.draw.rect(surf, PICKER_BG, bg_r, border_radius=4)
    pygame.draw.rect(surf, BORDER,    bg_r, width=1, border_radius=4)

    for i, name in enumerate(rows):
        item_r = pygame.Rect(x + 2, y + 2 + i * PICKER_ITEM_H, PICKER_W - 4, PICKER_ITEM_H - 2)
        if not ports:
            bg = PICKER_BG
        elif name == cur_port:
            bg = PICKER_SEL
        elif item_r.collidepoint(mouse_pos):
            bg = PICKER_HOV
        else:
            bg = PICKER_BG
        pygame.draw.rect(surf, bg, item_r, border_radius=3)
        surf.blit(font_sm.render(name, True, TEXT), (item_r.x + 6, item_r.y + 4))
        if ports:
            buttons[f"{key_pfx}_{i}"] = (item_r, name)


def draw_color_picker_overlay(surf, state: AppState, mouse_pos, buttons: dict):
    """Floating 4×4 swatch panel (15 colors + blank) anchored to the color button."""
    if state.color_picker_open is None or state.color_picker_anchor is None:
        return

    source, note = state.color_picker_open
    swatches = PAD_PALETTE + [None]   # None = blank / remove color
    cols = COLOR_PICK_COLS
    rows = (len(swatches) + cols - 1) // cols

    pw = cols * COLOR_PICK_SIZE + (cols - 1) * COLOR_PICK_GAP + 8
    ph = rows * COLOR_PICK_SIZE + (rows - 1) * COLOR_PICK_GAP + 8

    anchor = state.color_picker_anchor
    x = max(0, anchor.right - pw)
    y = anchor.bottom + 2
    if y + ph > H:
        y = anchor.y - ph - 2

    bg_r = pygame.Rect(x, y, pw, ph)
    pygame.draw.rect(surf, PICKER_BG, bg_r, border_radius=4)
    pygame.draw.rect(surf, BORDER,    bg_r, width=1, border_radius=4)

    cur_col = state.pad_colors.get((source, note))

    for i, col in enumerate(swatches):
        sc  = i % cols
        sr  = i // cols
        sx  = x + 4 + sc * (COLOR_PICK_SIZE + COLOR_PICK_GAP)
        sy  = y + 4 + sr * (COLOR_PICK_SIZE + COLOR_PICK_GAP)
        sw_r = pygame.Rect(sx, sy, COLOR_PICK_SIZE, COLOR_PICK_SIZE)

        if col is None:
            pygame.draw.rect(surf, (30, 30, 30), sw_r, border_radius=3)
            pygame.draw.line(surf, (90, 90, 90), sw_r.topleft,  sw_r.bottomright, 1)
            pygame.draw.line(surf, (90, 90, 90), sw_r.topright, sw_r.bottomleft,  1)
            selected = cur_col is None
        else:
            pygame.draw.rect(surf, col, sw_r, border_radius=3)
            selected = (col == cur_col)

        border_col = (255, 255, 255) if selected else BORDER
        pygame.draw.rect(surf, border_col, sw_r, width=1, border_radius=3)
        buttons[f"colorpick_{i}"] = (sw_r, col)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config = load_config()

    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Pseta")
    clock  = pygame.time.Clock()

    font    = pygame.font.SysFont("monospace", 14)
    font_sm = pygame.font.SysFont("monospace", 12)

    bridge = RustBridge()
    audio  = AudioEngine(config)
    state  = AppState(config)

    # Restore saved groove and user sounds
    for note, path in state.groove_kit_paths.items():
        if os.path.exists(path):
            try:
                audio.groove_kit[note] = pygame.mixer.Sound(path)
            except pygame.error:
                pass
    for note, path in state.user_kit_paths.items():
        if os.path.exists(path):
            try:
                audio.kit[note] = pygame.mixer.Sound(path)
            except pygame.error:
                pass

    rust_ok = bridge.start()
    if not rust_ok:
        state.status_msg = "Rust binary missing — run: cargo build --release"

    # Three-panel layout inside PADS_R (left → right):
    #   midi_monitor (all sources) | groove_pads (4×4) | user_pads (4×4)
    gap        = 16
    grid_w     = PAD_GRID_COLS * 76 + (PAD_GRID_COLS - 1) * PAD_GRID_GAP
    mon_w      = PADS_R.width - 3 * gap - 2 * grid_w   # remaining width for single monitor

    midi_mon_r    = pygame.Rect(PADS_R.x + gap,           PADS_R.y, mon_w,  PADS_R.height)
    groove_grid_r = pygame.Rect(midi_mon_r.right + gap,   PADS_R.y, grid_w, PADS_R.height)
    user_grid_r   = pygame.Rect(groove_grid_r.right + gap, PADS_R.y, grid_w, PADS_R.height)

    buttons: dict = {}

    running = True
    while running:
        mouse_pos = pygame.mouse.get_pos()

        # --- Pygame events ---
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False

            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                # Port picker item selection (check before other buttons so overlay clicks register)
                picked = False
                for k, v in list(buttons.items()):
                    if k.startswith("pick_") and isinstance(v, tuple):
                        item_r, port_name = v
                        if item_r.collidepoint(ev.pos):
                            if k.startswith("pick_in_"):
                                bridge.send(cmd="set_input", port=port_name)
                            else:
                                bridge.send(cmd="set_output", port=port_name)
                            state.port_picker = None
                            picked = True
                            break
                if picked:
                    pass

                elif "port_in" in buttons and buttons["port_in"].collidepoint(ev.pos):
                    state.port_picker = None if state.port_picker == "input" else "input"
                    state.color_picker_open = None

                elif "port_out" in buttons and buttons["port_out"].collidepoint(ev.pos):
                    state.port_picker = None if state.port_picker == "output" else "output"
                    state.color_picker_open = None

                elif "groove_remove" in buttons and buttons["groove_remove"].collidepoint(ev.pos):
                    state.groove_remove_mode = not state.groove_remove_mode

                elif "user_remove" in buttons and buttons["user_remove"].collidepoint(ev.pos):
                    state.user_remove_mode = not state.user_remove_mode

                elif "groove_cc_dec" in buttons and buttons["groove_cc_dec"].collidepoint(ev.pos):
                    cur = state.groove_cc if state.groove_cc is not None else 0
                    state.groove_cc = max(0, cur - 1)
                    save_settings(state)

                elif "groove_cc_inc" in buttons and buttons["groove_cc_inc"].collidepoint(ev.pos):
                    cur = state.groove_cc if state.groove_cc is not None else 0
                    state.groove_cc = min(128 - PAD_GRID_COLS * PAD_GRID_ROWS, cur + 1)
                    save_settings(state)

                elif "user_cc_dec" in buttons and buttons["user_cc_dec"].collidepoint(ev.pos):
                    cur = state.user_cc if state.user_cc is not None else 0
                    state.user_cc = max(0, cur - 1)
                    save_settings(state)

                elif "user_cc_inc" in buttons and buttons["user_cc_inc"].collidepoint(ev.pos):
                    cur = state.user_cc if state.user_cc is not None else 0
                    state.user_cc = min(128 - PAD_GRID_COLS * PAD_GRID_ROWS, cur + 1)
                    save_settings(state)

                elif "load" in buttons and buttons["load"].collidepoint(ev.pos):
                    state.port_picker = None
                    _load_file(bridge, state)

                elif "play" in buttons and buttons["play"].collidepoint(ev.pos):
                    if state.loaded_path and not state.is_playing:
                        bridge.send(cmd="play")
                        state.is_playing   = True
                        state.status_msg   = "Playing"

                elif "stop" in buttons and buttons["stop"].collidepoint(ev.pos):
                    bridge.send(cmd="stop")
                    state.is_playing = False
                    state.status_msg = "Stopped"

                elif "loop" in buttons and buttons["loop"].collidepoint(ev.pos):
                    state.loop_enabled = not state.loop_enabled
                    bridge.send(cmd="loop", enabled=state.loop_enabled)

                elif any(k.startswith("opt_dec_") or k.startswith("opt_inc_")
                         for k, v in buttons.items()
                         if isinstance(v, pygame.Rect) and v.collidepoint(ev.pos)):
                    for k, v in buttons.items():
                        if isinstance(v, pygame.Rect) and v.collidepoint(ev.pos):
                            if k.startswith("opt_dec_") or k.startswith("opt_inc_"):
                                key  = k[8:]
                                step, lo, hi = OPT_META[key]
                                cur  = getattr(state, f"opt_{key}")
                                sign = -1 if k.startswith("opt_dec_") else 1
                                setattr(state, f"opt_{key}",
                                        round(max(lo, min(hi, cur + sign * step)), 4))
                                if key == "bpm":
                                    bridge.send(cmd="set_bpm", bpm=state.opt_bpm)
                                save_settings(state)
                                break

                else:
                    clicked = False

                    # Slider click — start drag and set value immediately
                    if not clicked and state.opt_drag_key is None:
                        for k, v in buttons.items():
                            if k.startswith("opt_slider_") and isinstance(v, tuple):
                                track_r_, thumb_r_, opt_key = v
                                hit_zone = track_r_.inflate(0, 16)
                                if hit_zone.collidepoint(ev.pos) or thumb_r_.collidepoint(ev.pos):
                                    state.opt_drag_key = opt_key
                                    _apply_slider_drag(state, opt_key, ev.pos[0], track_r_, bridge)
                                    clicked = True
                                    break

                    # Color swatch picker: swatch selection
                    if not clicked and state.color_picker_open is not None:
                        for k, v in list(buttons.items()):
                            if k.startswith("colorpick_") and isinstance(v, tuple):
                                sw_r, col = v
                                if sw_r.collidepoint(ev.pos):
                                    src, nt = state.color_picker_open
                                    if col is None:
                                        state.pad_colors.pop((src, nt), None)
                                    else:
                                        state.pad_colors[(src, nt)] = col
                                    state.color_picker_open = None
                                    save_settings(state)
                                    clicked = True
                                    break

                    # Color swatch button on each pad: open/close picker
                    if not clicked:
                        for idx in range(PAD_GRID_COLS * PAD_GRID_ROWS):
                            for pfx in ("groove_pad", "user_pad"):
                                entry = buttons.get(f"{pfx}_colorbtn_{idx}")
                                if entry and entry[0].collidepoint(ev.pos):
                                    _, src, nt = entry
                                    if state.color_picker_open == (src, nt):
                                        state.color_picker_open = None
                                    else:
                                        state.color_picker_open   = (src, nt)
                                        state.color_picker_anchor = entry[0]
                                    clicked = True
                                    break
                            if clicked:
                                break

                    # Close picker on any other click
                    if not clicked and state.color_picker_open is not None:
                        state.color_picker_open = None

                    # Groove pad clicks
                    if not clicked:
                        for idx in range(PAD_GRID_COLS * PAD_GRID_ROWS):
                            entry = buttons.get(f"groove_pad_{idx}")
                            if entry and entry[0].collidepoint(ev.pos):
                                _, note = entry
                                if state.groove_remove_mode:
                                    state.groove_kit_paths.pop(note, None)
                                    audio.groove_kit.pop(note, None)
                                    save_settings(state)
                                    state.status_msg = f"Removed groove: {GM_DRUMS.get(note, f'n{note}')} ({note})"
                                elif not state.file_dialog_open:
                                    _open_file_dialog(
                                        state.file_pick_q, "set_groove_sound",
                                        title=f"Set groove sound — note {note} ({GM_DRUMS.get(note, f'n{note}')})",
                                        file_filter="WAV files (*.wav) | *.wav",
                                        initial_dir="samples", state=state, note=note,
                                    )
                                clicked = True
                                break
                    # User pad clicks
                    if not clicked:
                        for idx in range(PAD_GRID_COLS * PAD_GRID_ROWS):
                            entry = buttons.get(f"user_pad_{idx}")
                            if entry and entry[0].collidepoint(ev.pos):
                                _, note = entry
                                if state.user_remove_mode:
                                    state.user_kit_paths.pop(note, None)
                                    audio.kit.pop(note, None)
                                    save_settings(state)
                                    state.status_msg = f"Removed user: {GM_DRUMS.get(note, f'n{note}')} ({note})"
                                elif not state.file_dialog_open:
                                    _open_file_dialog(
                                        state.file_pick_q, "set_user_sound",
                                        title=f"Set user sound — note {note} ({GM_DRUMS.get(note, f'n{note}')})",
                                        file_filter="WAV files (*.wav) | *.wav",
                                        initial_dir="samples", state=state, note=note,
                                    )
                                clicked = True
                                break
                    # Kit row remove clicks
                    if not clicked:
                        for k, v in list(buttons.items()):
                            if isinstance(v, pygame.Rect) and v.collidepoint(ev.pos):
                                if k.startswith("kit_row_") and state.groove_remove_mode:
                                    note = int(k[len("kit_row_"):])
                                    state.groove_kit_paths.pop(note, None)
                                    audio.groove_kit.pop(note, None)
                                    save_settings(state)
                                    state.status_msg = f"Removed groove: {GM_DRUMS.get(note, f'n{note}')} ({note})"
                                    clicked = True
                                    break
                                elif k.startswith("user_kit_row_") and state.user_remove_mode:
                                    note = int(k[len("user_kit_row_"):])
                                    state.user_kit_paths.pop(note, None)
                                    audio.kit.pop(note, None)
                                    save_settings(state)
                                    state.status_msg = f"Removed user: {GM_DRUMS.get(note, f'n{note}')} ({note})"
                                    clicked = True
                                    break
                    if not clicked:
                        state.port_picker = None

            elif ev.type == pygame.MOUSEMOTION:
                if state.opt_drag_key is not None and pygame.mouse.get_pressed()[0]:
                    sld = buttons.get(f"opt_slider_{state.opt_drag_key}")
                    if sld:
                        track_r_, _, opt_key = sld
                        _apply_slider_drag(state, opt_key, ev.pos[0], track_r_, bridge)

            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                state.opt_drag_key = None

        # --- Drain Rust events ---
        while True:
            try:
                ev = bridge.event_q.get_nowait()
            except queue.Empty:
                break
            _handle_rust_event(ev, state, bridge, audio)

        # --- Drain file-dialog results ---
        while True:
            try:
                pick = state.file_pick_q.get_nowait()
            except queue.Empty:
                break
            _handle_file_pick(pick, bridge, state, audio)

        # --- Tick flash + ζ ---
        state.tick_flash()
        state.tick_zeta()

        # --- Render ---
        screen.fill(BG)
        buttons = {}

        draw_timeline(screen, font_sm, state)

        # Pad section — midi_monitor (all) | groove_pads (4×4) | user_pads (4×4)
        draw_rect_border(screen, PADS_R, PANEL_BG, BORDER, radius=0)
        draw_midi_monitor(screen, font_sm, state, midi_mon_r, source_filter=None)
        draw_groove_pads(screen, font, font_sm, state, groove_grid_r, buttons)
        draw_user_pads(screen, font, font_sm, state, user_grid_r, buttons)

        draw_controls(screen, font, font_sm, state, mouse_pos, buttons)
        draw_pseta_options(screen, font_sm, state, mouse_pos, buttons)
        draw_status_bar(screen, font_sm, state, mouse_pos, buttons)
        draw_port_picker(screen, font_sm, state, mouse_pos, buttons)
        draw_color_picker_overlay(screen, state, mouse_pos, buttons)

        pygame.display.flip()
        clock.tick(FPS)

    bridge.stop()
    pygame.quit()


def _open_file_dialog(result_q: queue.Queue, action: str,
                      title: str, file_filter: str, initial_dir: str,
                      state: "AppState | None" = None, **extra):
    """
    Run a native OS file-selection dialog in a background thread.
    Tries zenity (GTK), then kdialog (KDE). Result posted to result_q.
    Never blocks the pygame main loop.
    Sets state.file_dialog_open = True while in flight; clears it via result_q.
    """
    if state is not None:
        state.file_dialog_open = True

    def _run():
        path = None

        # zenity — available on most GNOME/GTK desktops
        if path is None:
            try:
                args = ["zenity", "--file-selection", f"--title={title}"]
                if initial_dir and os.path.isdir(initial_dir):
                    args += [f"--filename={os.path.join(initial_dir, '')}"]
                if file_filter:
                    args += [f"--file-filter={file_filter}"]
                r = subprocess.run(args, capture_output=True, text=True)
                path_candidate = r.stdout.strip()
                if path_candidate:
                    path = path_candidate
            except FileNotFoundError:
                pass

        # kdialog — KDE fallback
        if path is None:
            try:
                start = initial_dir if initial_dir and os.path.isdir(initial_dir) else "."
                args  = ["kdialog", "--getopenfilename", start]
                if file_filter:
                    args.append(file_filter)
                r = subprocess.run(args, capture_output=True, text=True)
                path_candidate = r.stdout.strip()
                if path_candidate:
                    path = path_candidate
            except FileNotFoundError:
                pass

        result_q.put({"action": action, "path": path, **extra})

    threading.Thread(target=_run, daemon=True).start()


def _handle_file_pick(pick: dict, bridge: "RustBridge",
                      state: "AppState", audio: "AudioEngine"):
    """Apply a completed file-dialog result."""
    state.file_dialog_open = False
    action = pick["action"]
    path   = pick.get("path")

    if action == "load":
        if path:
            state.loaded_path = path
            state.is_playing  = False
            state.status_msg  = f"Loaded: {os.path.basename(path)}"
            bridge.send(cmd="load", path=path)
            save_settings(state)

    elif action == "set_sound":
        note = pick.get("note")
        if path and note is not None:
            try:
                audio.kit[note] = pygame.mixer.Sound(path)
                name = GM_DRUMS.get(note, f"n{note}")
                state.status_msg = f"Sound set: {name} → {os.path.basename(path)}"
            except pygame.error as e:
                state.status_msg = f"Could not load sound: {e}"

    elif action == "set_user_sound":
        note = pick.get("note")
        if path and note is not None:
            try:
                audio.kit[note] = pygame.mixer.Sound(path)
                state.user_kit_paths[note] = path
                save_settings(state)
                name = GM_DRUMS.get(note, f"n{note}")
                state.status_msg = f"User sound set: {name} → {os.path.basename(path)}"
            except pygame.error as e:
                state.status_msg = f"Could not load sound: {e}"

    elif action == "set_groove_sound":
        note = pick.get("note")
        if path and note is not None:
            try:
                audio.groove_kit[note] = pygame.mixer.Sound(path)
                state.groove_kit_paths[note] = path
                save_settings(state)
                name = GM_DRUMS.get(note, f"n{note}")
                state.status_msg = f"Groove sound set: {name} → {os.path.basename(path)}"
            except pygame.error as e:
                state.status_msg = f"Could not load sound: {e}"


def _load_file(bridge: RustBridge, state: AppState):
    if state.file_dialog_open:
        return
    state.status_msg = "Opening…"
    _open_file_dialog(
        state.file_pick_q, "load",
        title="Load Groove MIDI file",
        file_filter="MIDI files (*.mid *.midi) | *.mid *.midi",
        initial_dir="datasets",
        state=state,
    )





def _handle_rust_event(ev: dict, state: AppState, bridge: RustBridge, audio: AudioEngine):
    t = ev.get("type")

    if t == "ready":
        state.status_msg = "Ready"
        # Auto-connect configured ports
        if state.input_port:
            bridge.send(cmd="set_input", port=state.input_port)
        if state.output_port:
            bridge.send(cmd="set_output", port=state.output_port)
        # Auto-reload last groove MIDI
        if state.loaded_path and os.path.exists(state.loaded_path):
            bridge.send(cmd="load", path=state.loaded_path)

    elif t == "ports":
        state.input_ports  = ev.get("input",  [])
        state.output_ports = ev.get("output", [])

    elif t in ("note_on", "note_off"):
        state.push_event(ev)
        if t == "note_on":
            audio.play(ev["note"], ev["velocity"], source=ev.get("source", "capture"))

    elif t == "file_loaded":
        state.groove_bpm         = ev.get("bpm", 120.0)
        state.groove_duration_us = ev.get("duration_us", 0)
        # Sync BPM and time signature from file
        state.opt_bpm            = state.groove_bpm
        ts_num = ev.get("ts_num", 4)
        ts_den = ev.get("ts_den", 4)
        # Convert to quarter-notes per bar: ts_num × (4 / ts_den)
        # e.g. 4/4 → 4, 3/4 → 3, 6/8 → 3, 5/4 → 5
        state.opt_beats_per_bar  = max(1, round(ts_num * 4 / ts_den))
        bridge.send(cmd="set_bpm", bpm=state.opt_bpm)
        save_settings(state)
        state.status_msg = (
            f"Loaded ({ev.get('event_count',0)} events, "
            f"{state.groove_bpm:.1f} BPM, {ts_num}/{ts_den})"
        )

    elif t == "playback_started":
        state.groove_bpm         = ev.get("bpm", state.groove_bpm)
        state.groove_duration_us = ev.get("duration_us", state.groove_duration_us)
        state.is_playing         = True
        state.playback_start_ns  = time.time_ns()
        state.bar_origin_s       = time.time()   # anchor bar grid to this play start
        state.status_msg       = f"Playing — {state.groove_bpm:.1f} BPM"

    elif t in ("playback_stopped", "playback_done"):
        state.is_playing        = False
        state.playback_start_ns = None
        state.status_msg = "Stopped" if t == "playback_stopped" else "Done"

    elif t == "input_opened":
        state.input_port = ev.get("port", "")
        state.status_msg = f"Input: {state.input_port}"
        save_settings(state)

    elif t == "output_opened":
        state.output_port = ev.get("port", "")
        save_settings(state)

    elif t == "loop_set":
        state.loop_enabled = ev.get("enabled", False)


if __name__ == "__main__":
    main()
