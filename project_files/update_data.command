#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "Setup is missing. See README, First-time setup."
  read -r
  exit 1
fi
caffeinate -i .venv/bin/python update_data.py
echo "Finished. You can close this window."
