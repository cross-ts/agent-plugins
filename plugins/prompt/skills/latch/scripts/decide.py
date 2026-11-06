#!/usr/bin/env python3
"""Unblind graded runs and apply the latch rule against the held condition.

usage: decide.py EXP_DIR --held COND

Per candidate (conditions named baseline_* are controls and are never adopted), against --held:
  veto     a critical row (p.tsv 重大 = yes) is graded false in any candidate run          -> reject
  regress  for a (model, case, row), held pass rate - candidate pass rate >= reject_drop   -> reject
  hold     a critical row has a judge split, a (model, case, row) has fewer than `reps` valid
           grades on either side, a (cond, model, case) cell is unmeasured, or a smaller drop
           is seen before both sides reach reps + max_extra_reps                          -> add runs
           to held and candidate in the same block (printed); at the cap the held version stays
  improve  on at least one model, the select difference is >= tolerance.min_gain
  holdout  on every improved model, the holdout difference is >= tolerance.min_gain
  adopt    no veto, regress or hold, improve and holdout pass
The expected table (conditions x models x cases x rows) comes from cases.jsonl, not from what ran,
so a registered case or row with no valid grade holds the decision. The bootstrap 2.5th percentile of
the select difference is printed for reference only. Rows a candidate changes on purpose are listed
in plan.json "changed_rows": {cand: [row, ...]}: they leave the comparison with the held version and
must instead pass at >= tolerance.changed_min_pass on their own. A candidate listed in
plan.json "diff_candidates" needs registered "regression" cases (current full file vs integrated full
file); those cases only enter the non-inferiority checks. Judge splits on non-critical rows are left
out of the pass rates. p.tsv may carry a 重み (weight) column used in the scores.
"""
import argparse, csv, json, pathlib, random, statistics as st
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("exp"); ap.add_argument("--held", required=True)
    a = ap.parse_args()
    exp = pathlib.Path(a.exp).resolve()
    plan = json.loads((exp / "plan.json").read_text())
    tol = plan.get("tolerance", {})
    reject_drop = float(tol.get("reject_drop", 0.5)); min_gain = float(tol.get("min_gain", 0.05))
    reps = int(plan.get("reps", 3)); cap = reps + int(plan.get("max_extra_reps", 3))
    changed = {k: set(v) for k, v in plan.get("changed_rows", {}).items()}
    changed_min = float(tol.get("changed_min_pass", 1.0))
    crit, weight = set(), {}
    with open(exp / "p.tsv") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if str(r.get("重大", "")).strip().lower() in {"yes", "y", "true", "1", "はい"}:
                crit.add(r["id"])
            try:
                weight[r["id"]] = float(r.get("重み") or 1)
            except ValueError:
                weight[r["id"]] = 1.0
    reg = [json.loads(l) for l in (exp / "cases.jsonl").read_text().splitlines() if l.strip()]
    case_rows = {k["id"]: sorted({c["row"] for c in k.get("checks", [])}) for k in reg}
    reg_split = {k["id"]: k.get("split", "select") for k in reg}
    key = json.loads((exp / "runs" / ".key.json").read_text())
    total = defaultdict(int); runs = defaultdict(list)
    for rid, k in key.items():
        cell = (k["cond"], k["model"], k["case"]); total[cell] += 1
        gp = exp / "runs" / rid / "grades.json"
        if not gp.exists():
            continue
        g = json.loads(gp.read_text())
        if all(v.get("pass") is None for v in g.values()):
            continue
        runs[cell].append({"rid": rid, "g": g})
    conds = sorted(p.stem for p in (exp / "variants").glob("*.md") if not p.stem.lower().startswith("readme")) or sorted({c for c, _, _ in total})
    models = sorted(plan.get("models", {}) or {m for _, m, _ in total})
    cases = sorted(case_rows); split_of = reg_split
    sel = [k for k in cases if split_of[k] == "select"]; hold_cases = [k for k in cases if split_of[k] == "holdout"]

    def valid(cell, row):
        return [r["g"][row]["pass"] for r in runs.get(cell, []) if row in r["g"] and isinstance(r["g"][row].get("pass"), bool)]

    def score(r):
        xs = [(weight.get(k, 1.0), 1.0 if v["pass"] else 0.0) for k, v in r["g"].items() if isinstance(v.get("pass"), bool)]
        return sum(w * x for w, x in xs) / sum(w for w, _ in xs) if xs else None

    rng = random.Random(0)

    def diff(c, h, m, ks, iters=2000):
        ks = [k for k in ks if any(score(r) is not None for r in runs.get((c, m, k), [])) and any(score(r) is not None for r in runs.get((h, m, k), []))]
        if not ks:
            return None, None
        S = {(x, k): [s for s in map(score, runs[(x, m, k)]) if s is not None] for x in (c, h) for k in ks}
        d = st.mean(st.mean(S[(c, k)]) - st.mean(S[(h, k)]) for k in ks)
        bs = []
        for _ in range(iters):
            tot = []
            for k in (rng.choice(ks) for _ in ks):
                tot.append(st.mean(rng.choice(S[(c, k)]) for _ in S[(c, k)]) - st.mean(rng.choice(S[(h, k)]) for _ in S[(h, k)]))
            bs.append(st.mean(tot))
        bs.sort()
        return d, bs[int(0.025 * iters)]

    print(f"held={a.held} reps={reps} cap={cap} reject_drop={reject_drop} min_gain={min_gain} critical={sorted(crit)}")
    print("\nmean score per condition / model / split (valid grades only)")
    for c in conds:
        cols = []
        for m in models:
            for sp in ("select", "holdout", "regression"):
                xs = [s for k in cases if split_of[k] == sp for s in map(score, runs.get((c, m, k), [])) if s is not None]
                if xs or sp != "regression":
                    cols.append(f"{m}/{sp}=" + (f"{st.mean(xs):.3f}(n{len(xs)})" if xs else "-"))
        print(f"  {c:24s} " + "  ".join(cols))
    report = {"held": a.held, "candidates": {}}
    for c in conds:
        if c == a.held or c.startswith("baseline"):
            continue
        why, verdict, extra_cells = [], None, set()
        if c in plan.get("diff_candidates", []) and not any(s == "regression" for s in split_of.values()):
            why.append("REJECT: diff candidate without registered regression cases"); verdict = "reject"
        for m in models:
            for k in cases:
                ch, cc = (a.held, m, k), (c, m, k)
                if not runs.get(ch) or not runs.get(cc):
                    why.append(f"hold: unmeasured cell {m}/{k}"); extra_cells.add((m, k)); continue
                for row in case_rows[k]:
                    cv = [r["g"][row].get("pass") for r in runs[cc] if row in r["g"]]
                    if row in crit and False in cv:
                        ev = next(r["g"][row].get("evidence", "") for r in runs[cc] if row in r["g"] and r["g"][row].get("pass") is False)
                        why.append(f"REJECT veto {row} on {m}/{k}: {str(ev)[:160]}"); verdict = "reject"; continue
                    if row in crit and "split" in cv:
                        why.append(f"hold: judge split on critical {row} {m}/{k} (confirm the evidence)"); extra_cells.add((m, k)); continue
                    H, C = valid(ch, row), valid(cc, row)
                    if row in changed.get(c, set()):
                        if len(C) < reps:
                            why.append(f"hold: changed row {m}/{k}/{row} has {len(C)} valid grades < {reps}"); extra_cells.add((m, k))
                        elif st.mean(C) < changed_min - 1e-9:
                            if total[cc] < cap:
                                why.append(f"hold: changed row {m}/{k}/{row} passes {st.mean(C):.2f} < {changed_min}"); extra_cells.add((m, k))
                            else:
                                why.append(f"REJECT changed row {m}/{k}/{row} passes {st.mean(C):.2f} < {changed_min} at cap"); verdict = "reject"
                        continue
                    if len(H) < reps or len(C) < reps:
                        why.append(f"hold: {m}/{k}/{row} valid grades held={len(H)} cand={len(C)} < {reps}"); extra_cells.add((m, k)); continue
                    drop = st.mean(H) - st.mean(C)
                    if drop >= reject_drop - 1e-9:
                        why.append(f"REJECT regress {m}/{k}/{row}: held {st.mean(H):.2f} -> {st.mean(C):.2f}"); verdict = "reject"
                    elif drop > 1e-9:
                        if max(total[ch], total[cc]) < cap:
                            why.append(f"hold: {m}/{k}/{row} held {st.mean(H):.2f} -> {st.mean(C):.2f}"); extra_cells.add((m, k))
                        else:
                            why.append(f"tolerated at cap: {m}/{k}/{row} held {st.mean(H):.2f} -> {st.mean(C):.2f}")
        imp = {}
        for m in models:
            d, lo = diff(c, a.held, m, sel)
            dh, _ = diff(c, a.held, m, hold_cases, iters=1)
            imp[m] = {"select_diff": d, "select_p2.5": lo, "holdout_diff": dh}
        extra = []
        if verdict != "reject" and extra_cells:
            for m, k in sorted(extra_cells):
                n = min(3, cap - max(total[(a.held, m, k)], total[(c, m, k)]))
                if n > 0:
                    extra += [f"{a.held}:{m}:{k}:{n}", f"{c}:{m}:{k}:{n}"]
            verdict = "hold" if extra else "keep-held"
            if not extra:
                why.append("cap reached with incomplete comparisons: the held version stays")
        if verdict is None:
            up = [m for m, v in imp.items() if v["select_diff"] is not None and v["select_diff"] >= min_gain - 1e-9]
            if not up:
                verdict = "keep-held"; why.append(f"no model improved on select by >= min_gain={min_gain}")
            elif any(imp[m]["holdout_diff"] is None or imp[m]["holdout_diff"] < min_gain for m in up):
                verdict = "keep-held"; why.append(f"holdout gain below min_gain={min_gain} on an improved model")
            else:
                verdict = "adopt"; why.append(f"improved on {up}; holdout gain >= {min_gain}; no regression")
        report["candidates"][c] = {"verdict": verdict, "why": why, "improvement": imp, "extra": extra}
        f = lambda x: "-" if x is None else f"{x:+.3f}"
        print(f"\n== {c}: {verdict.upper()}")
        for m, v in imp.items():
            print(f"   {m}: select diff {f(v['select_diff'])} (bootstrap 2.5th pct {f(v['select_p2.5'])}, reference), holdout diff {f(v['holdout_diff'])}")
        for w in why[:15]:
            print("   -", w)
        if len(why) > 15:
            print(f"   - ... {len(why) - 15} more in decision.json")
        if extra:
            print("   next: run_trials.py EXP --extra " + " ".join(extra) + "  then grade.py and decide.py again")
    (exp / "decision.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
