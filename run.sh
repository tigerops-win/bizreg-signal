#!/bin/sh
set -eu

export PORT="${PORT:-8080}"
exec python server.py
