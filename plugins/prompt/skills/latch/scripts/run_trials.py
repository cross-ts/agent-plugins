#!/usr/bin/env python3
"""Run every condition x model x case x rep in an isolated, fresh session, in parallel.

usage: run_trials.py EXP_DIR [--extra COND:MODEL:CASE:N ...] [--gate COND ...] [--probe] [--dry-run]

Reads EXP_DIR/plan.json, EXP_DIR/variants/*.md, EXP_DIR/cases.jsonl.
Writes EXP_DIR/runs/<anon-id>/ (prompt.txt, work/, history.jsonl, last.txt, status.json)
and the unblinding key to EXP_DIR/runs/.key.json (graders read runs/<id>/ only).
The exploration models (plan "models") run every condition on every case up to "reps".
The gate models (plan "gate.models") run only with --gate, only for the named conditions and only on the
cases whose split is in "gate.splits", up to "gate.reps".
"""
import argparse, concurrent.futures as cf, json, os, pathlib, random, shlex, shutil, subprocess, sys, time

DEFAULT_MODELS = {
    "haiku": {"cli": "claude", "model": "haiku"},
    "luna": {"cli": "codex", "model": "gpt-6-luna", "effort": "high"},
}
DEFAULT_GATE_SPLITS = ["holdout", "regression"]


def load(exp):
    plan = json.loads((exp / "plan.json").read_text())
    conds = sorted(p.stem for p in (exp / "variants").glob("*.md") if not p.stem.lower().startswith("readme"))
    cases = [json.loads(l) for l in (exp / "cases.jsonl").read_text().splitlines() if l.strip()]
    models = {k: {**DEFAULT_MODELS.get(k, {}), **v} for k, v in plan.get("models", {k: {} for k in DEFAULT_MODELS}).items()}
    gate = gate_models(plan)
    clash = sorted(set(models) & set(gate))
    if clash:
        sys.exit(f"plan.json: gate model names must differ from exploration model names: {clash}")
    return plan, conds, cases, models, gate


def gate_models(plan):
    """Models that only check non-inferiority of finalists: the models that actually read the document."""
    return dict((plan.get("gate") or {}).get("models", {}))


def gate_splits(plan):
    return list((plan.get("gate") or {}).get("splits", DEFAULT_GATE_SPLITS))


def gate_reps(plan):
    return int((plan.get("gate") or {}).get("reps", plan.get("reps", 3)))


DEFAULT_TARGETS = {"claude": "CLAUDE.md", "codex": "AGENTS.md"}


def target_for(plan, cli):
    """Where the document goes for this CLI: a path in the work directory (the path the agent
    normally reads, e.g. CLAUDE.md / AGENTS.md / .claude/skills/x/SKILL.md) or "prompt"."""
    return plan.get("targets", DEFAULT_TARGETS).get(cli, "prompt")


def case_input(case, rep):
    """The input of a case for repetition rep (1-based) and its index: "inputs" (paraphrases with the same
    expected behaviour) rotates with rep, so runs with the same rep get the same input in every condition."""
    xs = case.get("inputs")
    if xs:
        i = (rep - 1) % len(xs)
        return xs[i], i
    return case["input"], None


def build_prompt(plan, cli, doc, text):
    parts = []
    if doc.strip() and target_for(plan, cli) == "prompt":
        parts.append(f'<instructions>\n{doc.strip()}\n</instructions>')
    parts.append(text.strip())
    return "\n\n".join(parts) + "\n"


CLAUDE_SETTINGS = json.dumps({"autoMemoryEnabled": False,
                              "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False}})


def cmd_for(m, work, d, effort_override=None):
    """Claude: project settings only, Bash allowed inside the OS sandbox (writes stay in the work
    directory, no network). Codex: a fresh CODEX_HOME holding only a link to auth.json, inside d."""
    if m["cli"] == "claude":
        return (["claude", "-p", "--model", m["model"], "--setting-sources", "project", "--strict-mcp-config",
                 "--no-session-persistence", "--settings", CLAUDE_SETTINGS,
                 "--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose"], None)
    ch = d / "codex-home"; ch.mkdir(parents=True, exist_ok=True)
    src = pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex")) / "auth.json"
    if src.exists():
        (ch / "auth.json").symlink_to(src)
    effort = effort_override or m.get("effort", "high")
    return (["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-m", m["model"],
             "-c", f"model_reasoning_effort={effort}", "-s", "workspace-write", "--json",
             "-o", str(d / "last.txt"), "-"], ch)


def trial_env(plan, work):
    """Variables every trial needs (plan "env_vars", "{work}" replaced by the run's work directory)
    and PATH with plan "path_prepend" (directories relative to the work directory) in front."""
    out = {k: str(v).replace("{work}", str(work)) for k, v in plan.get("env_vars", {}).items()}
    pre = [str(work / p) for p in plan.get("path_prepend", [])]
    if pre:
        out["PATH"] = os.pathsep.join(pre + [out.get("PATH", os.environ.get("PATH", ""))])
    return out


def apply_trial_env(cli, cmd, env, tenv, d):
    """Pass tenv to the CLI process and to every shell command it runs: Claude re-reads the login
    shell's PATH, so the variables also go through CLAUDE_ENV_FILE; Codex gets shell_environment_policy.set
    and, when PATH is set, a non-login shell with an empty ZDOTDIR."""
    if not tenv:
        return cmd
    env.update(tenv)
    if cli == "claude":
        f = d / "session-env.sh"
        f.write_text("".join(f"export {k}={shlex.quote(v)}\n" for k, v in tenv.items()))
        env["CLAUDE_ENV_FILE"] = str(f)
        return cmd
    tenv = dict(tenv)
    if "PATH" in tenv:  # the login profile and the user's .zshenv would rebuild PATH: use an empty ZDOTDIR
        zd = d / "zdotdir"; zd.mkdir(exist_ok=True)
        tenv.setdefault("ZDOTDIR", str(zd)); env["ZDOTDIR"] = tenv["ZDOTDIR"]
    sets = [x for k, v in tenv.items() for x in ("-c", f"shell_environment_policy.set.{k}={json.dumps(v)}")]
    if "PATH" in tenv:
        sets += ["-c", "allow_login_shell=false"]
    return cmd[:-1] + sets + cmd[-1:]


CLAUDE_FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def unwrap_shell(cmd):
    """Codex reports a command as "/bin/zsh -lc '<cmd>'": return <cmd>, so one pattern matches Claude's
    Bash command and Codex's command alike."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return cmd
    if len(parts) == 3 and parts[1] in ("-lc", "-c") and os.path.basename(parts[0]) in ("bash", "zsh", "sh"):
        return parts[2]
    return cmd


def claude_kind(name):
    if name == "Bash":
        return "shell"
    if name in CLAUDE_FILE_TOOLS:
        return "file_change"
    return "mcp" if str(name).startswith("mcp__") else "other"


def normalize_history(cli, raw):
    """Return ([{"kind", "tool", "text", "ok", "result"}], final) from claude stream-json or codex --json.
    "kind" is shell | file_change | mcp | other on both CLIs; for shell, "text" is the bare command on both.
    A Claude file_change keeps the path and the written text; a Codex file_change keeps only the paths.
    "ok" tells whether the call went through (tool_result without error / exit code 0), so an
    attempted call and a call that took effect can be judged separately."""
    hist, final, by_id = [], "", {}
    for line in raw.splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        if cli == "claude":
            for c in ((o.get("message") or {}).get("content") or []) if isinstance(o.get("message"), dict) else []:
                if not isinstance(c, dict):
                    continue
                if o.get("type") == "assistant" and c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    kind = claude_kind(c.get("name"))
                    text = inp.get("command", "") if kind == "shell" else json.dumps(inp, ensure_ascii=False)
                    h = {"kind": kind, "tool": c.get("name"), "text": text, "ok": None, "result": ""}
                    hist.append(h); by_id[c.get("id")] = h
                elif o.get("type") == "user" and c.get("type") == "tool_result" and c.get("tool_use_id") in by_id:
                    body = c.get("content"); body = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                    by_id[c["tool_use_id"]].update(ok=not c.get("is_error", False), result=body[:500])
            if o.get("type") == "result":
                final = o.get("result") or ""
        else:
            it = o.get("item") or {}
            if o.get("type") == "item.completed" and isinstance(it, dict):
                t = it.get("type")
                if t == "command_execution":
                    hist.append({"kind": "shell", "tool": "shell", "text": unwrap_shell(it.get("command", "")),
                                 "ok": it.get("exit_code") == 0, "result": str(it.get("aggregated_output", ""))[:500]})
                elif t == "file_change":
                    hist.append({"kind": "file_change", "tool": "file_change", "text": json.dumps(it.get("changes"), ensure_ascii=False),
                                 "ok": it.get("status") != "failed", "result": ""})
                elif t == "mcp_tool_call":
                    hist.append({"kind": "mcp", "tool": it.get("tool", "mcp"), "text": json.dumps(it.get("arguments"), ensure_ascii=False),
                                 "ok": it.get("status") != "failed", "result": ""})
    return hist, final


def usage_of(cli, raw):
    """Token, turn and cost figures of one session: claude's result event (input counts the cache reads and
    writes; turns and cost_usd come only from claude) or the sum of codex's turn.completed usage."""
    u = {}
    for line in raw.splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        us = o.get("usage") or {}
        if cli == "claude" and o.get("type") == "result":
            u = {"tokens_in": sum(int(us.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
                 "tokens_out": int(us.get("output_tokens") or 0), "turns": o.get("num_turns"), "cost_usd": o.get("total_cost_usd")}
        elif cli == "codex" and o.get("type") == "turn.completed":
            u["tokens_in"] = u.get("tokens_in", 0) + int(us.get("input_tokens") or 0)
            u["tokens_out"] = u.get("tokens_out", 0) + int(us.get("output_tokens") or 0)
    return u


def tree_hashes(root):
    """sha256 of every file under root, so a prohibited effect can be checked as a state diff."""
    import hashlib
    out = {}
    for f in pathlib.Path(root).rglob("*"):
        if f.is_file() and not f.is_symlink():
            out[str(f.relative_to(root))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def run_one(exp, plan, rid, job, models, dry):
    d = exp / "runs" / rid
    m = models[job["model"]]
    work = d / "work"
    cmd, ch = cmd_for(m, work, d)
    env = dict(os.environ)
    if ch:
        env["CODEX_HOME"] = str(ch)
    cmd = apply_trial_env(m["cli"], cmd, env, trial_env(plan, work), d)
    if dry:
        return rid, {"state": "dry-run", "cmd": cmd}
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=(d / "prompt.txt").read_text(), capture_output=True, text=True,
                           cwd=work, env=env, timeout=plan.get("timeout_sec", 1200))
        rc, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = "timeout", (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"
    finally:
        if ch and (ch / "auth.json").is_symlink():
            (ch / "auth.json").unlink()
    (d / "raw.jsonl").write_text(out)
    (d / "stderr.txt").write_text(err[-20000:])
    hist, final = normalize_history(m["cli"], out)
    (d / "history.jsonl").write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in hist))
    if m["cli"] == "claude":
        (d / "last.txt").write_text(final)
    measured = rc == 0 and (d / "last.txt").exists() and (d / "last.txt").read_text().strip() != ""
    st = {"state": "measured" if measured else "unmeasured", "rc": rc, "sec": round(time.time() - t0),
          "tool_calls": len(hist), **usage_of(m["cli"], out)}
    (d / "status.json").write_text(json.dumps(st))
    return rid, st


INSTRUCTION_FILES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENTS.override.md")
INSTRUCTION_DIRS = (".agents", ".claude", ".git")


def ancestor_leaks(start):
    """Instruction files, skill or setting directories and git roots in start and its ancestors: Claude Code
    loads CLAUDE.md from every ancestor even with --setting-sources project, and Codex reads AGENTS.md from
    the git root down. The home directory's .claude is the user-level configuration, which
    --setting-sources and the separate CODEX_HOME keep out (the model probe checks that)."""
    home = pathlib.Path.home().resolve()
    start = pathlib.Path(start).resolve()
    found = []
    for p in [start, *start.parents]:
        found += [str(p / n) for n in INSTRUCTION_FILES if (p / n).exists()]
        found += [str(p / n) for n in INSTRUCTION_DIRS if (p / n).exists() and not (n == ".claude" and p == home)]
    return found


PROBE = "読み込まれている指示ファイル(CLAUDE.md, AGENTS.md など)があれば, その最初の2行をそのまま引用せよ. なければ NONE とだけ答えよ."


def probe(exp, plan, models):
    leaks = ancestor_leaks(exp / "runs")
    if leaks:
        print("probe: instruction files or git roots above the experiment directory would load into every run;"
              " move the experiment directory where none of these is an ancestor:")
        for x in leaks:
            print("  " + x)
        return 1
    print("probe ancestors: OK")
    bad = 0
    for name, m in models.items():
        d = exp / "runs" / f".probe-{name}"; shutil.rmtree(d, ignore_errors=True); (d / "work").mkdir(parents=True)
        (d / "prompt.txt").write_text(PROBE)
        cmd, ch = cmd_for(m, d / "work", d, effort_override="low")
        env = dict(os.environ, **({"CODEX_HOME": str(ch)} if ch else {}))
        r = subprocess.run(cmd, input=PROBE, capture_output=True, text=True, cwd=d / "work", env=env, timeout=600)
        (d / "raw.jsonl").write_text(r.stdout)
        if ch and (ch / "auth.json").is_symlink():
            (ch / "auth.json").unlink()
        _, final = normalize_history(m["cli"], r.stdout)
        if m["cli"] == "codex" and (d / "last.txt").exists():
            final = (d / "last.txt").read_text()
        ok = final.strip().upper().startswith("NONE")
        bad += not ok
        print(f"probe {name}: {'OK' if ok else 'LEAK? ' + final.strip()[:200]}")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp")
    ap.add_argument("--extra", nargs="*", default=[], help="COND:MODEL:CASE:N additional reps (MODEL may be a gate model)")
    ap.add_argument("--gate", nargs="*", default=[], metavar="COND", help="run the gate models on the gate splits for these conditions")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true", help="check that no instruction file leaks in: the ancestors, then the real CLIs")
    a = ap.parse_args()
    exp = pathlib.Path(a.exp).resolve()
    plan, conds, cases, models, gate = load(exp)
    every = {**models, **gate}
    if a.probe:
        (exp / "runs").mkdir(exist_ok=True)
        sys.exit(probe(exp, plan, every))
    runs = exp / "runs"; runs.mkdir(exist_ok=True)
    keyf = runs / ".key.json"
    key = json.loads(keyf.read_text()) if keyf.exists() else {}
    have = {}
    for rid, j in key.items():
        have.setdefault((j["cond"], j["model"], j["case"]), []).append(j["rep"])
    want = []
    reps = int(plan.get("reps", 3))
    for c in conds:
        for mname in models:
            for k in cases:
                n_have = len(have.get((c, mname, k["id"]), []))
                want += [(c, mname, k, r) for r in range(n_have + 1, reps + 1)]
    if a.gate:
        if not gate:
            sys.exit("--gate: plan.json has no gate.models")
        unknown = sorted(set(a.gate) - set(conds))
        if unknown:
            sys.exit(f"--gate: no such condition in variants/: {unknown}")
        g_reps, g_splits = gate_reps(plan), gate_splits(plan)
        for c in a.gate:
            for mname in gate:
                for k in cases:
                    if k.get("split", "select") in g_splits:
                        n_have = len(have.get((c, mname, k["id"]), []))
                        want += [(c, mname, k, r) for r in range(n_have + 1, g_reps + 1)]
    extra_cap = int(plan.get("max_extra_reps", 3))
    for spec in a.extra:
        c, mname, kid, n = spec.split(":")
        if mname not in every:
            sys.exit(f"--extra {spec}: no such model in plan.json")
        k = next(x for x in cases if x["id"] == kid)
        n_have = len(have.get((c, mname, kid), []))
        cap = (gate_reps(plan) if mname in gate else reps) + extra_cap
        if n_have + int(n) > cap:
            sys.exit(f"--extra {spec}: {n_have}+{n} runs exceeds reps+max_extra_reps={cap}")
        want += [(c, mname, k, n_have + i) for i in range(1, int(n) + 1)]
    rng = random.Random()
    jobs = {}
    for c, mname, k, rep in want:
        rid = "%010x" % rng.getrandbits(40)
        d = runs / rid; (d / "work").mkdir(parents=True)
        fx = exp / plan.get("env", "env")  # the trial environment inside the experiment directory
        if fx.exists():
            shutil.copytree(fx, d / "work", dirs_exist_ok=True)
        doc = (exp / "variants" / f"{c}.md").read_text()
        cli = every[mname]["cli"]; tgt = target_for(plan, cli)
        if doc.strip() and tgt != "prompt":
            tp = d / "work" / tgt; tp.parent.mkdir(parents=True, exist_ok=True); tp.write_text(doc)
        text, idx = case_input(k, rep)
        (d / "prompt.txt").write_text(build_prompt(plan, cli, doc, text))
        (d / "pre_hashes.json").write_text(json.dumps(tree_hashes(d / "work")))
        (d / "case.json").write_text(json.dumps({**{x: v for x, v in k.items() if x != "inputs"}, "input": text}, ensure_ascii=False))
        key[rid] = {"cond": c, "model": mname, "case": k["id"], "split": k.get("split", "select"), "rep": rep}
        if mname in gate:
            key[rid]["gate"] = True
        if idx is not None:
            key[rid]["input_idx"] = idx
        jobs[rid] = key[rid]
    keyf.write_text(json.dumps(key, ensure_ascii=False, indent=1))
    order = list(jobs); rng.shuffle(order)
    par = int(plan.get("parallel", 8))
    print(f"{len(order)} runs, parallel={par}", flush=True)
    with cf.ThreadPoolExecutor(par) as ex:
        futs = [ex.submit(run_one, exp, plan, rid, jobs[rid], every, a.dry_run) for rid in order]
        for f in cf.as_completed(futs):
            rid, st = f.result()
            print(rid, st.get("state"), st.get("rc", ""), st.get("sec", ""), flush=True)


if __name__ == "__main__":
    main()
