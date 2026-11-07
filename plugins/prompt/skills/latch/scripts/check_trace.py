#!/usr/bin/env python3
"""Self-check that every candidate expresses every requirement row of p.tsv.

usage: check_trace.py EXP_DIR

trace.tsv: first column "id" (row id of p.tsv), then one column per candidate (variants/<name>.md stem);
each cell quotes the candidate text that expresses the row. A cell must be non-empty and its quote must
occur in the candidate file (whitespace-insensitive). Baselines (baseline_*) are not checked.
variants/baseline_original.md must be a byte-identical copy of plan.json "source" (the current document).
An A/A control must be a byte-identical copy of what it controls: variants/baseline_aa.md of
baseline_original.md, variants/baseline_aa_<cond>.md of <cond>.md.
README files in variants/ are ignored. Exit code 1 when anything is missing.
"""
import csv, pathlib, re, sys

exp = pathlib.Path(sys.argv[1]).resolve()
rows = [r["id"] for r in csv.DictReader(open(exp / "p.tsv"), delimiter="\t")]
trace = list(csv.DictReader(open(exp / "trace.tsv"), delimiter="\t"))
cands = [c for c in (trace[0].keys() if trace else []) if c != "id"]
norm = lambda s: re.sub(r"\s+", "", s or "")
texts = {c: norm((exp / "variants" / f"{c}.md").read_text()) for c in cands if (exp / "variants" / f"{c}.md").exists()}
bad = []
for c in sorted(p.stem for p in (exp / "variants").glob("*.md")):
    if not c.startswith("baseline") and not c.lower().startswith("readme") and c not in cands:
        bad.append(f"{c}: no column in trace.tsv")
by_id = {t["id"]: t for t in trace}
for rid in rows:
    t = by_id.get(rid)
    if t is None:
        bad.append(f"{rid}: missing from trace.tsv"); continue
    for c in cands:
        q = norm(t.get(c))
        if not q:
            bad.append(f"{rid}/{c}: empty")
        elif c in texts and q not in texts[c]:
            bad.append(f"{rid}/{c}: quote not found in variants/{c}.md")
import json
plan = json.loads((exp / "plan.json").read_text()) if (exp / "plan.json").exists() else {}
src = plan.get("source")
bo = exp / "variants" / "baseline_original.md"
if src:
    sp = (exp / src) if not pathlib.Path(src).is_absolute() else pathlib.Path(src)
    if not sp.is_file():
        bad.append(f"plan.json source not found: {src}")
    elif not bo.is_file() or bo.read_bytes() != sp.read_bytes():
        bad.append(f"variants/baseline_original.md is not a byte-identical copy of {src}")
elif bo.is_file():
    bad.append("plan.json has no \"source\" (path of the current document) to check baseline_original against")
for aa in sorted((exp / "variants").glob("baseline_aa*.md")):  # A/A controls: baseline_aa copies baseline_original,
    ref = "baseline_original" if aa.stem == "baseline_aa" else aa.stem[len("baseline_aa_"):]  # baseline_aa_<c> copies <c>
    rp = exp / "variants" / f"{ref}.md"
    if not rp.is_file():
        bad.append(f"variants/{aa.name} is the A/A copy of {ref}, but variants/{ref}.md does not exist")
    elif aa.read_bytes() != rp.read_bytes():
        bad.append(f"variants/{aa.name} is not a byte-identical copy of variants/{ref}.md")
for b in bad:
    print(b)
print("OK" if not bad else f"{len(bad)} problems")
sys.exit(1 if bad else 0)
