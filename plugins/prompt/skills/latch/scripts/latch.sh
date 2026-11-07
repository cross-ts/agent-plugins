#!/usr/bin/env bash
# usage: latch.sh EXP_DIR --held COND
# trace self-check (no model) -> probe (ancestors, then real CLIs: no instruction file may leak) -> trials -> grading
# -> decision, then the extra and gate runs the decision asks for (decide.py --next), until it asks for none
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
EXP="$1"; shift
python3 "$D/check_trace.py" "$EXP"
python3 "$D/run_trials.py" "$EXP" --probe
python3 "$D/run_trials.py" "$EXP"
python3 "$D/grade.py" "$EXP"
python3 "$D/decide.py" "$EXP" "$@"
# every pass adds runs toward reps + max_extra_reps or the gate reps, so the loop ends; the bound is a backstop
for _ in $(seq 1 30); do
  next="$(python3 "$D/decide.py" "$EXP" "$@" --next)"
  [ -n "$next" ] || exit 0
  read -r -a args <<< "$next"
  python3 "$D/run_trials.py" "$EXP" "${args[@]}"
  python3 "$D/grade.py" "$EXP"
  python3 "$D/decide.py" "$EXP" "$@"
done
echo "latch.sh: the decision still asks for runs after 30 passes; see decision.json" >&2
exit 1
