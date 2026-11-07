# latch scripts

`latch.sh EXP_DIR --held COND` runs everything below in order (trace check, probe, trials, grading, decision), then the extra runs and the gate runs the decision asks for (`decide.py --next`), grading and deciding again until it asks for none. Each script can also run alone. EXP_DIR is a new directory made for the experiment; nothing outside it is written.

| script | does |
| :- | :- |
| `run_trials.py EXP [--probe] [--extra C:M:K:N ...] [--gate C ...]` | one fresh, isolated session per condition x model x case x rep, in parallel; `--probe` scans the experiment's ancestors for instruction files and git roots, then asks each real CLI whether any instruction file is loaded and fails unless it answers NONE; `--gate` runs the gate models for the named conditions |
| `check_trace.py EXP` | every candidate quotes a text for every row of `p.tsv` (semantic equivalence stays a self-check); `baseline_original` and the A/A copies are byte-identical to what they copy |
| `grade.py EXP [--judges sonnet sol]` | mechanical checks on the tool history and files; judged checks by two judge families that see only the evidence |
| `decide.py EXP --held COND [--next]` | the latch rule; `--next` prints the `run_trials.py` arguments the decision still needs (nothing when none) |
| `simulate.py EXP [--assume LO:HI \| --from-runs EXP2] [--gains ...] [--reps ...]` | the rule's false rejection, false adoption and adoption rates on random grades; calls no model |
| `factorial.py EXP make --out EXP2`, `factorial.py EXP2 analyze` | a factorial experiment over parts of a candidate (drop or reword), and the effect of each part |

## EXP_DIR

```text
p.tsv          id  適用条件  様相(義務|禁止|許可)  強さ  行為  順序  重大(yes|no)  [重み]
cases.jsonl    {"id", "split": "select"|"holdout"|"regression", "input" | "inputs": [...], "artifacts": [glob], "checks": [...]}
trace.tsv      id  <candidate>...   (cell = the candidate text that expresses the row)
plan.json      see below
factors.json   for factorial.py, see below
variants/      <cond>.md; baseline_original.md (byte-identical copy of plan.source), baseline_none.md (empty),
               baseline_aa.md (byte-identical copy of baseline_original.md) or baseline_aa_<cond>.md (of a held
               candidate <cond>); nothing else
hypotheses.md  per candidate: the behaviour change it aims at and the cases that check it
env/           the trial environment, copied into every run's work directory (stand-ins for external targets live here)
runs/          written by run_trials.py; runs/.key.json is the unblinding key
decision.json  written by decide.py
```

Check types: `tool` (some call matches), `no_tool` (no call matches: the attempt), `unchanged` (files matching `path` and none of the optional `exclude` globs are identical to the pre-run snapshot and none were added: the effect), `order`, `file` (glob, optional regex), `final` (regex on the final report), `judged` (criterion text). Use `no_tool` and `unchanged` as two rows when a prohibition is also enforced by the environment; an effect outside the work directory needs its own evidence (an artifact the case captures), otherwise the row is unmeasured. `unchanged` with `"path": "*"` and the expected artifacts in `exclude` checks that nothing else was touched.

`tool`, `no_tool` and `order` patterns are searched in `"<kind> <tool> <text>"` of each history entry. `kind` is `shell`, `file_change`, `mcp` or `other` on both CLIs. For `shell`, `text` is the bare command on both (Codex's `/bin/zsh -lc '...'` is unwrapped), so `^shell .*git push` matches either. A Claude `file_change` text is the tool input (path and written text); a Codex `file_change` text holds only the paths, so check written content with `file` or `unchanged`.

`inputs` lists paraphrases of a case's input that share the expected behaviour on every row; repetition r uses `inputs[(r-1) % len(inputs)]`, the same in every condition, and `runs/<id>/case.json` holds the one used.

## plan.json

```json
{"source": "/abs/path/to/current/AGENTS.md", "targets": {"claude": "CLAUDE.md", "codex": "AGENTS.md"}, "env": "env",
 "env_vars": {"TMUX_PANE": "%3"}, "path_prepend": ["bin"],
 "reps": 3, "max_extra_reps": 3, "parallel": 8, "timeout_sec": 1200,
 "tolerance": {"reject_drop": 0.2, "confidence": 0.95, "min_gain": 0.05, "min_shrink": 0.1, "changed_min_pass": 1.0},
 "models": {"haiku": {"cli": "claude", "model": "haiku"},
            "luna": {"cli": "codex", "model": "gpt-6-luna", "effort": "high"}},
 "gate": {"models": {"opus": {"cli": "claude", "model": "opus"}}, "splits": ["holdout", "regression"], "reps": 3},
 "changed_rows": {"cand_x": ["R4"]}, "diff_candidates": []}
```

`targets` puts the document where each CLI normally reads it, inside the run's work directory: `CLAUDE.md` / `AGENTS.md` for repository instructions, `.claude/skills/<name>/SKILL.md` / `.agents/skills/<name>/SKILL.md` for a skill, `.claude/agents/<name>.md` for a Claude subagent. `"prompt"` prepends it to the case input instead (for a CLI that has no such path). Judges never see the `targets` paths, even when an artifact glob matches them.
`env/` is the task environment copied into every run; it must not contain the documents being compared.
`env_vars` (values may contain `{work}`, the run's work directory) and `path_prepend` (directories relative to the work directory, e.g. `bin` for stand-in commands copied from `env/bin`) reach the CLI process and every shell command it runs (Claude through `CLAUDE_ENV_FILE`, Codex through `shell_environment_policy.set`); do not hand them over by writing `.claude/settings.json` or `.codex/config.toml`. The CLI itself may call a stand-in too (Claude runs `tmux display-message` when `TMUX_PANE` is set), so check the agent's calls in the tool history rather than counting lines in a stand-in's log.
`models` are the exploration models: every condition runs on every case. `gate.models` (names distinct from `models`) are the models that actually read the document; they run only the finalists and the held version, only on the cases in `gate.splits`, `gate.reps` times (default `reps`).
`run.sh` must run to the end from the prepared directory as it is: no manual step before it, and no guard that stops it when something is missing.
Everything a run writes (work directory, isolated CODEX_HOME, judge directories, probe) stays under `runs/`. `runs/<id>/status.json` holds `state`, `rc`, `sec`, `tool_calls`, `tokens_in`, `tokens_out` and, for Claude, `turns` and `cost_usd` (Claude's `tokens_in` counts cache reads and writes); costs compare within a model.
`changed_rows` lists rows a candidate changes on purpose (a revised p); they leave the comparison with the held version and must pass at `changed_min_pass` in every case on their own.
`diff_candidates` lists candidates written as a diff; they cannot be adopted without `regression` cases.
`p.tsv` may carry a `重み` column; scores are weighted means of the rows.
For a candidate written as a diff, put the current full file and the integrated full file in `variants/` and add `regression` cases.

## Decision rule (decide.py)

- Estimate: for a (model, row), every case that checks the row gives the candidate's pass rate minus the held version's; the estimate is their mean, its standard error comes from each case's binomial variance (with 0.5 successes and 1 trial added) summed and divided by the squared number of cases, and bounds are one-sided at `confidence`. `reject_drop` is a margin on this pooled drop, not on a single cell. The cases are fixed; generalisation is what holdout checks.
- Rows: the lower bound of the drop at or above `reject_drop` rejects; the upper bound below it is non-inferior; in between, both sides of every case of the row get extra runs (brought to the same count, at most `reps + max_extra_reps`), and in between at that cap the held version stays. A row needs `reps` valid grades on both sides of each of its cases.
- Critical rows: if the held version never violated the row on that model, one violation by the candidate rejects it; otherwise the row is judged as above with `reject_drop` 0, so the candidate must show a lower violation rate. Zero violations print the rule-of-three bound 3/n.
- Adoption: non-inferior on every row of the exploration models, and either a select gain of at least `min_gain` on some model with the same holdout gain on each such model, or a document of at most `1 - min_shrink` of the held version's characters. With `gate`, the gate models then run the held version and the candidate on `gate.splits`, and every row must be non-inferior there too (no gain asked).
- Futility: a candidate on hold whose select upper bound is below `min_gain` on every model and that is not shorter gets no more runs.
- A/A: the copy of the held version is judged like a candidate and never adopted; if it would be adopted, every candidate keeps the held version; if it is rejected, a warning says that rejections in the round may be false.
- Defaults `reject_drop` 0.2 and `confidence` 0.95 were chosen with `simulate.py` on 9 cases x 3 of 5 rows, 2 models, reps 3, cap 6, `min_gain` 0.05: at held pass rates 0.6-0.95 / 0.4-0.8, A/A false rejection about 1-4% / 6-7% and false adoption at most 1%, and a +0.15 gain adopted about 64-70% / 36-44% of the time. Check your own table with `simulate.py` before registering.
- `decide.py` also prints the document length, median tokens, tool calls and seconds per condition and model, and the split of run-score variance between cases and within a case (when cases dominate, more cases help more than more reps).

## simulate.py

`simulate.py EXP [--assume LO:HI | --from-runs EXP2 [--held COND]] [--gate-assume LO:HI] [--gains 0,0.1,0.2] [--trials 200] [--reps 3,5]` builds the registered table (plan, cases, p.tsv) for a held version and one candidate, draws every grade from a pass rate per (model, case, row), runs `decide_core` and follows its extra and gate runs as `latch.sh` does. Held pass rates come from `--assume` (uniform) or from the graded runs of `--held` in an earlier experiment (Beta(1,1) posterior mean); the candidate's are the held ones plus the gain, capped at 1. Gain 0 is the A/A case: its adopt share is the false adoption rate and its reject share the false rejection rate.

## factorial.py

```json
{"base": "cand_x", "n_variants": 16, "seed": 0, "reps": 1,
 "factors": [{"id": "F1", "anchor": "<text that occurs once in cand_x>", "levels": ["<the anchor>", ""]},
             {"id": "F2", "anchor": "<text>", "levels": ["<the anchor>", {"text": "<other wording>", "trace": {"R3": "<quote of R3 in it>"}}]}]}
```

`base` needs a `trace.tsv` column. The first level of a factor is its anchor; `""` removes it. An anchor overlapping a row's `trace.tsv` quote carries that row: it cannot be removed, and each other level gives the row's quote. `factorial.py EXP make --out EXP2` writes a new experiment (exploration models only, the select cases only so that holdout stays out of candidate choice, `reps` from factors.json or the plan) whose versions `fac_NN` take each level equally often, in the random order closest to orthogonal out of 500; then `check_trace.py EXP2`, `run_trials.py EXP2 --probe`, `run_trials.py EXP2`, `grade.py EXP2` and `factorial.py EXP2 analyze`. The analysis gives each level's effect on the run score against the anchor (difference of means over versions, bootstrap interval over versions), marks a `""` level removable when the upper bound of its pass-rate drop is below `reject_drop` on every exploration model and row, and prints 2x2 interactions of two-level factors. A candidate built from the results goes through an ordinary round.

## Isolation and safety

- Claude runs with `--setting-sources project`, which loads neither the user-level `CLAUDE.md` nor user settings; MCP servers come only from an empty `--strict-mcp-config`. It still loads `CLAUDE.md` from every ancestor of the work directory, which is why the probe scans them. Its Bash runs inside the OS sandbox (`sandbox.enabled`, writes limited to the run's work directory, no network), and edits are pre-approved, so no run waits for a prompt. Authentication is the CLI's own and is not affected.
- Codex runs with `CODEX_HOME` set to a fresh directory under the run that holds only a link to the user's `auth.json` (removed after the run), so the user-level `AGENTS.md` and config are not read; `-s workspace-write` limits writes to the work directory and blocks network. Codex reads `AGENTS.md` from the git root down, so the experiment must not sit inside a git repository. No credential content is copied or printed.
- `run_trials.py --probe` first lists `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `AGENTS.override.md`, `.agents/`, `.git` and (outside the home directory) `.claude/` in the experiment directory and its ancestors, and stops if there is any; it then asks each real CLI, exploration and gate models, from a directory under `runs/`, whether any instruction file is loaded and stops unless it answers NONE. Re-run it whenever the machine's setup changes.
- The Sol judge runs with `--disable shell_tool --disable unified_exec`, so it cannot read the delivered document or the key next to its directory; the Sonnet judge runs with no tools.
- Each run has its own work directory, a per-call timeout (`timeout_sec`), and is recorded as unmeasured when it fails; one failure does not stop the others.
