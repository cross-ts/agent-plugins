#!/usr/bin/env python3
"""Unblind graded runs and apply the latch rule against the held condition.

usage: decide.py EXP_DIR --held COND [--next]

Estimate. For a candidate, a model and a row, every registered case that checks the row gives the
difference of pass rates (candidate - held); the estimate is the mean over those cases, and its standard
error comes from each case's binomial variance (computed with 0.5 successes and 1 trial added, so all-pass
and all-fail cases keep a variance) summed and divided by the squared number of cases. Bounds are one-sided
at tolerance.confidence (normal quantile). The cases are fixed: generalisation is what holdout checks.

Rows. drop = held - candidate.
  reject      lower bound of the drop >= reject_drop
  ok          upper bound of the drop <  reject_drop          (non-inferior)
  hold        in between: extra runs on every case of the row, held and candidate brought to the same count
  unresolved  in between at reps + max_extra_reps on those cases: the held version stays
  A row needs `reps` valid grades on both sides of every case that checks it, otherwise it holds.
  Judge splits on non-critical rows are left out of the pass rates.
Critical rows (p.tsv 重大 = yes). When the held version never violated the row on that model, one violation
  by the candidate rejects it. When the held version also violated it, the row follows the rule above with
  reject_drop = 0. A judge split on a critical row holds. Zero violations print the rule-of-three bound 3/n.
Changed rows (plan "changed_rows": {cand: [row]}) leave the comparison and must pass at
  tolerance.changed_min_pass in every case on their own.
Improvement. The same paired estimate on run scores (weighted mean of the rows, p.tsv 重み): adopt needs a
  select gain >= min_gain on some model and a holdout gain >= min_gain on every such model. A candidate whose
  select upper bound is below min_gain on every model and that is not shorter gets no more runs.
Shorter. A candidate non-inferior on every row whose document has at most (1 - min_shrink) of the held
  version's characters is adopted without a gain.
Gate. With plan "gate", a candidate that passes the exploration models is "gate" until the gate models have
  run it and the held version on the gate splits; the gate models then apply the row rules only (no gain).
A/A. The copy of the held version (variants/baseline_aa.md for baseline_original, baseline_aa_<cond>.md for a
  held candidate) is judged like a candidate and never adopted; if it would be adopted every candidate keeps the
  held version, if it is rejected a warning is printed.
Conditions named baseline_* are controls and are never adopted. A candidate listed in plan
"diff_candidates" needs registered "regression" cases.
--next prints the run_trials.py arguments for the runs the decision still needs (nothing when none).
"""
import argparse, csv, json, pathlib, statistics as st, sys
from collections import defaultdict
from statistics import NormalDist

sys.dont_write_bytecode = True  # importing a sibling script must not write __pycache__ into the skill directory
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run_trials import DEFAULT_MODELS, gate_models, gate_reps, gate_splits  # noqa: E402

AA = "baseline_aa"
# chosen with simulate.py (9 cases x 3 of 5 rows, 2 models, reps 3, cap 6, min_gain 0.05, 200 trials, 2 seeds):
# at held pass rates 0.6-0.95 / 0.4-0.8, A/A false rejection about 1-4% / 6-7% and false adoption at most 1%;
# a +0.15 gain is adopted about 64-70% / 36-44% of the time
DEFAULT_REJECT_DROP = 0.2
DEFAULT_CONFIDENCE = 0.95
EPS = 1e-9
RANK = {"ok": 0, "hold": 1, "unresolved": 2, "reject": 3}


def aa_name(held):
    """The A/A control of a held condition: baseline_aa copies baseline_original, baseline_aa_<cond> copies <cond>."""
    return AA if held == "baseline_original" else f"{AA}_{held}"


def is_yes(x):
    return str(x or "").strip().lower() in {"yes", "y", "true", "1", "はい"}


def load(exp):
    """Everything decide_core needs, read from EXP_DIR (runs whose grades are all null are left out)."""
    plan = json.loads((exp / "plan.json").read_text())
    crit, weight = set(), {}
    with open(exp / "p.tsv") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if is_yes(r.get("重大")):
                crit.add(r["id"])
            try:
                weight[r["id"]] = float(r.get("重み") or 1)
            except ValueError:
                weight[r["id"]] = 1.0
    reg = [json.loads(l) for l in (exp / "cases.jsonl").read_text().splitlines() if l.strip()]
    key = json.loads((exp / "runs" / ".key.json").read_text())
    total, runs, stats = defaultdict(int), defaultdict(list), defaultdict(list)
    for rid, k in key.items():
        cell = (k["cond"], k["model"], k["case"]); total[cell] += 1
        d = exp / "runs" / rid
        if (d / "status.json").exists():
            stats[cell].append(json.loads((d / "status.json").read_text()))
        if not (d / "grades.json").exists():
            continue
        g = json.loads((d / "grades.json").read_text())
        if all(v.get("pass") is None for v in g.values()):
            continue
        runs[cell].append({"rid": rid, "g": g})
    variants = {p.stem: p for p in (exp / "variants").glob("*.md") if not p.stem.lower().startswith("readme")}
    return {"plan": plan, "crit": crit, "weight": weight,
            "case_rows": {k["id"]: sorted({c["row"] for c in k.get("checks", [])}) for k in reg},
            "split_of": {k["id"]: k.get("split", "select") for k in reg},
            "runs": runs, "total": total, "stats": stats,
            "conds": sorted(variants) or sorted({c for c, _, _ in total}),
            "lengths": {c: len(p.read_text()) for c, p in variants.items()}}


def smoothed_var(xs):
    """Variance of the mean of values in [0, 1], as a binomial with 0.5 successes and 1 trial added."""
    p = (sum(xs) + 0.5) / (len(xs) + 1)
    return p * (1 - p) / len(xs)


def decide_core(data, held):
    """The latch decision as data: {"held", "candidates", "calibration", "warnings", "next"} (no I/O)."""
    plan, crit, weight = data["plan"], data["crit"], data["weight"]
    case_rows, split_of, runs, total = data["case_rows"], data["split_of"], data["runs"], data["total"]
    lengths = data.get("lengths", {})
    tol = plan.get("tolerance", {})
    reject_drop = float(tol.get("reject_drop", DEFAULT_REJECT_DROP)); min_gain = float(tol.get("min_gain", 0.05))
    conf = float(tol.get("confidence", DEFAULT_CONFIDENCE)); z = NormalDist().inv_cdf(conf)
    min_shrink = float(tol.get("min_shrink", 0.1)); changed_min = float(tol.get("changed_min_pass", 1.0))
    reps = int(plan.get("reps", 3)); extra_max = int(plan.get("max_extra_reps", 3))
    explore = list(plan.get("models") or DEFAULT_MODELS); gate = list(gate_models(plan))
    g_reps, g_splits = gate_reps(plan), set(gate_splits(plan))
    changed = {k: set(v) for k, v in plan.get("changed_rows", {}).items()}
    cases = sorted(case_rows)
    sel = [k for k in cases if split_of[k] == "select"]
    hold_cases = [k for k in cases if split_of[k] == "holdout"]
    gate_cases = [k for k in cases if split_of[k] in g_splits]

    def base(m):
        return g_reps if m in gate else reps

    def cap(m):
        return base(m) + extra_max

    def valid(cell, row):
        return [r["g"][row]["pass"] for r in runs.get(cell, []) if row in r["g"] and isinstance(r["g"][row].get("pass"), bool)]

    def score(r):
        xs = [(weight.get(k, 1.0), 1.0 if v["pass"] else 0.0) for k, v in r["g"].items() if isinstance(v.get("pass"), bool)]
        return sum(w * x for w, x in xs) / sum(w for w, _ in xs) if xs else None

    def scores(cell):
        return [s for s in map(score, runs.get(cell, [])) if s is not None]

    def paired(c, m, ks, values):
        """Mean over the cases of mean(values(c)) - mean(values(held)), its standard error, number of cases."""
        ds, vs = [], []
        for k in ks:
            a, b = values((c, m, k)), values((held, m, k))
            if a and b:
                ds.append(st.mean(a) - st.mean(b)); vs.append(smoothed_var(a) + smoothed_var(b))
        if not ds:
            return None, None, 0
        return st.mean(ds), sum(vs) ** 0.5 / len(ds), len(ds)

    def check_rows(c, models, ks_all, why, cells):
        """Row rules for c against held on the given models and cases: the worst state, reasons, cells to extend."""
        state = "ok"

        def worse(s):
            nonlocal state
            state = s if RANK[s] > RANK[state] else state

        for m in models:
            ks_m = []
            for k in ks_all:
                if not runs.get((held, m, k)) or not runs.get((c, m, k)):
                    why.append(f"hold: unmeasured cell {m}/{k}"); cells.add((m, k)); worse("hold")
                else:
                    ks_m.append(k)
            for row in sorted({r for k in ks_m for r in case_rows[k]}):
                ks = [k for k in ks_m if row in case_rows[k]]
                if row in changed.get(c, set()):
                    for k in ks:
                        C = valid((c, m, k), row)
                        if len(C) < base(m):
                            why.append(f"hold: changed row {m}/{k}/{row} has {len(C)} valid grades < {base(m)}"); cells.add((m, k)); worse("hold")
                        elif st.mean(C) < changed_min - EPS:
                            if total[(c, m, k)] < cap(m):
                                why.append(f"hold: changed row {m}/{k}/{row} passes {st.mean(C):.2f} < {changed_min}"); cells.add((m, k)); worse("hold")
                            else:
                                why.append(f"REJECT changed row {m}/{k}/{row} passes {st.mean(C):.2f} < {changed_min} at cap"); worse("reject")
                    continue
                margin = reject_drop
                if row in crit:
                    cv = [r["g"][row].get("pass") for k in ks for r in runs[(c, m, k)] if row in r["g"]]
                    held_violated = any(x is False for k in ks for x in valid((held, m, k), row))
                    if "split" in cv:
                        why.append(f"hold: judge split on critical {row} {m} (confirm the evidence)"); cells.update((m, k) for k in ks); worse("hold")
                        continue
                    if not held_violated:
                        if False in cv:
                            ev = next(r["g"][row].get("evidence", "") for k in ks for r in runs[(c, m, k)]
                                      if row in r["g"] and r["g"][row].get("pass") is False)
                            why.append(f"REJECT veto {row} on {m}: {str(ev)[:160]}"); worse("reject")
                            continue
                        n = sum(1 for x in cv if x is True)
                        if n:
                            why.append(f"critical {row} on {m}: 0/{n} violations (95% upper bound ~{min(1.0, 3 / n):.2f})")
                        continue
                    margin = 0.0
                short = [k for k in ks if len(valid((held, m, k), row)) < base(m) or len(valid((c, m, k), row)) < base(m)]
                if short:
                    why.append(f"hold: {m}/{row} has fewer than {base(m)} valid grades on {short}"); cells.update((m, k) for k in short); worse("hold")
                    continue
                d, se, n_k = paired(c, m, ks, lambda cell: [1.0 if x else 0.0 for x in valid(cell, row)])
                drop, lo, hi = -d, -d - z * se, -d + z * se
                band = f"drop {drop:+.2f} [{lo:+.2f}, {hi:+.2f}] over {n_k} cases, margin {margin}"
                if lo >= margin - EPS:
                    why.append(f"REJECT regress {m}/{row}: {band}"); worse("reject")
                elif hi < margin - EPS:
                    continue
                elif all(max(total[(held, m, k)], total[(c, m, k)]) >= cap(m) for k in ks):
                    why.append(f"unresolved at cap {m}/{row}: {band}"); worse("unresolved")
                else:
                    why.append(f"hold: {m}/{row}: {band}"); cells.update((m, k) for k in ks); worse("hold")
        return state

    def extras(c, cells):
        """Runs on each cell: an unequal pair is first brought to the larger count, an equal pair gets 3 more
        each, never beyond the cap."""
        out = []
        for m, k in sorted(cells):
            th, tc = total[(held, m, k)], total[(c, m, k)]
            target = min(cap(m), max(th, tc) if th != tc else th + 3)
            out += [(x, m, k, target - t) for x, t in ((held, th), (c, tc)) if target - t > 0]
        return out

    def judge(c, allow_gate=True):
        why, cells = [], set()
        if c in plan.get("diff_candidates", []) and not any(s == "regression" for s in split_of.values()):
            return {"verdict": "reject", "why": ["REJECT: diff candidate without registered regression cases"],
                    "improvement": {}, "extra": [], "gate": False}
        state = check_rows(c, explore, cases, why, cells)
        imp = {}
        for m in explore:
            g, se, _ = paired(c, m, sel, scores)
            gh, _, _ = paired(c, m, hold_cases, scores)
            imp[m] = {"select_diff": g, "select_hi": None if g is None else g + z * se, "holdout_diff": gh}
        shorter = c in lengths and held in lengths and lengths[c] <= (1 - min_shrink) * lengths[held] + EPS
        hopeful = shorter or any(v["select_hi"] is not None and v["select_hi"] >= min_gain - EPS for v in imp.values())
        out = {"verdict": None, "why": why, "improvement": imp, "extra": [], "gate": False}
        if state == "reject":
            out["verdict"] = "reject"; return out
        if state == "unresolved":
            out["verdict"] = "keep-held"; why.append("non-inferiority not established at the cap: the held version stays"); return out
        if state == "hold":
            if not hopeful:
                out["verdict"] = "keep-held"
                why.append(f"select upper bound below min_gain={min_gain} on every model and not shorter: no more runs")
                return out
            out["extra"] = extras(c, cells)
            out["verdict"] = "hold" if out["extra"] else "keep-held"
            if not out["extra"]:
                why.append("cap reached with incomplete comparisons: the held version stays")
            return out
        up = [m for m, v in imp.items() if v["select_diff"] is not None and v["select_diff"] >= min_gain - EPS]
        if up and all(imp[m]["holdout_diff"] is not None and imp[m]["holdout_diff"] >= min_gain - EPS for m in up):
            basis = f"improved on {up}; holdout gain >= {min_gain}; non-inferior on every row"
        elif shorter:
            basis = f"non-inferior and shorter ({lengths[c]} vs {lengths[held]} chars, min_shrink {min_shrink})"
        else:
            out["verdict"] = "keep-held"
            why.append(f"holdout gain below min_gain={min_gain} on an improved model" if up
                       else f"no model improved on select by >= min_gain={min_gain} and not shorter")
            return out
        why.append(basis)
        if not gate or not allow_gate:
            out["verdict"] = "adopt"; return out
        if not gate_cases:
            out["verdict"] = "keep-held"; why.append(f"gate registered but no case is in gate.splits {sorted(g_splits)}"); return out
        if any(total[(x, m, k)] < g_reps for x in (held, c) for m in gate for k in gate_cases):
            out["verdict"] = "gate"; out["gate"] = True; why.append(f"waiting for the gate models {gate}"); return out
        gcells = set()
        gstate = check_rows(c, gate, gate_cases, why, gcells)
        if gstate == "reject":
            out["verdict"] = "reject"
        elif gstate == "unresolved":
            out["verdict"] = "keep-held"; why.append("gate: non-inferiority not established at the cap: the held version stays")
        elif gstate == "hold":
            out["extra"] = extras(c, gcells)
            out["verdict"] = "hold" if out["extra"] else "keep-held"
        else:
            out["verdict"] = "adopt"; why.append(f"non-inferior on the gate models {gate}")
        return out

    report = {"held": held, "candidates": {}, "calibration": None, "warnings": [],
              "settings": {"reps": reps, "cap": reps + extra_max, "reject_drop": reject_drop, "min_gain": min_gain,
                           "confidence": conf, "min_shrink": min_shrink, "critical": sorted(crit),
                           "explore": explore, "gate": gate}}
    for c in data["conds"]:
        if c == held or c.startswith("baseline"):
            continue
        report["candidates"][c] = judge(c)
    aa = aa_name(held)
    if aa in data["conds"]:
        cal = judge(aa, allow_gate=False)
        report["calibration"] = cal
        if cal["verdict"] == "adopt":
            report["warnings"].append("A/A control would be adopted: the rule is not calibrated; every candidate keeps the held version")
            for r in report["candidates"].values():
                if r["verdict"] in ("adopt", "gate"):
                    r["verdict"] = "keep-held"; r["gate"] = False; r["why"].append("A/A control would be adopted: the held version stays")
        elif cal["verdict"] == "reject":
            report["warnings"].append("A/A control was rejected: rejections in this round may be false ones")
    need = {}
    for r in [*report["candidates"].values(), *([report["calibration"]] if report["calibration"] else [])]:
        for x, m, k, n in r["extra"]:
            need[(x, m, k)] = max(need.get((x, m, k), 0), n)
    gate_conds = sorted(c for c, r in report["candidates"].items() if r["gate"])
    report["next"] = {"extra": [f"{x}:{m}:{k}:{n}" for (x, m, k), n in sorted(need.items())],
                      "gate": [held, *gate_conds] if gate_conds else []}
    for r in [*report["candidates"].values(), *([report["calibration"]] if report["calibration"] else [])]:
        r["extra"] = [f"{x}:{m}:{k}:{n}" for x, m, k, n in r["extra"]]
    return report


def next_args(report):
    nx = report["next"]
    return (["--extra", *nx["extra"]] if nx["extra"] else []) + (["--gate", *nx["gate"]] if nx["gate"] else [])


def summary(data, held):
    """Mean scores, costs and the case/rep variance split, for the printed report."""
    plan, runs, stats, lengths = data["plan"], data["runs"], data["stats"], data.get("lengths", {})
    weight, split_of = data["weight"], data["split_of"]
    explore = list(plan.get("models") or DEFAULT_MODELS)
    models = explore + list(gate_models(plan))
    reps = int(plan.get("reps", 3))

    def score(r):
        xs = [(weight.get(k, 1.0), 1.0 if v["pass"] else 0.0) for k, v in r["g"].items() if isinstance(v.get("pass"), bool)]
        return sum(w * x for w, x in xs) / sum(w for w, _ in xs) if xs else None

    lines = ["mean score per condition / model / split (valid grades only)"]
    for c in data["conds"]:
        cols = []
        for m in models:
            for sp in ("select", "holdout", "regression"):
                xs = [s for (x, mm, k), rs in runs.items() if x == c and mm == m and split_of.get(k) == sp for s in map(score, rs) if s is not None]
                if xs or (sp == "select" and m in explore):
                    cols.append(f"{m}/{sp}=" + (f"{st.mean(xs):.3f}(n{len(xs)})" if xs else "-"))
        lines.append(f"  {c:24s} " + "  ".join(cols))
    lines.append("\ncost per condition / model: document chars, median tokens in/out, tool calls, seconds")
    for c in data["conds"]:
        for m in models:
            ss = [s for (x, mm, _), v in stats.items() if x == c and mm == m for s in v if s.get("state") == "measured"]
            if not ss:
                continue
            med = lambda f: (lambda xs: f"{st.median(xs):.0f}" if xs else "-")([s[f] for s in ss if isinstance(s.get(f), (int, float))])
            lines.append(f"  {c:24s} {m:10s} chars={lengths.get(c, '-')} in={med('tokens_in')} out={med('tokens_out')}"
                         f" tools={med('tool_calls')} sec={med('sec')} (n{len(ss)})")
    var_lines = []
    for m in models:
        by_case = {k: [s for s in map(score, rs) if s is not None] for (x, mm, k), rs in runs.items() if x == held and mm == m}
        by_case = {k: v for k, v in by_case.items() if len(v) >= 2}
        if len(by_case) < 2:
            continue
        within = st.mean(st.variance(v) for v in by_case.values())
        n_bar = st.mean(len(v) for v in by_case.values())
        between = max(0.0, st.variance([st.mean(v) for v in by_case.values()]) - within / n_bar)
        hint = "more cases help more than more reps" if between >= within / reps else "more reps help as much as more cases"
        var_lines.append(f"  {m}: between={between:.3f} within={within:.3f} ({len(by_case)} cases): {hint}")
    if var_lines:
        lines += [f"\nvariance of run scores on {held}: between cases vs within a case", *var_lines]
    return "\n".join(lines)


def print_report(data, report):
    s = report["settings"]
    print(f"held={report['held']} reps={s['reps']} cap={s['cap']} reject_drop={s['reject_drop']} min_gain={s['min_gain']}"
          f" confidence={s['confidence']} min_shrink={s['min_shrink']} critical={s['critical']}"
          f" explore={s['explore']} gate={s['gate']}")
    print(summary(data, report["held"]))
    f = lambda x: "-" if x is None else f"{x:+.3f}"
    items = list(report["candidates"].items()) + ([(f"{aa_name(report['held'])} (A/A control)", report["calibration"])] if report["calibration"] else [])
    for c, r in items:
        print(f"\n== {c}: {r['verdict'].upper()}")
        for m, v in r["improvement"].items():
            print(f"   {m}: select diff {f(v['select_diff'])} (upper bound {f(v['select_hi'])}), holdout diff {f(v['holdout_diff'])}")
        for w in r["why"][:15]:
            print("   -", w)
        if len(r["why"]) > 15:
            print(f"   - ... {len(r['why']) - 15} more in decision.json")
    for w in report["warnings"]:
        print("\nWARNING:", w)
    nx = next_args(report)
    if nx:
        print("\nnext: run_trials.py EXP " + " ".join(nx) + "  then grade.py and decide.py again")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("exp"); ap.add_argument("--held", required=True)
    ap.add_argument("--next", action="store_true", help="print only the run_trials.py arguments still needed")
    a = ap.parse_args()
    exp = pathlib.Path(a.exp).resolve()
    data = load(exp)
    report = decide_core(data, a.held)
    (exp / "decision.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    if a.next:
        nx = next_args(report)
        if nx:
            print(" ".join(nx))
        return
    print_report(data, report)


if __name__ == "__main__":
    main()
