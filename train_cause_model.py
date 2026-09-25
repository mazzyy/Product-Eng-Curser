"""
Train the cause finder (model 2) on many simulated weeks where the true cause is known.

Each simulated week gets random root-cause scenarios (machine / people / method / station,
in any symptom family), plus all the usual noise. The script finds the incidents exactly as
find_causes.py does, builds their evidence features, labels them with the planted cause, and
learns a random forest. Weeks are split: the last 20% are held out and never seen in
training - that is the honest accuracy. The final model is then refit on all weeks.

    python train_cause_model.py              # 40 weeks, about 1 minute on a MacBook
    python train_cause_model.py --weeks 80   # more data
"""
from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from cause_finder import CAUSES, FEATURES, block_table, context, evidence, incidents, match_scenario
from generate_data import build_week
from station_db import MODELS_DIR

MIN_CONFIDENCE = 0.5


def week_rows(seed: int):
    w = build_week(seed, 7, demo=False)
    B, ctx = block_table(w["tables"]), context(w["tables"])
    rows = []
    for inc in incidents(B):
        f, _ = evidence(inc, B, ctx)
        label, scen = match_scenario(inc, B, w["causes"])
        rows.append({**f, "label": label, "scenario": scen, "week": seed, "family": inc["family"]})
    return rows, w["causes"].assign(week=seed)


def report(y_true, y_pred, title):
    labels = CAUSES
    cm = pd.crosstab(pd.Series(y_true, name="true"), pd.Series(y_pred, name="predicted")).reindex(
        index=labels, columns=labels, fill_value=0)
    acc = float(np.mean(np.array(y_true) == np.array(y_pred)))
    print(f"\n{title}: accuracy {acc:.0%} on {len(y_true)} incidents")
    print(cm.to_string())
    for c in labels:
        tp = cm.at[c, c]
        prec = tp / max(cm[c].sum(), 1)
        rec = tp / max(cm.loc[c].sum(), 1)
        print(f"    {c:<8} precision {prec:.0%}  recall {rec:.0%}")
    return acc, cm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=40)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()
    t0 = time.time()

    seeds = list(range(1000, 1000 + args.weeks))
    rows, causes = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for r, c in pool.map(week_rows, seeds):
            rows += r
            causes.append(c)
    data = pd.DataFrame(rows)
    causes = pd.concat(causes, ignore_index=True)
    print(f"simulated {args.weeks} weeks in {time.time() - t0:.0f}s: {len(causes)} planted root causes, "
          f"{len(data)} incidents found ({(data.label == 'none').sum()} not linked to any planted cause)")
    print("incidents per true cause:", data[data.label != "none"].label.value_counts().to_dict())

    lab = data[data.label != "none"]
    n_test = max(1, len(seeds) // 5)
    test_weeks = set(seeds[-n_test:])
    tr, te = lab[~lab.week.isin(test_weeks)], lab[lab.week.isin(test_weeks)]

    clf = RandomForestClassifier(n_estimators=400, min_samples_leaf=2, class_weight="balanced",
                                 random_state=0, n_jobs=-1)
    clf.fit(tr[FEATURES], tr.label)
    proba = clf.predict_proba(te[FEATURES])
    pred = clf.classes_[proba.argmax(axis=1)]
    conf = proba.max(axis=1)
    acc, cm = report(te.label.values, pred, f"held-out weeks ({n_test} weeks never seen in training)")
    sure = conf >= MIN_CONFIDENCE
    print(f"  confidence >= {MIN_CONFIDENCE}: {sure.mean():.0%} of incidents, accuracy there "
          f"{np.mean(te.label.values[sure] == pred[sure]):.0%} (the rest are reported as 'unclear')")

    # per planted root cause: found at all? majority vote right?
    te = te.assign(pred=pred)
    tc = causes[causes.week.isin(test_weeks)]
    found = te.groupby(["week", "scenario"]).pred.agg(lambda x: x.mode().iloc[0])
    right = [found.get((w, s)) == c for w, s, c in zip(tc.week, tc.scenario_id, tc.cause)]
    det = [(w, s) in found.index for w, s in zip(tc.week, tc.scenario_id)]
    print(f"  planted root causes in held-out weeks: {len(tc)}, detected {np.mean(det):.0%}, "
          f"cause right (majority of its incidents) {np.mean(right):.0%}")
    by_fam = te.assign(ok=te.label == te.pred).groupby("family").ok.mean()
    print("  accuracy by symptom family:", {k: f"{v:.0%}" for k, v in by_fam.items()})

    # final model on everything
    clf.fit(lab[FEATURES], lab.label)
    imp = pd.Series(clf.feature_importances_, index=FEATURES).sort_values(ascending=False)
    print("\nstrongest evidence:", ", ".join(f"{k} {v:.2f}" for k, v in imp.head(8).items()))
    MODELS_DIR.mkdir(exist_ok=True)
    joblib.dump({"model": clf, "features": FEATURES, "min_confidence": MIN_CONFIDENCE,
                 "heldout_accuracy": acc, "heldout_confusion": cm, "importance": imp,
                 "weeks": args.weeks, "n_incidents": len(lab)}, MODELS_DIR / "cause_finder.joblib")
    print(f"saved -> {MODELS_DIR / 'cause_finder.joblib'}   ({time.time() - t0:.0f}s total)")


if __name__ == "__main__":
    main()
