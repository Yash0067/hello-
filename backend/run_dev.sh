#!/usr/bin/env bash
# Load .env then start the Adarsh Boutique backend (serves the website + admin).
set -a; [ -f ../.env ] && . ../.env; set +a
PORT="${PORT:-8000}"
APP_ENV="${APP_ENV:-development}"
export PORT APP_ENV
exec python server.py
