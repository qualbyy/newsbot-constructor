#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ -f "venv/bin/python" ]; then
    exec venv/bin/python main.py "$@"
elif [ -f "/home/qualbyy/TeakBot/venv/bin/python" ]; then
    exec /home/qualbyy/TeakBot/venv/bin/python main.py "$@"
else
    exec python3 main.py "$@"
fi
