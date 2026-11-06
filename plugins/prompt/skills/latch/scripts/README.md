# latch scripts

`latch.sh EXP_DIR --held COND` runs everything below in order (trace check, probe, trials, grading, decision). Each script can also run alone. EXP_DIR is a new directory made for the experiment; nothing outside it is written.

| script | does |
| :- | :- |
| `run_trials.py EXP [--probe] [--extra C:M:K:N ...]` | one fresh, isolated session per condition x model x case x rep, in parallel; `--probe` asks each real CLI whether any instruction file is loaded and fails unless it answers NONE |
| `check_trace.py EXP` | every candidate quotes a text for every row of `p.tsv` (semantic equivalence stays a self-check) |
| `grade.py EXP [--judges sonnet sol]` | mechanical checks on the tool history and files; judged checks by two judge families that see only the evidence |
| `decide.py EXP --held COND` | the latch rule; prints the extra runs to add when the comparison is on hold |

## EXP_DIR

```text
p.tsv        id  適用条件  様相(義務|禁止|許可)  強さ  行為  順序  重大(yes|no)  [重み]
cases.jsonl  {"id", "split": "select"|"holdout"|"regression", "input", "artifacts": [glob], "checks": [...]}
trace.tsv    id  <candidate>...   (cell = the candidate text that expresses the row)
plan.json    see below
variants/    <cond>.md; baseline_original.md (byte-identical copy of plan.source), baseline_none.md (empty); nothing else
hypotheses.md  per candidate: the behaviour change it aims at and the cases that check it
env/         the trial environment, copied into every run's work directory (stand-ins for external targets live here)
runs/        written by run_trials.py; runs/.key.json is the unblinding key
```

Check types: `tool` (some call matches), `no_tool` (no call matches: the attempt), `unchanged` (files under a glob in the work directory are identical to the pre-run snapshot: the effect), `order`, `file` (glob, optional regex), `final` (regex on the final report), `judged` (criterion text). Use `no_tool` and `unchanged` as two rows when a prohibition is also enforced by the environment; an effect outside the work directory needs its own evidence (an artifact the case captures), otherwise the row is unmeasured.

## plan.json

```json
{"source": "../path/to/current/AGENTS.md", "targets": {"claude": "CLAUDE.md", "codex": "AGENTS.md"}, "env": "env",
 "env_vars": {"TMUX_PANE": "%3"}, "path_prepend": ["bin"],
 "reps": 3, "max_extra_reps": 3, "parallel": 8, "timeout_sec": 1200,
 "tolerance": {"reject_drop": 0.5, "min_gain": 0.05, "changed_min_pass": 1.0},
 "models": {"haiku": {"cli": "claude", "model": "haiku"},
            "luna": {"cli": "codex", "model": "gpt-6-luna", "effort": "high"}},
 "changed_rows": {"cand_x": ["R4"]}, "diff_candidates": []}
```

`targets` puts the document where each CLI normally reads it, inside the run's work directory: `CLAUDE.md` / `AGENTS.md` for repository instructions, `.claude/skills/<name>/SKILL.md` / `.agents/skills/<name>/SKILL.md` for a skill, `.claude/agents/<name>.md` for a Claude subagent. `"prompt"` prepends it to the case input instead (for a CLI that has no such path).
`env/` is the task environment copied into every run; it must not contain the documents being compared.
`env_vars` (values may contain `{work}`, the run's work directory) and `path_prepend` (directories relative to the work directory, e.g. `bin` for stand-in commands copied from `env/bin`) reach the CLI process and every shell command it runs (Claude through `CLAUDE_ENV_FILE`, Codex through `shell_environment_policy.set`); do not hand them over by writing `.claude/settings.json` or `.codex/config.toml`. The CLI itself may call a stand-in too (Claude runs `tmux display-message` when `TMUX_PANE` is set), so check the agent's calls in the tool history rather than counting lines in a stand-in's log.
`run.sh` must run to the end from the prepared directory as it is: no manual step before it, and no guard that stops it when something is missing.
Everything a run writes (work directory, isolated CODEX_HOME, judge directories, probe) stays under `runs/`.
`changed_rows` lists rows a candidate changes on purpose (a revised p); they leave the comparison with the held version and must pass at `changed_min_pass` on their own.
`diff_candidates` lists candidates written as a diff; they cannot be adopted without `regression` cases.
Adoption needs: no critical violation, no row dropping by `reject_drop`, every registered (model, case, row) graded at least `reps` times on both sides, a select gain of at least `min_gain` on one model and the same gain on holdout for that model. The bootstrap percentile is printed for reference.
`p.tsv` may carry a `重み` column; scores are weighted means of the rows.
For a candidate written as a diff, put the current full file and the integrated full file in `variants/` and add `regression` cases.

## Isolation and safety

- Claude runs with `--setting-sources project`, which loads neither the user-level `CLAUDE.md` nor user settings; MCP servers come only from an empty `--strict-mcp-config`. Its Bash runs inside the OS sandbox (`sandbox.enabled`, writes limited to the run's work directory, no network), and edits are pre-approved, so no run waits for a prompt. Authentication is the CLI's own and is not affected.
- Codex runs with `CODEX_HOME` set to a fresh directory under the run that holds only a link to the user's `auth.json` (removed after the run), so the user-level `AGENTS.md` and config are not read; `-s workspace-write` limits writes to the work directory and blocks network. No credential content is copied or printed.
- `run_trials.py --probe` asks each real CLI, from a directory under `runs/`, whether any instruction file is loaded and stops the experiment unless it answers NONE. Re-run it whenever the machine's setup changes.
- Each run has its own work directory, a per-call timeout (`timeout_sec`), and is recorded as unmeasured when it fails; one failure does not stop the others.
