#!/usr/bin/env bash
set -e

# Build Rust binary
cargo build --release 2>&1

# Activate Python venv
source .venv/bin/activate

# Install Python deps if needed
pip install pygame tomli --quiet

# Launch UI (starts Rust binary as subprocess)
python main.py
