use std::io::{self, BufRead, Write};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use std::fs;

use midir::{MidiInput, MidiOutput, MidiOutputConnection, MidiInputConnection};
use midly::{Smf, TrackEventKind, MetaMessage};
use serde_json::json;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn now_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos() as u64
}

fn emit(tx: &Sender<String>, msg: serde_json::Value) {
    tx.send(msg.to_string()).ok();
}

// ---------------------------------------------------------------------------
// MIDI file parsing
// ---------------------------------------------------------------------------

struct NoteEvent {
    time_us: u64,
    note: u8,
    velocity: u8,
    is_on: bool,
}

fn parse_midi(path: &str) -> (Vec<NoteEvent>, f64) {
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("[midi_capture] failed to read {}: {}", path, e);
            return (vec![], 120.0);
        }
    };

    let smf = match Smf::parse(&data) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[midi_capture] failed to parse MIDI: {}", e);
            return (vec![], 120.0);
        }
    };

    let ticks_per_beat: u64 = match smf.header.timing {
        midly::Timing::Metrical(t) => t.as_int() as u64,
        _ => 480,
    };

    // Build global tempo map from all tracks
    let mut tempo_map: Vec<(u64, u64)> = vec![(0, 500_000)]; // (tick, us_per_beat)
    for track in &smf.tracks {
        let mut tick: u64 = 0;
        for event in track.iter() {
            tick += event.delta.as_int() as u64;
            if let TrackEventKind::Meta(MetaMessage::Tempo(t)) = event.kind {
                tempo_map.push((tick, t.as_int() as u64));
            }
        }
    }
    tempo_map.sort_by_key(|x| x.0);
    tempo_map.dedup_by_key(|x| x.0);

    // Convert absolute tick to microseconds using tempo map
    let tick_to_us = |target_tick: u64| -> u64 {
        let mut us: u64 = 0;
        let mut prev_tick: u64 = 0;
        let mut prev_tempo: u64 = 500_000;
        for &(tick, tempo) in &tempo_map {
            if tick >= target_tick {
                break;
            }
            us += (tick - prev_tick) * prev_tempo / ticks_per_beat;
            prev_tick = tick;
            prev_tempo = tempo;
        }
        us += (target_tick - prev_tick) * prev_tempo / ticks_per_beat;
        us
    };

    // Derive BPM from first tempo event
    let first_tempo_us = tempo_map.first().map(|x| x.1).unwrap_or(500_000);
    let bpm = 60_000_000.0 / first_tempo_us as f64;

    // Collect note events from all tracks
    let mut events: Vec<NoteEvent> = Vec::new();
    for track in &smf.tracks {
        let mut tick: u64 = 0;
        for event in track.iter() {
            tick += event.delta.as_int() as u64;
            if let TrackEventKind::Midi { channel: _, message } = event.kind {
                match message {
                    midly::MidiMessage::NoteOn { key, vel } => {
                        let is_on = vel.as_int() > 0;
                        events.push(NoteEvent {
                            time_us: tick_to_us(tick),
                            note: key.as_int(),
                            velocity: vel.as_int(),
                            is_on,
                        });
                    }
                    midly::MidiMessage::NoteOff { key, vel } => {
                        events.push(NoteEvent {
                            time_us: tick_to_us(tick),
                            note: key.as_int(),
                            velocity: vel.as_int(),
                            is_on: false,
                        });
                    }
                    _ => {}
                }
            }
        }
    }

    events.sort_by_key(|e| e.time_us);
    (events, bpm)
}

// ---------------------------------------------------------------------------
// Port listing
// ---------------------------------------------------------------------------

fn list_input_ports() -> Vec<String> {
    match MidiInput::new("pseta_list") {
        Ok(mi) => mi.ports().iter()
            .filter_map(|p| mi.port_name(p).ok())
            .collect(),
        Err(_) => vec![],
    }
}

fn list_output_ports() -> Vec<String> {
    match MidiOutput::new("pseta_list") {
        Ok(mo) => mo.ports().iter()
            .filter_map(|p| mo.port_name(p).ok())
            .collect(),
        Err(_) => vec![],
    }
}

// ---------------------------------------------------------------------------
// Commands from Python
// ---------------------------------------------------------------------------

#[derive(Debug)]
enum Cmd {
    Load(String),
    Play,
    Stop,
    Loop(bool),
    MidiOutEnable(bool),
    SetInputPort(String),
    SetOutputPort(String),
    Quit,
}

fn parse_cmd(line: &str) -> Option<Cmd> {
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    match v.get("cmd")?.as_str()? {
        "load"         => Some(Cmd::Load(v["path"].as_str()?.to_string())),
        "play"         => Some(Cmd::Play),
        "stop"         => Some(Cmd::Stop),
        "loop"         => Some(Cmd::Loop(v["enabled"].as_bool().unwrap_or(false))),
        "midi_out_en"  => Some(Cmd::MidiOutEnable(v["enabled"].as_bool().unwrap_or(false))),
        "set_input"    => Some(Cmd::SetInputPort(v["port"].as_str()?.to_string())),
        "set_output"   => Some(Cmd::SetOutputPort(v["port"].as_str()?.to_string())),
        "quit"         => Some(Cmd::Quit),
        _              => None,
    }
}

// ---------------------------------------------------------------------------
// Playback thread
// ---------------------------------------------------------------------------

fn spawn_playback(
    events: Vec<NoteEvent>,
    loop_flag: Arc<Mutex<bool>>,
    stop_rx: std::sync::mpsc::Receiver<()>,
    out_tx: Sender<String>,
    midi_out: Arc<Mutex<Option<MidiOutputConnection>>>,
    midi_out_enabled: Arc<Mutex<bool>>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        loop {
            let start = Instant::now();
            for ev in &events {
                // Non-blocking stop check
                if stop_rx.try_recv().is_ok() {
                    return;
                }

                // Sleep until event time
                let elapsed_us = start.elapsed().as_micros() as u64;
                if ev.time_us > elapsed_us {
                    thread::sleep(Duration::from_micros(ev.time_us - elapsed_us));
                }

                // Check stop again after sleep
                if stop_rx.try_recv().is_ok() {
                    return;
                }

                let t = now_ns();
                let type_str = if ev.is_on { "note_on" } else { "note_off" };

                // MIDI hardware out (optional)
                if *midi_out_enabled.lock().unwrap() {
                    if let Some(conn) = midi_out.lock().unwrap().as_mut() {
                        let status: u8 = if ev.is_on { 0x99 } else { 0x89 }; // ch 10 drums
                        conn.send(&[status, ev.note, ev.velocity]).ok();
                    }
                }

                // Emit to Python
                emit(&out_tx, json!({
                    "type": type_str,
                    "t": t,
                    "source": "playback",
                    "note": ev.note,
                    "velocity": ev.velocity,
                }));
            }

            if !*loop_flag.lock().unwrap() {
                emit(&out_tx, json!({ "type": "playback_done" }));
                break;
            }
        }
    })
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

#[allow(unused_assignments)]
fn main() {
    // Stdout writer thread: serialize all output through one channel to avoid interleaving
    let (out_tx, out_rx) = channel::<String>();
    thread::spawn(move || {
        let stdout = io::stdout();
        let mut out = stdout.lock();
        for line in out_rx {
            let _ = writeln!(out, "{}", line);
            let _ = out.flush();
        }
    });

    // Stdin reader thread: parse commands and send to main loop
    let (cmd_tx, cmd_rx) = channel::<Cmd>();
    {
        let cmd_tx = cmd_tx.clone();
        thread::spawn(move || {
            let stdin = io::stdin();
            for line in stdin.lock().lines() {
                match line {
                    Ok(l) if !l.trim().is_empty() => {
                        if let Some(cmd) = parse_cmd(&l) {
                            if matches!(cmd, Cmd::Quit) {
                                cmd_tx.send(cmd).ok();
                                return;
                            }
                            cmd_tx.send(cmd).ok();
                        } else {
                            eprintln!("[midi_capture] unknown command: {}", l);
                        }
                    }
                    Err(_) => return,
                    _ => {}
                }
            }
        });
    }

    // Shared state
    let loop_flag        = Arc::new(Mutex::new(false));
    let midi_out_enabled = Arc::new(Mutex::new(false));
    let midi_out_conn: Arc<Mutex<Option<MidiOutputConnection>>> = Arc::new(Mutex::new(None));

    // Playback control
    let mut stop_tx: Option<std::sync::mpsc::SyncSender<()>> = None;
    let mut loaded_events: Vec<NoteEvent> = vec![];
    let mut loaded_bpm: f64 = 120.0;

    // MIDI input connection — kept alive here via RAII; never read, only held.
    let mut midi_in_conn: Option<MidiInputConnection<()>> = None;

    // Announce available ports and ready state
    let inputs  = list_input_ports();
    let outputs = list_output_ports();
    emit(&out_tx, json!({
        "type": "ports",
        "input":  inputs,
        "output": outputs,
    }));
    emit(&out_tx, json!({ "type": "ready" }));

    // Command dispatch loop
    loop {
        let cmd = match cmd_rx.recv() {
            Ok(c) => c,
            Err(_) => break,
        };

        match cmd {
            Cmd::Quit => break,

            Cmd::Load(path) => {
                // Stop current playback
                if let Some(tx) = stop_tx.take() {
                    tx.try_send(()).ok();
                }
                let (events, bpm) = parse_midi(&path);
                loaded_bpm = bpm;
                loaded_events = events;
                let duration_us = loaded_events.last().map(|e| e.time_us).unwrap_or(0);
                emit(&out_tx, json!({
                    "type": "file_loaded",
                    "path": path,
                    "bpm":  loaded_bpm,
                    "event_count": loaded_events.len(),
                    "duration_us": duration_us,
                }));
            }

            Cmd::Play => {
                // Stop any running playback first
                if let Some(tx) = stop_tx.take() {
                    tx.try_send(()).ok();
                }
                if loaded_events.is_empty() {
                    eprintln!("[midi_capture] no file loaded");
                    continue;
                }
                // Clone events for thread
                let events: Vec<NoteEvent> = loaded_events.iter().map(|e| NoteEvent {
                    time_us: e.time_us,
                    note: e.note,
                    velocity: e.velocity,
                    is_on: e.is_on,
                }).collect();

                let (s_tx, s_rx) = std::sync::mpsc::sync_channel::<()>(1);
                stop_tx = Some(s_tx);

                spawn_playback(
                    events,
                    Arc::clone(&loop_flag),
                    s_rx,
                    out_tx.clone(),
                    Arc::clone(&midi_out_conn),
                    Arc::clone(&midi_out_enabled),
                );
                emit(&out_tx, json!({ "type": "playback_started", "bpm": loaded_bpm }));
            }

            Cmd::Stop => {
                if let Some(tx) = stop_tx.take() {
                    tx.try_send(()).ok();
                }
                emit(&out_tx, json!({ "type": "playback_stopped" }));
            }

            Cmd::Loop(enabled) => {
                *loop_flag.lock().unwrap() = enabled;
                emit(&out_tx, json!({ "type": "loop_set", "enabled": enabled }));
            }

            Cmd::MidiOutEnable(enabled) => {
                *midi_out_enabled.lock().unwrap() = enabled;
                emit(&out_tx, json!({ "type": "midi_out_set", "enabled": enabled }));
            }

            Cmd::SetInputPort(name) => {
                // Drop old connection
                midi_in_conn = None;

                let capture_tx = out_tx.clone();
                let mi = match MidiInput::new("pseta_capture") {
                    Ok(m) => m,
                    Err(e) => {
                        eprintln!("[midi_capture] MidiInput error: {}", e);
                        continue;
                    }
                };
                let port = mi.ports().into_iter()
                    .find(|p| mi.port_name(p).as_deref() == Ok(name.as_str()));

                match port {
                    Some(p) => {
                        match mi.connect(&p, "pseta_capture", move |_ts, data, _| {
                            if data.len() < 3 { return; }
                            let status = data[0] & 0xF0;
                            let note   = data[1];
                            let vel    = data[2];
                            let (is_on, type_str) = match status {
                                0x90 if vel > 0 => (true,  "note_on"),
                                0x90 | 0x80     => (false, "note_off"),
                                _ => return,
                            };
                            let t = now_ns();
                            let _ = is_on; // captured in type_str
                            capture_tx.send(json!({
                                "type":     type_str,
                                "t":        t,
                                "source":   "capture",
                                "note":     note,
                                "velocity": vel,
                            }).to_string()).ok();
                        }, ()) {
                            Ok(conn) => {
                                midi_in_conn = Some(conn);
                                emit(&out_tx, json!({ "type": "input_opened", "port": name }));
                            }
                            Err(e) => {
                                eprintln!("[midi_capture] connect error: {}", e);
                            }
                        }
                    }
                    None => eprintln!("[midi_capture] input port not found: {}", name),
                }
            }

            Cmd::SetOutputPort(name) => {
                let mo = match MidiOutput::new("pseta_output") {
                    Ok(m) => m,
                    Err(e) => {
                        eprintln!("[midi_capture] MidiOutput error: {}", e);
                        continue;
                    }
                };
                let port = mo.ports().into_iter()
                    .find(|p| mo.port_name(p).as_deref() == Ok(name.as_str()));

                match port {
                    Some(p) => {
                        match mo.connect(&p, "pseta_output") {
                            Ok(conn) => {
                                *midi_out_conn.lock().unwrap() = Some(conn);
                                emit(&out_tx, json!({ "type": "output_opened", "port": name }));
                            }
                            Err(e) => eprintln!("[midi_capture] output connect error: {}", e),
                        }
                    }
                    None => eprintln!("[midi_capture] output port not found: {}", name),
                }
            }
        }
    }
}
