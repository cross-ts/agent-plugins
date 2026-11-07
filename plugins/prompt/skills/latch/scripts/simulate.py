#!/usr/bin/env python3
"""Operating characteristics of the registered decision rule, without calling any model.

usage: simulate.py EXP_DIR [--assume LO:HI | --from-runs EXP2 [--held COND]] [--gate-assume LO:HI]
                   [--gains 0,0.1,0.2] [--trials 200] [--reps N,...] [--seed S]

Builds the registered table (plan.json models and gate, cases.jsonl, p.tsv) for a held version and one
candidate, draws every grade at random from a pass rate per (model, case, row), and runs decide.decide_core,
following its extra and gate runs until it stops asking, as latch.sh does.
  --assume LO:HI     held pass rates drawn uniformly from [LO, HI] for each (model, case, row)
  --from-runs EXP2   held pass rates estimated from the graded runs of --held in EXP2 (Beta(1,1) posterior
                     mean); cells without grades fall back to --assume
  --gate-assume      pass rates of the gate models (default 0.95:1.0)
  --gains            candidate pass rate = held pass rate + gain, capped at 1; gain 0 is the A/A case
Prints, per reps and gain, the share of adopt / keep-held / reject and the mean number of runs; with gain 0,
adopt is the false adoption rate and reject the false rejection rate. Also prints the rule-of-three bound
3/n that zero violations of each critical row would show with the planned runs.
"""
import argparse, collections, copy, json, pathlib, random, statistics as st, sys

sys.dont_write_bytecode = True  # importing a sibling script must not write __pycache__ into the skill directory
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from decide import decide_core, load as load_exp  # noqa: E402
from run_trials import DEFAULT_MODELS, gate_models, gate_reps, gate_splits  # noqa: E402

HELD, CAND = "held", "cand"


def registered(exp):
    """plan, critical rows, weights, case rows and splits from EXP_DIR (variants and runs are not needed)."""
    import csv
    plan = json.loads((exp / "plan.json").read_text())
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
    return (plan, crit, weight, {k["id"]: sorted({c["row"] for c in k.get("checks", [])}) for k in reg},
            {k["id"]: k.get("split", "select") for k in reg})


def rng_range(s):
    lo, hi = (float(x) for x in s.split(":"))
    return lo, hi


def observed_rates(exp2, held):
    """(model, case, row) -> Beta(1,1) posterior mean of the pass rate of `held` in an earlier experiment."""
    data = load_exp(exp2)
    out = {}
    for (c, m, k), rs in data["runs"].items():
        if c != held:
            continue
        for row in data["case_rows"].get(k, []):
            xs = [r["g"][row]["pass"] for r in rs if row in r["g"] and isinstance(r["g"][row].get("pass"), bool)]
            if xs:
                out[(m, k, row)] = (sum(xs) + 1) / (len(xs) + 2)
    return out


def simulate_one(plan, crit, weight, case_rows, split_of, rates, gain, rng, max_rounds=30):
    explore = list(plan.get("models") or DEFAULT_MODELS); gate = list(gate_models(plan))
    reps = int(plan.get("reps", 3)); g_reps = gate_reps(plan); g_splits = set(gate_splits(plan))
    runs, total = collections.defaultdict(list), collections.defaultdict(int)

    def add(cond, m, k, n):
        for _ in range(n):
            g = {}
            for row in case_rows[k]:
                p = rates[(m, k, row)]
                p = min(1.0, p + gain) if cond == CAND else p
                g[row] = {"pass": rng.random() < p, "evidence": "", "by": "sim"}
            runs[(cond, m, k)].append({"rid": "", "g": g}); total[(cond, m, k)] += 1

    for cond in (HELD, CAND):
        for m in explore:
            for k in case_rows:
                add(cond, m, k, reps)
    data = {"plan": plan, "crit": crit, "weight": weight, "case_rows": case_rows, "split_of": split_of,
            "runs": runs, "total": total, "stats": {}, "conds": [CAND, HELD], "lengths": {HELD: 1000, CAND: 1000}}
    for _ in range(max_rounds):
        report = decide_core(data, HELD)
        r = report["candidates"][CAND]
        if r["verdict"] not in ("hold", "gate"):
            break
        for spec in report["next"]["extra"]:
            cond, m, k, n = spec.split(":")
            add(cond, m, k, int(n))
        for cond in report["next"]["gate"]:
            for m in gate:
                for k in case_rows:
                    if split_of[k] in g_splits and total[(cond, m, k)] < g_reps:
                        add(cond, m, k, g_reps - total[(cond, m, k)])
    return r["verdict"], sum(total.values()), r["why"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp")
    ap.add_argument("--assume", default="0.6:0.95")
    ap.add_argument("--from-runs"); ap.add_argument("--held", default="baseline_original")
    ap.add_argument("--gate-assume", default="0.95:1.0")
    ap.add_argument("--gains", default="0,0.1,0.2")
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--reps", default="")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    exp = pathlib.Path(a.exp).resolve()
    plan0, crit, weight, case_rows, split_of = registered(exp)
    explore = list(plan0.get("models") or DEFAULT_MODELS); gate = list(gate_models(plan0))
    lo, hi = rng_range(a.assume); glo, ghi = rng_range(a.gate_assume)
    seen = observed_rates(pathlib.Path(a.from_runs).resolve(), a.held) if a.from_runs else {}
    gains = [float(x) for x in a.gains.split(",")]
    reps_list = [int(x) for x in a.reps.split(",")] if a.reps else [int(plan0.get("reps", 3))]
    print(f"{len(case_rows)} cases ({collections.Counter(split_of.values())}), explore={explore}, gate={gate}, "
          f"held pass rates {'from ' + a.from_runs + ' (fallback ' + a.assume + ')' if a.from_runs else a.assume}, "
          f"gate {a.gate_assume}, {a.trials} trials each")
    print(f"tolerance={plan0.get('tolerance', {})}")
    print(f"\n{'reps':>4} {'gain':>5} {'adopt':>6} {'keep':>6} {'reject':>6} {'runs':>7}  main reasons")
    for reps in reps_list:
        plan = copy.deepcopy(plan0); plan["reps"] = reps
        for gain in gains:
            verdicts, n_runs, reasons = collections.Counter(), [], collections.Counter()
            for t in range(a.trials):
                rng = random.Random(a.seed * 1_000_003 + t)
                rates = {}
                for m in explore + gate:
                    for k, rows in case_rows.items():
                        for row in rows:
                            r0 = seen.get((m, k, row))
                            rates[(m, k, row)] = r0 if r0 is not None else (rng.uniform(glo, ghi) if m in gate else rng.uniform(lo, hi))
                v, n, why = simulate_one(plan, crit, weight, case_rows, split_of, rates, gain, rng)
                verdicts[v] += 1; n_runs.append(n)
                w = next((x for x in why if x.startswith(("REJECT", "unresolved"))), why[-1] if why else "")
                reasons[" ".join(w.split()[:2])] += 1
            pct = lambda v: f"{100 * verdicts[v] / a.trials:5.1f}%"
            top = ", ".join(f"{k} {n}" for k, n in reasons.most_common(3))
            print(f"{reps:>4} {gain:>5.2f} {pct('adopt'):>6} {pct('keep-held'):>6} {pct('reject'):>6} {st.mean(n_runs):>7.0f}  {top}")
    if crit:
        reps = reps_list[0]
        print("\nrule of three: zero violations of a critical row show at best this upper bound (95%)")
        for row in sorted(crit):
            ks = [k for k, rows in case_rows.items() if row in rows]
            n = reps * len(ks)
            if n:
                print(f"  {row}: {len(ks)} cases x {reps} reps = {n} runs per model -> violation rate < {min(1.0, 3 / n):.2f}")


if __name__ == "__main__":
    main()
