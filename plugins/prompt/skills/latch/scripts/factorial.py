#!/usr/bin/env python3
"""Factorial design over parts of a candidate: which sentences carry weight, which phrasing works.

usage: factorial.py EXP_DIR make --out EXP2
       factorial.py EXP2 analyze

make reads EXP_DIR/factors.json:
  {"base": "<candidate in variants/ with a trace.tsv column>", "n_variants": 16, "seed": 0, "reps": 1,
   "factors": [{"id": "F1", "anchor": "<text that occurs once in the base>", "levels": ["<anchor>", ""]},
               {"id": "F2", "anchor": "...", "levels": ["<anchor>", {"text": "<other wording>", "trace": {"R3": "<quote>"}}]}]}
  The first level is the anchor itself; effects are measured against it. A level "" removes the anchor.
  An anchor overlapping the trace.tsv quote of a row carries that row: it cannot have a "" level, and every
  other level gives the row's quote in "trace" (a level equal to the anchor reuses the base's quote).
  Anchors must not overlap. Each factor's levels appear equally often over the n_variants versions (when
  n_variants is a multiple of the number of levels), in a random order chosen, out of 500, as the one whose
  level pairs of every two factors are closest to equal counts. "reps" (default: the plan's) is the
  repetitions per version in EXP2.
  EXP2 (new) gets p.tsv, cases.jsonl, env/, a plan.json with the exploration models only, variants/
  baseline_original.md and fac_NN.md, a trace.tsv for the versions and factorial_design.json (their levels).
  Then run: check_trace.py EXP2; run_trials.py EXP2 --probe; run_trials.py EXP2; grade.py EXP2;
  factorial.py EXP2 analyze.
analyze reads the graded runs of EXP2. Per exploration model, the effect of a level is the mean run score over
  the versions with that level minus the mean over the versions with the first level (the balanced assignment
  averages the other factors out), with a bootstrap interval over versions at tolerance.confidence. A "" level
  is removable when, on every exploration model and every row, the upper bound of its pass-rate drop is below
  reject_drop. 2x2 interactions of two-level factors are printed when every cell has 2 versions or more.
  Writes factorial.json. Removing several removable parts at once can still interact: build that candidate
  and compare it with the held version in an ordinary round.
"""
import argparse, csv, json, pathlib, random, re, shutil, statistics as st, sys

sys.dont_write_bytecode = True  # importing a sibling script must not write __pycache__ into the skill directory
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from decide import DEFAULT_CONFIDENCE, DEFAULT_REJECT_DROP, load as load_exp  # noqa: E402
from run_trials import DEFAULT_MODELS  # noqa: E402


def norm(s):
    return re.sub(r"\s+", "", s or "")


def span_of(text, quote):
    """Raw [start, end) of the first whitespace-insensitive occurrence of quote in text (as check_trace.py
    compares), or None."""
    keep, idx = [], []
    for i, ch in enumerate(text):
        if not re.match(r"\s", ch):
            keep.append(ch); idx.append(i)
    nq = norm(quote)
    j = "".join(keep).find(nq) if nq else -1
    return None if j < 0 else (idx[j], idx[j + len(nq) - 1] + 1)


def level_of(x):
    return {"text": x, "trace": {}} if isinstance(x, str) else {"text": x.get("text", ""), "trace": dict(x.get("trace", {}))}


def make(exp, out):
    spec = json.loads((exp / "factors.json").read_text())
    plan = json.loads((exp / "plan.json").read_text())
    base = spec["base"]
    text = (exp / "variants" / f"{base}.md").read_text()
    rows = [r["id"] for r in csv.DictReader(open(exp / "p.tsv"), delimiter="\t")]
    trace = {t["id"]: t for t in csv.DictReader(open(exp / "trace.tsv"), delimiter="\t")}
    bad = []
    quotes = {}
    for r in rows:
        q = (trace.get(r) or {}).get(base)
        if not q or span_of(text, q) is None:
            bad.append(f"{r}: no quote of {base} in trace.tsv found in variants/{base}.md")
        quotes[r] = q
    if bad:
        sys.exit("\n".join(bad))
    qspans = {r: span_of(text, q) for r, q in quotes.items()}
    factors = []
    for f in spec["factors"]:
        a = f["anchor"]; i = text.find(a)
        if not a or i < 0 or text.find(a, i + 1) >= 0:
            bad.append(f"{f['id']}: the anchor must occur exactly once in variants/{base}.md"); continue
        sp = (i, i + len(a))
        for g in factors:
            if sp[0] < g["span"][1] and g["span"][0] < sp[1]:
                bad.append(f"{f['id']}: the anchor overlaps the anchor of {g['id']}")
        levels = [level_of(x) for x in f["levels"]]
        if len(levels) < 2 or levels[0]["text"] != a:
            bad.append(f"{f['id']}: needs at least 2 levels, the first one being the anchor itself")
        carried = [r for r, (s, e) in qspans.items() if s < sp[1] and sp[0] < e]
        for j, lv in enumerate(levels):
            if carried and lv["text"] == "":
                bad.append(f"{f['id']}: carries {carried}, so it cannot have an empty level")
            for r in carried:
                if lv["text"] == a and r not in lv["trace"]:
                    lv["trace"][r] = quotes[r]
                if not lv["trace"].get(r):
                    bad.append(f"{f['id']}: level {j} must give the quote of {r} in \"trace\"")
                elif norm(lv["trace"][r]) not in norm(lv["text"]):
                    bad.append(f"{f['id']}: level {j}: the quote of {r} is not in the level's text")
        factors.append({"id": f["id"], "anchor": a, "span": sp, "levels": levels, "carried": carried})
    if bad:
        sys.exit("\n".join(bad))
    n = int(spec["n_variants"]); rng = random.Random(spec.get("seed", 0))
    for f in factors:
        if n % len(f["levels"]):
            print(f"warning: n_variants={n} is not a multiple of the {len(f['levels'])} levels of {f['id']}: unbalanced")

    def draw():
        out = {}
        for f in factors:
            col = [j % len(f["levels"]) for j in range(n)]; rng.shuffle(col); out[f["id"]] = col
        return out

    def imbalance(asg):
        """How far the level pairs of every two factors are from equal counts (0: orthogonal)."""
        s = 0.0
        for i, f in enumerate(factors):
            for g in factors[i + 1:]:
                lf, lg = len(f["levels"]), len(g["levels"])
                cnt = {}
                for x, y in zip(asg[f["id"]], asg[g["id"]]):
                    cnt[(x, y)] = cnt.get((x, y), 0) + 1
                s += sum((cnt.get((x, y), 0) - n / (lf * lg)) ** 2 for x in range(lf) for y in range(lg))
        return s

    # random orders, keeping the one whose factors are closest to orthogonal so main effects do not mix
    assign = min((draw() for _ in range(500)), key=imbalance)
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} exists and is not empty")
    (out / "variants").mkdir(parents=True, exist_ok=True)
    names, cols = [], {}
    for v in range(n):
        name = f"fac_{v + 1:02d}"; names.append(name)
        t = text
        for f in sorted(factors, key=lambda f: f["span"][0], reverse=True):
            s, e = f["span"]; t = t[:s] + f["levels"][assign[f["id"]][v]]["text"] + t[e:]
        col = {}
        for r in rows:
            owner = next((f for f in factors if r in f["carried"]), None)
            col[r] = owner["levels"][assign[owner["id"]][v]]["trace"][r] if owner else quotes[r]
            if norm(col[r]) not in norm(t):
                bad.append(f"{name}: the quote of {r} is missing from the assembled text")
        cols[name] = col
        (out / "variants" / f"{name}.md").write_text(t)
    if bad:
        shutil.rmtree(out); sys.exit("\n".join(bad))
    for fname in ("p.tsv", "cases.jsonl"):
        shutil.copy2(exp / fname, out / fname)
    env = exp / plan.get("env", "env")
    if env.exists():
        shutil.copytree(env, out / plan.get("env", "env"))
    if (exp / "variants" / "baseline_original.md").exists():
        shutil.copy2(exp / "variants" / "baseline_original.md", out / "variants" / "baseline_original.md")
    p2 = {k: v for k, v in plan.items() if k not in ("gate", "changed_rows", "diff_candidates")}
    if p2.get("source") and not pathlib.Path(p2["source"]).is_absolute():
        p2["source"] = str((exp / p2["source"]).resolve())
    p2["reps"] = int(spec.get("reps", plan.get("reps", 3)))
    (out / "plan.json").write_text(json.dumps(p2, ensure_ascii=False, indent=1))
    with open(out / "trace.tsv", "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["id", *names])
        for r in rows:
            w.writerow([r, *(cols[x][r] for x in names)])
    design = {"source_experiment": str(exp), "base": base, "seed": spec.get("seed", 0),
              "factors": [{"id": f["id"], "anchor": f["anchor"], "levels": [lv["text"] for lv in f["levels"]]} for f in factors],
              "assign": {name: {f["id"]: assign[f["id"]][v] for f in factors} for v, name in enumerate(names)}}
    (out / "factorial_design.json").write_text(json.dumps(design, ensure_ascii=False, indent=1))
    print(f"{out}: {n} versions of {base} over {len(factors)} factors; reps {p2['reps']}")


def analyze(exp2):
    design = json.loads((exp2 / "factorial_design.json").read_text())
    data = load_exp(exp2); plan = data["plan"]; tol = plan.get("tolerance", {})
    conf = float(tol.get("confidence", DEFAULT_CONFIDENCE)); reject_drop = float(tol.get("reject_drop", DEFAULT_REJECT_DROP))
    models = list(plan.get("models") or DEFAULT_MODELS)
    assign, weight = design["assign"], data["weight"]
    rows = sorted({r for rs in data["case_rows"].values() for r in rs})

    def score(r):
        xs = [(weight.get(k, 1.0), 1.0 if v["pass"] else 0.0) for k, v in r["g"].items() if isinstance(v.get("pass"), bool)]
        return sum(w * x for w, x in xs) / sum(w for w, _ in xs) if xs else None

    S, R = {}, {}
    for (c, m, k), rs in data["runs"].items():
        if c not in assign:
            continue
        for r in rs:
            s = score(r)
            if s is not None:
                S.setdefault((c, m), []).append(s)
            for row, v in r["g"].items():
                if isinstance(v.get("pass"), bool):
                    R.setdefault((c, m, row), []).append(1.0 if v["pass"] else 0.0)
    rng = random.Random(0)

    def effect(values, fid, j, iters=2000):
        """Mean over versions with level j minus mean over versions with level 0, and its bootstrap interval."""
        vs = [v for v in values if v in assign]
        A = [values[v] for v in vs if assign[v][fid] == j]; Z = [values[v] for v in vs if assign[v][fid] == 0]
        if not A or not Z:
            return None
        bs = []
        for _ in range(iters):
            pick = [rng.choice(vs) for _ in vs]
            a = [values[v] for v in pick if assign[v][fid] == j]; z = [values[v] for v in pick if assign[v][fid] == 0]
            if a and z:
                bs.append(st.mean(a) - st.mean(z))
        bs.sort()
        return {"diff": st.mean(A) - st.mean(Z), "lo": bs[int((1 - conf) * len(bs))], "hi": bs[min(len(bs) - 1, int(conf * len(bs)))]}

    report = {"base": design["base"], "confidence": conf, "reject_drop": reject_drop, "factors": {}, "interactions": []}
    print(f"factorial over {design['base']}: {len(assign)} versions, confidence {conf}, reject_drop {reject_drop}")
    for f in design["factors"]:
        fid = f["id"]; out = {"anchor": f["anchor"], "levels": f["levels"], "effects": {}, "removable": None}
        print(f"\n{fid}: {f['anchor'][:70]!r}")
        for m in models:
            score_v = {v: st.mean(S[(v, m)]) for v in assign if (v, m) in S}
            out["effects"][m] = []
            for j in range(1, len(f["levels"])):
                e = effect(score_v, fid, j)
                out["effects"][m].append(e)
                label = "(removed)" if f["levels"][j] == "" else repr(f["levels"][j][:40])
                print(f"   {m}: level {j} {label}: " + ("unmeasured" if e is None else f"{e['diff']:+.3f} [{e['lo']:+.3f}, {e['hi']:+.3f}]"))
        if "" in f["levels"]:
            j = f["levels"].index("")
            bounds = []  # (upper bound of the pass-rate drop when removed, model, row)
            for m in models:
                for row in rows:
                    e = effect({v: st.mean(R[(v, m, row)]) for v in assign if (v, m, row) in R}, fid, j)
                    bounds.append((None if e is None else -e["lo"], m, row))
            missing = [b for b in bounds if b[0] is None]
            if missing:
                out["removable"] = False
                print(f"   removable: no (unmeasured on {', '.join(f'{m}/{row}' for _, m, row in missing[:5])})")
            else:
                up, m, row = max(bounds)
                out["removable"] = up < reject_drop
                out["worst_drop_bound"] = {"bound": up, "model": m, "row": row}
                print(f"   removable: {'yes' if out['removable'] else 'no'} (largest drop bound {up:+.3f} on {m}/{row}, reject_drop {reject_drop})")
        report["factors"][fid] = out
    two = [f for f in design["factors"] if len(f["levels"]) == 2]
    for i, f in enumerate(two):
        for g in two[i + 1:]:
            for m in models:
                cell = {}
                for v in assign:
                    if (v, m) in S:
                        cell.setdefault((assign[v][f["id"]], assign[v][g["id"]]), []).append(st.mean(S[(v, m)]))
                if len(cell) == 4 and all(len(x) >= 2 for x in cell.values()):
                    mm = {k: st.mean(x) for k, x in cell.items()}
                    inter = (mm[(1, 1)] - mm[(1, 0)]) - (mm[(0, 1)] - mm[(0, 0)])
                    report["interactions"].append({"factors": [f["id"], g["id"]], "model": m, "interaction": inter})
                    print(f"\ninteraction {f['id']} x {g['id']} on {m}: {inter:+.3f}")
    (exp2 / "factorial.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp"); ap.add_argument("action", choices=["make", "analyze"]); ap.add_argument("--out")
    a = ap.parse_args()
    exp = pathlib.Path(a.exp).resolve()
    if a.action == "make":
        if not a.out:
            sys.exit("make needs --out EXP2")
        make(exp, pathlib.Path(a.out).resolve())
    else:
        analyze(exp)


if __name__ == "__main__":
    main()
