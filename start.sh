#!/usr/bin/env bash
# GeoSyntra Bridge - CAD to GDB Schema Converter (Linux / macOS launcher)
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python -m geosyntra_bridge.server "$@"
