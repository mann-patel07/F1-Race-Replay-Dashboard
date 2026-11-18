#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "Setup is missing. See README, First-time setup."
  read -r
  exit 1
fi
.venv/bin/python forget_f1tv_signin.py
echo "Press Enter to close."
read -r
