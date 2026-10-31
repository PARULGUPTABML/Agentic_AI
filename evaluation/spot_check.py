"""Manual spot checks of the LLM judge.

1. Export a stratified sample of graded runs to CSV for a human to label::

       python -m evaluation.spot_check export results/runs.jsonl --n 20 --out results/spot_check.csv

   Fill in the ``human_accuracy`` (correct / partial / incorrect) and
   ``human_hallucination`` (0 / 1) columns.

2. Measure judge-vs-human agreement::

       python -m evaluation.spot_check agree results/spot_check.csv

   Reports raw agreement and Cohen's kappa for accuracy labels and hallucination.
   Rule of thumb: kappa >= 0.6 is substantial agreement; below that, revise the
   judge prompt / rubric before trusting the automated numbers.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

FIELDS = ["run_id", "question_id", "condition", "question", "final_answer", "judge_accuracy",
          "judge_hallucination", "judge_rationale", "human_accuracy", "human_hallucination", "notes"]


def export_sample(runs_path: str, n: int = 20, out: str = "results/spot_check.csv", seed: int = 7) -> int:
    rows = [json.loads(l) for l in Path(runs_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    by_cond: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("judge"):
            by_cond[r.get("condition", "")].append(r)
    rng = random.Random(seed)
    sample: list[dict] = []
    conds = sorted(by_cond)
    i = 0
    while len(sample) < n and any(by_cond.values()):      # round-robin over conditions
        c = conds[i % len(conds)]
        if by_cond[c]:
            sample.append(by_cond[c].pop(rng.randrange(len(by_cond[c]))))
        i += 1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sample:
            j = r.get("judge", {})
            w.writerow({"run_id": r.get("run_id"), "question_id": r.get("question_id"),
                        "condition": r.get("condition"), "question": r.get("question"),
                        "final_answer": r.get("final_answer", "")[:3000],
                        "judge_accuracy": j.get("accuracy_label"), "judge_hallucination": int(j.get("hallucination", 0)),
                        "judge_rationale": j.get("judge_rationale", ""), "human_accuracy": "",
                        "human_hallucination": "", "notes": ""})
    return len(sample)


def cohen_kappa(a: list, b: list) -> float | None:
    if not a or len(a) != len(b):
        return None
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    return 1.0 if pe == 1 else round((po - pe) / (1 - pe), 4)


def agreement(csv_path: str) -> dict:
    rows = [r for r in csv.DictReader(open(csv_path, encoding="utf-8")) if r.get("human_accuracy")]
    ja = [r["judge_accuracy"].strip().lower() for r in rows]
    ha = [r["human_accuracy"].strip().lower() for r in rows]
    jh = [str(r["judge_hallucination"]).strip() for r in rows if r.get("human_hallucination") != ""]
    hh = [str(r["human_hallucination"]).strip() for r in rows if r.get("human_hallucination") != ""]
    return {
        "n_labelled": len(rows),
        "accuracy_agreement": round(sum(x == y for x, y in zip(ja, ha)) / len(rows), 4) if rows else None,
        "accuracy_kappa": cohen_kappa(ja, ha),
        "hallucination_agreement": round(sum(x == y for x, y in zip(jh, hh)) / len(jh), 4) if jh else None,
        "hallucination_kappa": cohen_kappa(jh, hh),
        "confusion_accuracy": dict(Counter(f"judge={x}|human={y}" for x, y in zip(ja, ha))),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("runs")
    e.add_argument("--n", type=int, default=20)
    e.add_argument("--out", default="results/spot_check.csv")
    g = sub.add_parser("agree")
    g.add_argument("csv")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        print(f"wrote {export_sample(a.runs, a.n, a.out)} rows to {a.out}")
    else:
        print(json.dumps(agreement(a.csv), indent=2))


if __name__ == "__main__":
    main()
