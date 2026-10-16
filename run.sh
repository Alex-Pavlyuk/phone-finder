#!/usr/bin/env bash
# Один прогон phone-finder. Используется и вручную, и из launchd.
#
# Виртуальное окружение берётся своё, а если его нет — родительского проекта:
# зависимости у car-finder и phone-finder одни и те же (curl_cffi, PyYAML,
# rumps), и держать вторую копию тех же колёс незачем.
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x "../.venv/bin/python" ]; then
  PY="../.venv/bin/python"
else
  echo "Нет .venv — сначала: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec "$PY" -m phonefinder run "$@"
