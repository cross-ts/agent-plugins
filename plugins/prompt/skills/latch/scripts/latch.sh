#!/usr/bin/env bash
# usage: latch.sh EXP_DIR --held COND
# trace self-check (no model) -> probe (real CLIs: no instruction file may leak) -> trials -> grading -> decision
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
EXP="$1"; shift
python3 "$D/check_trace.py" "$EXP"
python3 "$D/run_trials.py" "$EXP" --probe
python3 "$D/run_trials.py" "$EXP"
python3 "$D/grade.py" "$EXP"
python3 "$D/decide.py" "$EXP" "$@"
