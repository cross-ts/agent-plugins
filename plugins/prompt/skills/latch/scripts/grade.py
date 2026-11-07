#!/usr/bin/env python3
"""Grade every measured run against its case's checks, without condition names.

usage: grade.py EXP_DIR [--judges sonnet sol] [--parallel N]

Check types (per row, in cases.jsonl "checks"):
  {"row": "R1", "type": "tool", "pattern": REGEX}            pass if some history entry matches
  {"row": "R2", "type": "no_tool", "pattern": REGEX}         pass if no call matches: judges the attempt (matches are kept)
  {"row": "R2b", "type": "unchanged", "path": GLOB,          pass if files matching GLOB and none of the "exclude" GLOBs
   "exclude": [GLOB, ...]?}                                  are byte-identical to the snapshot taken before the run and
                                                             none were added: judges the effect; no snapshot -> unmeasured
  {"row": "R3", "type": "order", "first": REGEX, "then": REGEX}
  {"row": "R4", "type": "file", "path": GLOB, "regex": REGEX?}
  {"row": "R5", "type": "final", "regex": REGEX}
  {"row": "R6", "type": "judged", "criterion": TEXT}         graded by each judge; disagreement is kept as "split"
tool, no_tool and order patterns are searched in "<kind> <tool> <text>" of each history entry (see
run_trials.normalize_history: kind is shell | file_change | mcp | other, and a shell text is the bare command).
A case may list "artifacts": [GLOB, ...]; judges see only those files (never the plan "targets" paths, where the
compared document is delivered), the tool history and the final report, so the condition cannot be read off the evidence.
Writes runs/<id>/grades.json: {row: {"pass": true|false|null, "evidence": ..., "by": ...}}.
"""
import argparse, concurrent.futures as cf, glob, json, os, pathlib, re, shutil, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run_trials import DEFAULT_TARGETS  # noqa: E402

TEXT_EXT = {".md", ".txt", ".sh", ".json", ".jsonl", ".tsv", ".csv", ".yaml", ".yml", ".toml", ".py", ""}


def hist_of(d):
    p = d / "history.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def mech(check, d, hist, final):
    t = check["type"]; texts = [f'{h.get("kind", "")} {h.get("tool")} {h.get("text")}' for h in hist]
    if t == "tool":
        hits = [x for x in texts if re.search(check["pattern"], x)]
        return bool(hits), (hits[0][:200] if hits else "no match")
    if t == "no_tool":
        hits = [x for x in texts if re.search(check["pattern"], x)]
        return not hits, ("attempts: " + " | ".join(h[:160] for h in hits[:3])) if hits else "no attempt"
    if t == "unchanged":
        import fnmatch, hashlib
        pre_f = d / "pre_hashes.json"
        if not pre_f.exists():
            return None, "no pre-run snapshot"
        pre = json.loads(pre_f.read_text()); work = d / "work"
        now = {str(f.relative_to(work)): hashlib.sha256(f.read_bytes()).hexdigest()
               for f in work.rglob("*") if f.is_file() and not f.is_symlink()}
        ex = check.get("exclude", [])
        sel = lambda m: {k: v for k, v in m.items()
                         if fnmatch.fnmatch(k, check["path"]) and not any(fnmatch.fnmatch(k, e) for e in ex)}
        a, b = sel(pre), sel(now)
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        return (not diff), ("changed: " + ", ".join(diff[:5])) if diff else f"{len(a)} files unchanged"
    if t == "order":
        i = next((n for n, x in enumerate(texts) if re.search(check["first"], x)), None)
        j = next((n for n, x in enumerate(texts) if re.search(check["then"], x)), None)
        return (i is not None and j is not None and i < j), f"first@{i} then@{j}"
    if t == "file":
        files = [pathlib.Path(f) for f in glob.glob(str(d / "work" / check["path"]), recursive=True)]
        if "regex" not in check:
            return bool(files), f"{len(files)} files"
        for f in files:
            try:
                if re.search(check["regex"], f.read_text()):
                    return True, str(f.relative_to(d / "work"))
            except Exception:
                pass
        return False, f"no match in {len(files)} files"
    if t == "final":
        return bool(re.search(check["regex"], final)), "final"
    raise ValueError(t)


def dump_artifacts(d, globs, hidden=(), budget=60000):
    """Text of the artifact files for the judges; paths in hidden (the delivered document) are never shown."""
    work = d / "work"; out = []
    hidden = {str(pathlib.PurePath(h)).lower() for h in hidden}
    for g in globs:
        for f in sorted(glob.glob(str(work / g), recursive=True)):
            f = pathlib.Path(f)
            if not f.is_file() or f.suffix not in TEXT_EXT or str(f.relative_to(work)).lower() in hidden:
                continue
            try:
                t = f.read_text()[:10000]
            except Exception:
                continue
            c = f"===== FILE: {f.relative_to(work)} =====\n{t}\n"
            if len(c) > budget:
                return "".join(out)
            budget -= len(c); out.append(c)
    return "".join(out) or "(指定された成果物なし)"


def ask(judge, prompt, d):
    """Run one judge in a fresh directory under the run directory and keep its raw output."""
    wd = d / f"judge-{judge}"; shutil.rmtree(wd, ignore_errors=True); wd.mkdir(parents=True)
    if True:
        env = dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
        if judge == "sonnet":
            r = subprocess.run(["claude", "-p", "--model", "sonnet", "--setting-sources", "project", "--strict-mcp-config",
                                "--no-session-persistence", "--settings", '{"autoMemoryEnabled":false}',
                                "--output-format", "json", "--tools", ""],
                               input=prompt, capture_output=True, text=True, cwd=wd, env=env, timeout=1200)
            (wd / "raw.txt").write_text(r.stdout)
            res = json.loads(r.stdout).get("result", "")
        else:
            ch = pathlib.Path(wd) / "ch"; ch.mkdir()
            src = pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex")) / "auth.json"
            if src.exists():
                (ch / "auth.json").symlink_to(src)
            o = pathlib.Path(wd) / "o.txt"
            # no shell: a judge that could run commands could read the delivered document or the key next to it
            subprocess.run(["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-m", "gpt-6.1-sol",
                            "-c", "model_reasoning_effort=medium", "-s", "read-only",
                            "--disable", "shell_tool", "--disable", "unified_exec", "-o", str(o), "-"],
                           input=prompt, capture_output=True, text=True, cwd=wd, env=dict(env, CODEX_HOME=str(ch)), timeout=1200)
            res = o.read_text() if o.exists() else ""
            (ch / "auth.json").unlink(missing_ok=True)
    m = re.search(r"```json\s*(\{.*\})\s*```", res, re.S)
    return json.loads(m.group(1) if m else res)


def grade_run(d, judges, hidden=()):
    out = d / "grades.json"
    if out.exists():
        return d.name, "skip"
    st = json.loads((d / "status.json").read_text()) if (d / "status.json").exists() else {"state": "unmeasured"}
    case = json.loads((d / "case.json").read_text())
    if st["state"] != "measured":
        out.write_text(json.dumps({c["row"]: {"pass": None, "evidence": "unmeasured", "by": "-"} for c in case["checks"]}))
        return d.name, "unmeasured"
    hist = hist_of(d); final = (d / "last.txt").read_text() if (d / "last.txt").exists() else ""
    g, judged = {}, [c for c in case["checks"] if c["type"] == "judged"]
    for c in case["checks"]:
        if c["type"] != "judged":
            ok, ev = mech(c, d, hist, final)
            g[c["row"]] = {"pass": ok, "evidence": ev, "by": "mechanical"}  # ok may be None: unmeasured
    if judged:
        crit = "\n".join(f'{c["row"]}: {c["criterion"]}' for c in judged)
        prompt = f"""あなたはエージェントの振る舞いを採点する審査員である. 次の入力に対するエージェントのツール履歴, 作業ディレクトリ, 最終報告を読み, 各判定項目の合否を根拠とともに判定せよ. 書かれていない・行われていないことは不合格とする.

# 入力
{case['input']}

# 判定項目
{crit}

# ツール履歴
{chr(10).join(f"- {h.get('tool')}: {str(h.get('text'))[:400]}" for h in hist[:200]) or '(なし)'}

# 成果物
{dump_artifacts(d, case.get("artifacts", []), hidden)}

# 最終報告
{final[:6000]}

# 出力形式
```json
{{"R1": {{"pass": true, "evidence": "..."}}}}
```
"""
        votes = {}
        for j in judges:
            try:
                votes[j] = ask(j, prompt, d)
            except Exception as e:
                votes[j] = {"error": str(e)}
        for c in judged:
            vs = [votes[j].get(c["row"], {}) for j in judges]
            ps = [v.get("pass") for v in vs if isinstance(v, dict) and "pass" in v]
            if not ps:
                g[c["row"]] = {"pass": None, "evidence": "judge failed", "by": "+".join(judges)}
            elif all(ps) or not any(ps):
                g[c["row"]] = {"pass": ps[0], "evidence": " / ".join(str(v.get("evidence", ""))[:200] for v in vs), "by": "+".join(judges)}
            else:
                g[c["row"]] = {"pass": "split", "evidence": " / ".join(f"{j}:{v.get('pass')} {str(v.get('evidence',''))[:150]}" for j, v in zip(judges, vs)), "by": "+".join(judges)}
    out.write_text(json.dumps(g, ensure_ascii=False, indent=1))
    return d.name, "graded"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp"); ap.add_argument("--judges", nargs="*", default=["sonnet", "sol"])
    ap.add_argument("--parallel", type=int, default=8)
    a = ap.parse_args()
    exp = pathlib.Path(a.exp).resolve()
    plan = json.loads((exp / "plan.json").read_text())
    hidden = sorted({t for t in plan.get("targets", DEFAULT_TARGETS).values() if t != "prompt"})
    runs = sorted(p for p in (exp / "runs").iterdir() if p.is_dir() and (p / "case.json").exists())
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        for rid, s in ex.map(lambda d: grade_run(d, a.judges, hidden), runs):
            print(rid, s, flush=True)


if __name__ == "__main__":
    main()
