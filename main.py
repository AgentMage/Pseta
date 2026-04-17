"""
Pseta — main UI
pygame frontend: timeline, pad grids, kit panel, transport, Pseta options.
Launches Rust midi_capture binary as a subprocess; communicates via JSONL pipe.
"""

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tomllib

import pygame

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
    }
    with open(SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

W, H       = 1280, 720
FPS        = 60

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
TIMELINE_R = pygame.Rect(0,   0,   W,   int(H * 0.35))
PADS_R     = pygame.Rect(0,   int(H * 0.35), W, int(H * 0.32))
CONTROLS_R = pygame.Rect(0,   int(H * 0.67), W, int(H * 0.33))

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

    # Lane backgrounds
    pygame.draw.rect(surf, (22, 30, 45), (r.x+2, groove_y, tw, lane_h))
    pygame.draw.rect(surf, (45, 28, 18), (r.x+2, user_y,   tw, lane_h))
    pygame.draw.rect(surf, (20, 36, 20), (r.x+2, zeta_y,   tw, zeta_h))

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
        in_stream = pad_col is not None and sum(
            1 for (s, n), c in state.pad_colors.items()
            if c == pad_col and (s, n) != (source, note)
        ) >= 1
        if in_stream:
            pulse = (math.sin(time.time() * 4) + 1) / 2
            draw_col = tuple(int(pad_col[i] * (0.4 + 0.6 * pulse)) for i in range(3))
        else:
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

    # Column header
    hy = rect.y + MONITOR_HEADER
    for x_off, hdr in ((8, "  ago"), (58, "type"), (100, "note"), (148, "name"), (220, "vel")):
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

        is_match = (active_note is not None and ev["note"] == active_note)
        fg  = hit_col if is_match else TEXT
        dim = hit_col if is_match else TEXT_DIM

        age     = now - ev["t"]
        age_str = f"{age:5.1f}s" if age < 60 else f"{age/60:4.1f}m"
        typ     = "ON " if ev["type"] == "note_on" else "OFF"
        name    = GM_DRUMS.get(ev["note"], "---")
        vel     = str(ev["velocity"]) if ev["type"] == "note_on" else ""

        for x_off, val, col in (
            (8,   age_str,         dim),
            (58,  typ,             fg),
            (100, str(ev["note"]), fg),
            (148, name,            fg),
            (220, vel,             dim),
        ):
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

    # --- Streams ---
    streams_r = pygame.Rect(r.x + third, r.y, third, r.height)
    surf.blit(font_sm.render("STREAMS", True, TEXT_DIM), (streams_r.x + 8, streams_r.y + 6))

    # Group pad_colors by color value
    by_color: dict = {}
    for (src, nt), col in state.pad_colors.items():
        by_color.setdefault(col, []).append((src, nt))

    sy = streams_r.y + 24
    if by_color:
        for col, members in sorted(by_color.items()):
            if len(members) < 2:
                continue
            if sy + 16 > streams_r.bottom - 4:
                break
            swatch_r = pygame.Rect(streams_r.x + 8, sy + 1, 10, 10)
            pygame.draw.rect(surf, col, swatch_r, border_radius=2)
            src_labels = []
            for src, nt in members:
                name = GM_DRUMS.get(nt, f"n{nt}")
                lane = "G" if src == "playback" else "U"
                src_labels.append(f"{lane}:{name[:6]}")
            label = "  " + "  ·  ".join(src_labels)
            surf.blit(font_sm.render(label, True, TEXT), (streams_r.x + 22, sy))
            sy += 17
    else:
        surf.blit(font_sm.render("no streams", True, TEXT_DIM), (streams_r.x + 8, sy))

    # --- Transport ---
    tr_r = pygame.Rect(r.x + 2*third, r.y, third, r.height)
    surf.blit(font_sm.render("TRANSPORT", True, TEXT_DIM), (tr_r.x+8, tr_r.y+6))

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

    # File path + progress bar
    info_x = tr_r.x + 8
    info_w = tr_r.width - 16
    iy     = by + 6

    if state.loaded_path:
        fname    = os.path.basename(state.loaded_path)
        max_ch   = info_w // 7
        if len(fname) > max_ch:
            fname = fname[:max_ch - 1] + "…"
        surf.blit(font_sm.render(fname, True, TEXT_DIM), (info_x, iy))
        iy += 16

        pct = _playback_pct(state)
        if pct is not None:
            bar_h   = 6
            filled  = int(info_w * pct / 100)
            pygame.draw.rect(surf, (50, 50, 50),  (info_x, iy, info_w, bar_h), border_radius=3)
            pygame.draw.rect(surf, GROOVE_HIT,    (info_x, iy, filled, bar_h), border_radius=3)
            pct_lbl = font_sm.render(f"{pct:.0f}%", True, TEXT_DIM)
            surf.blit(pct_lbl, (info_x + info_w + 4, iy - 3))
            iy += bar_h + 4

    # Status
    surf.blit(font_sm.render(state.status_msg, True, TEXT_DIM), (info_x, tr_r.bottom - 20))

    # Port selector buttons
    px = tr_r.x + bw + 16
    in_label  = f"IN:  {state.input_port  or '(none)'}"
    out_label = f"OUT: {state.output_port or '(none)'}"
    in_r  = pygame.Rect(px, tr_r.y + 26, third - bw - 24, bh)
    out_r = pygame.Rect(px, tr_r.y + 26 + bh + bpad, third - bw - 24, bh)
    in_active  = state.port_picker == "input"
    out_active = state.port_picker == "output"
    button(surf, font_sm, in_r,  in_label,  active=in_active,  hover=in_r.collidepoint(mouse_pos))
    button(surf, font_sm, out_r, out_label, active=out_active, hover=out_r.collidepoint(mouse_pos))
    buttons["port_in"]  = in_r
    buttons["port_out"] = out_r

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

    # Count how many pads (excluding the one being edited) use each color
    usage = {}
    for (s, n), c in state.pad_colors.items():
        if (s, n) != (source, note):
            usage[c] = usage.get(c, 0) + 1

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
            count = usage.get(col, 0)
            if count >= 2:
                pulse = (math.sin(time.time() * 5) + 1) / 2
                draw_col = tuple(int(col[i] * (0.3 + 0.7 * pulse)) for i in range(3))
            else:
                draw_col = col
            pygame.draw.rect(surf, draw_col, sw_r, border_radius=3)
            if count == 1:
                pygame.draw.circle(surf, (255, 255, 255), sw_r.center, 3)
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

    # Four-panel layout inside PADS_R (left → right):
    #   groove_midi | groove_pads (4×4) | user_pads (4×4) | user_midi
    gap        = 16
    grid_w     = PAD_GRID_COLS * 76 + (PAD_GRID_COLS - 1) * PAD_GRID_GAP
    mon_w      = (PADS_R.width - gap - grid_w - gap - grid_w - gap - 6 - gap) // 2

    groove_mon_r  = pygame.Rect(PADS_R.x + gap,            PADS_R.y, mon_w,   PADS_R.height)
    groove_grid_r = pygame.Rect(groove_mon_r.right + gap,  PADS_R.y, grid_w,  PADS_R.height)
    user_grid_r   = pygame.Rect(groove_grid_r.right + gap, PADS_R.y, grid_w,  PADS_R.height)
    user_mon_r    = pygame.Rect(user_grid_r.right + gap,   PADS_R.y, mon_w,   PADS_R.height)

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

                else:
                    clicked = False

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
                                        sharing = [k for k, v in state.pad_colors.items()
                                                   if v == col and k != (src, nt)]
                                        if len(sharing) >= 2:
                                            state.pad_colors.pop(sharing[0], None)
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

        # --- Tick flash ---
        state.tick_flash()

        # --- Render ---
        screen.fill(BG)
        buttons = {}

        draw_timeline(screen, font_sm, state)

        # Pad section — groove_midi | groove_pads (4×4) | user_pads (4×4) | user_midi
        draw_rect_border(screen, PADS_R, PANEL_BG, BORDER, radius=0)
        draw_midi_monitor(screen, font_sm, state, groove_mon_r, source_filter="playback")
        draw_groove_pads(screen, font, font_sm, state, groove_grid_r, buttons)
        draw_user_pads(screen, font, font_sm, state, user_grid_r, buttons)
        draw_midi_monitor(screen, font_sm, state, user_mon_r,   source_filter="capture")

        draw_controls(screen, font, font_sm, state, mouse_pos, buttons)
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
        state.status_msg         = f"Loaded ({ev.get('event_count',0)} events, {state.groove_bpm:.1f} BPM)"

    elif t == "playback_started":
        state.groove_bpm       = ev.get("bpm", state.groove_bpm)
        state.is_playing       = True
        state.playback_start_ns = time.time_ns()
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
