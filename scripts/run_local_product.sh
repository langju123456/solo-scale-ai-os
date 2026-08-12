#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_root="${SOLOSCALE_DATA_ROOT:-/Users/ju.l/Documents/SoloScaleData}"
python_bin="$project_root/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
  echo "SoloScale environment is missing. Run: uv venv .venv && uv pip install -e '.[dev]' -e packages/buildlog" >&2
  exit 1
fi

mkdir -p "$data_root"/{knowledge,career,learning,content,video,publishing}
chmod 700 "$data_root" "$data_root"/{knowledge,career,learning,content,video,publishing}

export BUILDLOG_CONFIG_ROOT="${BUILDLOG_CONFIG_ROOT:-/Users/ju.l/Documents/AI TEAM}"
export PYTHONPATH="$project_root/src:$project_root/packages/buildlog/src${PYTHONPATH:+:$PYTHONPATH}"

exec "$python_bin" -m soloscale.local_ui \
  --host "${SOLOSCALE_HOST:-127.0.0.1}" \
  --port "${SOLOSCALE_PORT:-8765}" \
  --data-root "$data_root"
