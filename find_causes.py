"""
Cause finder (model 2) - run it on the factory database.

For every incident (a symptom seen in one or more 2-hour blocks of a shift) it answers:
which cause - machine, people, method or station - who or what exactly, how sure, and why.

Writes:
  incidents table          one row per incident: symptom, cause, confidence, culprit, evidence, graph
  st012/st013.incident_id  every cycle of an incident's blocks points to its incident
                           (a block in two incidents points to the earlier one)
Incidents with the same cause and culprit are grouped into one case (case_id).

    python train_cause_model.py    # once: learn from simulated weeks
    python find_causes.py
"""
from __future__ import annotations

import time

import joblib
import numpy as np
import pandas as pd

from cause_finder import (CAUSES, block_table, context, culprit_and_evidence, cycles, evidence, graph_text,
                          incidents, match_scenario, symptom_text)
from station_db import CAUSES_PATH, INCIDENTS_SQL, MODELS_DIR, connect, station_tables


def main():
    t0 = time.time()
    path = MODELS_DIR / "cause_finder.joblib"
    if not path.exists():
        raise SystemExit("no trained cause finder yet - run: python train_cause_model.py")
    bundle = joblib.load(path)
    clf, feats, min_conf = bundle["model"], bundle["features"], bundle["min_confidence"]

    con = connect()
    names = station_tables(con)
    tables = {t: pd.read_sql(f"SELECT * FROM {t}", con) for t in names}
    B, ctx = block_table(tables), context(tables)
    incs = incidents(B)
    if not incs:
        print("no incidents found")
        return

    rows, X = [], []
    for inc in incs:
        f, ev = evidence(inc, B, ctx)
        X.append([f[k] for k in feats])
        rows.append((inc, f, ev))
    proba = clf.predict_proba(pd.DataFrame(X, columns=feats))
    classes = list(clf.classes_)

    out = []
    for i, ((inc, f, ev), p) in enumerate(zip(rows, proba)):
        best = int(np.argmax(p))
        cause = classes[best] if p[best] >= min_conf else "unclear"
        culprit, why = culprit_and_evidence(classes[best], inc["family"], f, ev)
        S = ev["S"]
        out.append({
            "incident_id": i + 1, "family": inc["family"], "shift_date": inc["shift_date"], "shift": inc["shift"],
            "start_ts": S.start.min().strftime("%Y-%m-%d %H:%M:%S"),
            "end_ts": S.end.max().strftime("%Y-%m-%d %H:%M:%S"),
            "stations": ",".join(ev["stations"]), "top_station": ev["top"], "blocks": len(S),
            "symptom": symptom_text(inc["family"], ev), "cause": cause, "confidence": round(float(p[best]), 3),
            **{f"p_{c}": round(float(p[classes.index(c)]), 3) if c in classes else 0.0 for c in CAUSES},
            "culprit": culprit if cause != "unclear" else f"unclear (best guess: {classes[best]} - {culprit})",
            "evidence": why, "graph": graph_text(inc["family"], f, ev), "bid_list": inc["bids"],
        })
    inc_df = pd.DataFrame(out)

    # cases: same cause + culprit + symptom family
    key = inc_df.cause + "|" + inc_df.culprit + "|" + inc_df.family
    first = inc_df.groupby(key).incident_id.transform("min")
    inc_df["case_id"] = first.rank(method="dense").astype(int)

    con.executescript(INCIDENTS_SQL)
    inc_df.drop(columns=["bid_list"]).to_sql("incidents", con, if_exists="append", index=False)

    # link cycles to incidents
    bid_to_inc = {}
    for r in inc_df.itertuples():            # a block in two incidents keeps the first one
        for b in r.bid_list:
            bid_to_inc.setdefault(b, r.incident_id)
    blk = B.set_index(["station", "shift_date", "shift", "block"]).bid
    for t in names:
        con.execute(f"UPDATE {t} SET incident_id = NULL")
        c = cycles(tables[t], t)
        keys = list(zip([t] * len(c), c.shift_date, c["shift"], c.block))
        ids = [bid_to_inc.get(blk.get(k)) for k in keys]
        upd = [(int(i), int(e)) for i, e in zip(ids, c.event_id) if i is not None]
        con.executemany(f"UPDATE {t} SET incident_id=? WHERE event_id=?", upd)
    checked = cross_check_people(con, inc_df)
    con.commit()
    con.close()

    print(f"cause finder: {len(B)} blocks, {len(inc_df)} incidents, {inc_df.case_id.nunique()} cases "
          f"({time.time() - t0:.1f}s)")
    print(f"  by cause: {inc_df.cause.value_counts().to_dict()}")
    print("\ncases (same cause + culprit):")
    for cid, g in inc_df.groupby("case_id"):
        r = g.iloc[0]
        span = f"{pd.Timestamp(g.start_ts.min()):%a %d %H:%M} -> {pd.Timestamp(g.end_ts.max()):%a %d %H:%M}"
        print(f"  #{cid:<2} {r.cause.upper():<8} {r.culprit:<26} {r.family:<8} {len(g)} incident(s)  {span}  "
              f"conf {g.confidence.mean():.0%}")
        print(f"        seen:  {r.symptom}")
        print(f"        why:   {r.evidence}")

    if checked is not None:
        print(f"\npeople-model findings re-checked: {checked}")
    if CAUSES_PATH.exists():
        evaluate(inc_df, B, pd.read_csv(CAUSES_PATH))


FINDING_FAMILY = {"fatigue": "speed", "pace": "speed", "retries": "quality", "quality": "quality",
                  "technique": "pattern"}


def cross_check_people(con, inc_df):
    """Tell the people model whether the cause finder agrees a flagged shift is about that person."""
    has = con.execute("SELECT name FROM sqlite_master WHERE name='operator_shifts'").fetchone()
    if not has:
        return None
    cols = [r[1] for r in con.execute("PRAGMA table_info(operator_shifts)")]
    if "cause_check" not in cols:
        con.execute("ALTER TABLE operator_shifts ADD COLUMN cause_check TEXT")
    shifts = pd.read_sql("SELECT operator_id, shift_date, shift, finding_type FROM operator_shifts WHERE flag = 1", con)
    counts = {"confirmed": 0, "other cause": 0, "no incident": 0}
    upd = []
    for r in shifts.itertuples():
        fam = FINDING_FAMILY.get(r.finding_type)
        if fam is None:
            continue
        g = inc_df[(inc_df.shift_date == r.shift_date) & (inc_df["shift"] == r.shift) & (inc_df.family == fam)]
        mine = g[(g.cause == "people") & (g.culprit == r.operator_id)]
        other = g[g.cause.isin(["machine", "method", "station"])]
        if len(mine):
            text, k = f"confirmed: the symptom follows {r.operator_id} (incident #{mine.incident_id.iloc[0]})", "confirmed"
        elif len(other):
            o = other.iloc[0]
            text, k = (f"careful: in this shift the cause finder points to {o.cause} - {o.culprit} "
                       f"(incident #{o.incident_id}), not this person"), "other cause"
        else:
            text, k = "no shift-level incident - a single-person pattern below the incident threshold", "no incident"
        counts[k] += 1
        upd.append((text, r.operator_id, r.shift_date, r.shift))
    con.executemany("UPDATE operator_shifts SET cause_check=? WHERE operator_id=? AND shift_date=? AND shift=?", upd)
    return ", ".join(f"{v} {k}" for k, v in counts.items())


def evaluate(inc_df, B, causes):
    """Only for simulated data: compare with the planted root causes."""
    rows = []
    for r in inc_df.itertuples():
        lab, sid = match_scenario({"family": r.family, "bids": r.bid_list}, B, causes)
        rows.append((sid, lab, r.cause, r.culprit))
    m = pd.DataFrame(rows, columns=["scenario", "true", "pred", "culprit"])
    linked = m[m.true != "none"]
    acc = (linked.true == linked.pred).mean() if len(linked) else float("nan")
    print(f"\nvs planted root causes: {len(linked)} of {len(m)} incidents belong to one; cause right for {acc:.0%}")
    for sc in causes.itertuples():
        g = m[m.scenario == sc.scenario_id]
        if g.empty:
            print(f"    {sc.scenario_id:<3} {sc.cause:<8} {sc.family:<8} NOT FOUND   {sc.description}")
            continue
        pred = g.pred.mode().iloc[0]
        ok = "ok " if pred == sc.cause else "WRONG"
        cul = g.culprit.mode().iloc[0]
        cul_ok = "culprit ok" if str(sc.culprit).split()[-1] in cul else f"culprit said: {cul}"
        print(f"    {sc.scenario_id:<3} {sc.cause:<8} {sc.family:<8} {ok} ({len(g)} incident(s), {cul_ok})  {sc.description}")


if __name__ == "__main__":
    main()
