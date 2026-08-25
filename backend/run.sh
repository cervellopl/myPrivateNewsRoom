#!/usr/bin/env bash
# Start myPrivateNewsRoom. Everything is configurable through the environment;
# see newsroom/config.py for the full list.
set -euo pipefail
cd "$(dirname "$0")"

export NEWSROOM_PORT="${NEWSROOM_PORT:-6000}"
export NEWSROOM_POLL_INTERVAL="${NEWSROOM_POLL_INTERVAL:-900}"   # 15 minutes

if [ ! -d .venv ] && [ "${NEWSROOM_NO_VENV:-0}" != "1" ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

exec "$PY" -m newsroom --host "${NEWSROOM_HOST:-0.0.0.0}" --port "$NEWSROOM_PORT"
