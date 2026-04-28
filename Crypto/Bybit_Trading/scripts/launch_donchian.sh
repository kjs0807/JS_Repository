#!/bin/bash
# Launcher for Donchian exploration with detached process
# Usage: ./scripts/launch_donchian.sh coarse
#        ./scripts/launch_donchian.sh coarse --reset

cd "$(dirname "$0")/.."

STAGE="${1:-coarse}"
shift || true
EXTRA_ARGS="$*"

mkdir -p logs/research/donchian

# Launch detached
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    # Windows Git Bash
    cmd.exe //C "start /B python scripts/explore_donchian.py $STAGE $EXTRA_ARGS"
else
    nohup python scripts/explore_donchian.py $STAGE $EXTRA_ARGS &
fi

echo "Launched. Check status with:"
echo "  python scripts/explore_donchian.py status"
echo "  tail -f logs/research/donchian/explore.log"
