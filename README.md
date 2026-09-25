# Production Engineer Copilot - station + people prototype

**Goal:** a "Cursor for production engineers" on a Giga-style vehicle line. Instead of a
dashboard full of numbers, the engineer gets the few rows that need a look, with a sentence
explaining why. The prototype is small on purpose: **2 stations, 1 table each, 2 simple models
(machine and people), 3 charts.**

## The database: one table per station, same pattern everywhere

`data/factory.db` (SQLite). Each station has one table (`st012`, `st013`) with the **same
columns**. A row is one event at that station: a production cycle, a fault, or a maintenance
stop. It holds both the machine side and the human side of the work, plus what both models found.

| Column group | Columns |
|---|---|
| When / what | `event_id`, `ts`, `shift`, `event_type` (CYCLE / FAULT / MAINTENANCE), `vin`, `model`, `cycle_time_s` |
| **Human cycle** | `operator_id`, `operator_skill` (1-4), `operator_time_s` (hands-on time), `wait_time_s`, `retries`, `andon_pulled`, `andon_response_s` |
| Process | `primary_name/value/unit/lsl/usl`, `secondary_name/value/unit/lsl/usl`, `temperature_c` |
| Outcome | `result` (OK / NOK), `fault_code`, `downtime_s`, `comment` |
| Station model | `anomaly_score`, `anomaly_flag`, `anomaly_type`, `anomaly_reason` |
| People model | `human_score`, `human_flag`, `human_type`, `human_reason` |

The only other table is **`operator_shifts`**: one summary row per operator per shift (pace,
slowdown, retry rate, NOK rate, method offset, counts, peer score, finding). The people model
rebuilds it each run.

| Station | What it does | primary | secondary | Hands-on standard |
|---|---|---|---|---|
| **ST012** | Front subframe bolt-down (nutrunner) | torque, Nm (110-130) | angle, deg (35-75) | 30 s of a 47 s cycle |
| **ST013** | Coolant fill & leak test (after ST012) | leak_rate, sccm (0-2) | fill_volume, L (9.2-9.8) | 18 s of a 44 s cycle |

The simulated crews are A, B and C, each with 4 operators on 8-hour shifts. They rotate every
2 hours, and whoever ran ST013 moves to ST012. Operator IDs are pseudonymous (`OP-B2`). To add
ST014, you add one entry to `STATIONS` in `station_db.py`.

## Model 1 - station (`train_model.py`)

It finds rows where the machine or process data doesn't tell one consistent story.

| anomaly_type | Examples | Found by |
|---|---|---|
| `label_conflict` | OK but torque out of spec; NOK with everything in spec and no code | rules |
| `traceability` | no VIN, same VIN twice, VIN at ST013 never seen at ST012 | rules |
| `sensor` | reading stuck on one value, measurement missing | rules |
| `unlogged_event` | fault with no code; slow or fast cycle nothing in the log explains | rules + model |
| `pattern` | in spec, but angle doesn't fit torque; slow drift | model |

- **How it works:** an Isolation Forest per station on 4 features, plus a 6-sigma guard. The
  features are primary level, distance from the normal primary/secondary line, cycle time
  after subtracting logged retries, and a rolling baseline.
- **Left alone on purpose:** correctly rejected parts, and slow cycles explained by an andon call.

## Model 2 - people (`train_people_model.py`)

It uses the same idea for the human side. Every finding is worded as something to check or
help with, not a score to rank people.

**Every cycle** (`human_*` columns):

| human_type | Meaning | Found by |
|---|---|---|
| `rushed` | far faster than *this operator's* normal, so a step may have been skipped | Isolation Forest + guard |
| `struggle` | far slower than their normal and no andon pulled, so they may have needed help | Isolation Forest + guard |
| `retries` | 4 or more re-hits in one cycle: part fit, tool or technique | Isolation Forest + guard |
| `support` | andon pulled but the team lead took more than 3 minutes. A support gap, not the operator | rule |
| `login` | same operator logged in at two stations at once (stale login at rotation) | rule |
| `working_time` | working outside their crew's shift, with rest time (flags under 11 h) | rule |

The cycle features are hands-on time against the operator's **own** baseline at that station,
hands-on time against the station standard, and retries. Comparing people with their own
normal avoids penalising someone who is always a bit slower.

**Every operator-shift** (`operator_shifts` table): each shift is compared with all others
(robust z-score of 4 or more).

- `fatigue`: slows down over the shift, for example +32% by the end when the typical drift is +3%.
- `method`: the machine signature of their work is shifted. At ST012 the angle for a given
  torque sits 1.2 sigma lower on every cycle. Every part is in spec, so no single cycle looks
  wrong, but the whole shift does.
- `retries`, `quality` (NOK rate), `pace`: well above or below peers.
- The cycle-level counts are included too: `rushing`, `support`, `login`, `working_time`.

## Results on the generated week (about 11,500 cycles per station, 12 operators, 85 operator-shifts)

| Model | Precision | Recall |
|---|---|---|
| Station ST012 / ST013 | 86% / 90% | 96% / 87% |
| People, every cycle | 93% | 99% |
| People, every operator-shift | 93% | 96% |

- **Station model:** every rule type is found 100%. Drift is found about 70-95%, because its
  first cycles look normal by design.
- **People model:** both fatigue shifts and all 7 method-deviation shifts are found. The
  high-retry operator is found in 6 of 7 shifts.
- **What "precision" means here:** most of the other flags are real behaviour that wasn't
  planted, such as slow cycles on the fatigued shifts.

## Run it (MacBook, under 10 seconds in total)

```bash
cd "Product Eng Curser "
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python generate_data.py        # rebuild data/factory.db (7 days, 2 stations, 12 operators)
python train_model.py          # station model  -> anomaly_* columns
python train_people_model.py   # people model   -> human_* columns + operator_shifts table
python plot_station.py         # charts/st012.png, charts/st013.png
python plot_people.py          # charts/people.png
```

## Handy queries

```sql
-- what needs a look at ST012 (machine side), worst first
SELECT ts, vin, anomaly_type, anomaly_reason FROM st012
WHERE anomaly_flag = 1 ORDER BY anomaly_score DESC LIMIT 20;

-- operator-shifts worth a supportive conversation
SELECT operator_id, shift_date, shift, finding_type, finding
FROM operator_shifts WHERE flag = 1 ORDER BY shift_date;

-- one vehicle, both stations, machine + human view (5 re-hits at ST012, then rushed at ST013)
SELECT 'ST012' st, ts, operator_id, operator_time_s, retries, result, anomaly_reason, human_reason
FROM st012 WHERE vin = 'XP7YSIM0106442'
UNION ALL
SELECT 'ST013', ts, operator_id, operator_time_s, retries, result, anomaly_reason, human_reason
FROM st013 WHERE vin = 'XP7YSIM0106442';

-- how fast do team leads answer the andon, per shift
SELECT shift, COUNT(*) calls, ROUND(AVG(andon_response_s)) avg_s, SUM(andon_response_s > 180) late
FROM st012 WHERE andon_pulled = 1 GROUP BY shift;
```

## Using people data responsibly

- **Purpose:** the people model is meant to find where the line should support people
  (coaching, tools, workload, team-lead response, logins). It is not for ranking or disciplining
  individuals.
- **Findings:** keep IDs pseudonymous, show findings to the team lead and operator together,
  and treat each finding as a question, not a verdict.
- **Legal (Germany):** a system like this touches worker-monitoring rules. Before using it with
  real people data, involve the works council (Betriebsrat co-determination) and your data
  protection officer (GDPR).

## Files

| File | Purpose |
|---|---|
| `station_db.py` | Table pattern, `operator_shifts` table, station config |
| `generate_data.py` | Synthetic week: machine data, crews, rotation, operator profiles, injected problems |
| `train_model.py` | Station model (rules + Isolation Forest) |
| `train_people_model.py` | People model (cycle rules + Isolation Forest, operator-shift peer comparison) |
| `plot_station.py` / `plot_people.py` | Charts |
| `data/injected_labels.csv`, `data/injected_people_labels.csv` | Ground truth for the evaluation printouts only |

All data is synthetic (VINs start with `XP7YSIM`, operators are `OP-<crew><n>`).
