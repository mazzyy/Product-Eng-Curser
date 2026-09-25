# Production Engineer Copilot - prototype

**Goal:** a "Cursor for production engineers" on a Giga-style vehicle line. Instead of a
dashboard full of numbers, the engineer gets the few things that need a look, **why** they
happened, and a sentence explaining it.

This prototype uses 2 stations, 1 table per station, and 3 small models:

- **Signal checker:** what looks wrong in the data.
- **People model:** what looks wrong in the human work.
- **Cause finder:** why it happened - machine, people, method or station.

See `PROGRESS.md` for what was built when, and the full roadmap (models 0-9).

## Run it (MacBook, about 30 seconds in total)

```bash
cd "Product Eng Curser "
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python generate_data.py        # simulate the demo week -> data/factory.db (+ answer keys)
python train_model.py          # signal checker   -> anomaly_* columns
python train_people_model.py   # people model     -> human_* columns + operator_shifts table
python train_cause_model.py    # cause finder: learn from 40 simulated weeks (about 15 s)
python find_causes.py          # cause finder     -> incidents table + incident_id column
python plot_station.py && python plot_people.py && python plot_causes.py   # charts/
```

## The database

`data/factory.db` (SQLite) has:

- **One table per station** (`st012`, `st013`), with the same columns everywhere. A row is
  one event (cycle, fault or maintenance). It holds the machine side and the human side of the
  work, plus what every model found about it.
- **Two summary tables** that the models rebuild: `operator_shifts` and `incidents`.

| Column group | Columns |
|---|---|
| When / what | `event_id`, `ts`, `shift`, `event_type`, `vin`, `model`, `work_instruction`, `part_batch`, `cycle_time_s`, `machine_time_s` |
| Human cycle | `operator_id`, `operator_skill`, `operator_time_s`, `wait_time_s`, `retries`, `andon_pulled`, `andon_response_s` |
| Process | `primary_name/value/unit/lsl/usl`, `secondary_name/value/unit/lsl/usl`, `temperature_c` |
| Outcome | `result`, `fault_code`, `downtime_s`, `comment` |
| Signal checker | `anomaly_score`, `anomaly_flag`, `anomaly_type`, `anomaly_reason` |
| People model | `human_score`, `human_flag`, `human_type`, `human_reason` |
| Cause finder | `incident_id` -> links the cycle to its row in `incidents` |

| Station | What it does | primary | secondary |
|---|---|---|---|
| ST012 | Front subframe bolt-down (nutrunner) | torque, Nm | angle, deg |
| ST013 | Coolant fill & leak test (after ST012) | leak rate, sccm | fill volume, L |

The simulated crews are A, B and C, each with 4 operators on 8-hour shifts. They rotate every
2 hours from ST013 to ST012. Work-instruction versions and part batches change during the week,
and machines get repaired. All data is synthetic.

## Model 1 - signal checker (`train_model.py`)

It finds data that contradicts itself or doesn't fit the normal pattern:

- **label_conflict:** OK but out of spec, or NOK with no reason.
- **traceability:** missing or double VIN, or a car never seen upstream.
- **sensor:** a stuck value or a dropout.
- **unlogged_event:** a slow or fast cycle nothing explains.
- **pattern:** in spec but off the pattern, or drifting.

How: rules plus an Isolation Forest per station.

## People model (`train_people_model.py`)

**Every cycle:**

- rushed (a possible skipped step)
- struggle (slow, no andon pulled)
- retry bursts
- andon calls with no response
- stale logins
- work outside the operator's own shift

**Every operator-shift, compared with peers:**

- fatigue
- technique
- retries
- quality
- pace

Pace is measured against others on the same station and shift, so a station-wide change
(a new instruction, a slow machine) doesn't get blamed on people. Findings are worded to support
people, not rank them.

## Model 2 - cause finder (`cause_finder.py`, `train_cause_model.py`, `find_causes.py`)

**Question:** the same symptom (more re-hits, slower cycles, a shifted signal, VIN errors) can
come from four causes. Which one is it, and who or what exactly?

| Cause | What it covers | Evidence that gives it away |
|---|---|---|
| machine | tool, sensor, scanner, fixture | every operator on that station; builds up over time; stops after a repair |
| people | one person | follows the operator, including to the other station after rotation |
| method | the work instruction | starts when a new version goes live; every operator and crew |
| station | inputs and surroundings: part batch, supply, IT/MES | starts and stops with a batch; waiting only; both stations at once |

**How it works**

1. **Blocks:** the week is cut into 2-hour rotation blocks. In one block, one operator works one
   station under one instruction version, so each block is a small natural experiment.
2. **Symptoms:** 10 KPIs per block (re-hits, NOK, hands-on time, machine time, waiting,
   signal level, signal offset, no VIN, double VIN, missing measurement) are compared with the
   station's healthy baseline. That baseline is estimated from the lower part of the
   distribution, so a problem that lasts days doesn't become "normal".
3. **Incidents:** symptomatic blocks of the same symptom family in the same shift form one incident.
4. **Evidence graph:** each incident is linked to its candidate culprits - the operator, the
   machine, the instruction version, the batch, and supply/IT. The rest of the week decides how
   strong each link is (35 evidence features).
5. **Learned classifier:** a random forest trained on 40 simulated weeks where the true cause is
   known. The simulator plants the same symptoms with different causes on purpose.
6. **Output:** cause, confidence (below 50% it says "unclear"), culprit, plain-language
   evidence, the graph, and cases (the same cause and culprit across shifts).

It also re-checks the people model. For each operator-shift flagged for fatigue, technique,
retries, quality or pace, `operator_shifts.cause_check` says whether the cause finder agrees it
is that person, or whether a machine, method or station cause was active instead.

**Results**

- **Weeks it never saw** (8 simulated weeks): 95% of incidents get the right cause; 94% of the
  planted root causes are detected.
- **Demo week:** all 11 planted root causes found, each with the right cause and culprit.

| Case | Cause | Culprit | Evidence |
|---|---|---|---|
| Re-hits at ST012 Mon afternoon | station | bolt batch BL-4471 | starts and stops with the batch, all operators |
| Re-hits every night, both stations | people | OP-C1 | 86% of their blocks vs 3% for others; follows them to ST013 |
| Hands-on time +23% at ST012 Tue-Wed | method | WI-012 v4 | started 0 h after v4 went live; every crew |
| Angle off pattern every afternoon | people | OP-B2 | 100% of their blocks vs 5% for others |
| Torque drifting Thu morning | machine | Nutrunner NR-012 | every operator; stopped after "Nutrunner recalibrated" |
| No VIN on both stations Wed 10:00 | station | MES / line network | both stations at the same time |
| Double VIN scans from Sat | method | WI-013 v8 | started with v8, every crew |

**Honest caveat:** the simulator makes each cause leave a clean fingerprint. Real data will be
messier, and the classifier should be re-trained on real incidents labelled by engineers.

## Handy queries

```sql
-- all incidents of the week with cause and culprit
SELECT incident_id, case_id, shift_date, shift, symptom, cause, confidence, culprit, evidence
FROM incidents ORDER BY start_ts;

-- every cycle behind one incident
SELECT * FROM st012 WHERE incident_id = 4;

-- people findings the cause finder does NOT attribute to the person
SELECT operator_id, shift_date, finding_type, cause_check FROM operator_shifts
WHERE cause_check LIKE 'careful%';

-- signal-checker flags that the cause finder could explain
SELECT s.ts, s.anomaly_reason, i.cause, i.culprit
FROM st013 s JOIN incidents i USING (incident_id) WHERE s.anomaly_flag = 1;
```

## Using people data responsibly

- **Purpose:** the people findings are for support (coaching, tools, workload, team-lead
  response, login process). They are not for ranking or discipline.
- **Legal (Germany):** before using real operator data, involve the works council (Betriebsrat)
  and the data protection officer (GDPR).

## Files

| File | Purpose |
|---|---|
| `station_db.py` | Table pattern, `operator_shifts` and `incidents` tables, station config |
| `generate_data.py` | Line simulator: demo week or random weeks; root-cause scenarios; answer keys |
| `train_model.py` | Signal checker |
| `train_people_model.py` | People model |
| `cause_finder.py` | Cause finder logic: blocks, KPIs, incidents, evidence graph, explanations |
| `train_cause_model.py` | Trains the cause finder on simulated weeks, with held-out test |
| `find_causes.py` | Runs the cause finder on `factory.db`, writes incidents, re-checks people findings |
| `plot_station.py`, `plot_people.py`, `plot_causes.py` | Charts in `charts/` |
| `data/injected_*.csv` | Answer keys from the simulator, used only for the evaluation printouts |
| `PROGRESS.md` | Step-by-step log of what was built and how |
