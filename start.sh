#!/usr/bin/env sh
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
export YUEGUANG_PYTHON="$PWD/.venv/bin/python"
echo 'Practice panel: http://127.0.0.1:1234/robot_windows/moonlight/moonlight.html?name=Moonlight'
exec webots --batch --mode=realtime --stream worlds/moonlight.wbt
