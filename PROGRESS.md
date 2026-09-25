# Progress log - Production Engineer Copilot

This file records what was built, how, and where it stands. A new step is added at the
bottom every time we work on the project, so you can see what was done at each point.

## Where we are

| # | Model | Job in one line | Status | Files |
|---|---|---|---|---|
| 0 | Line simulator | Dummy Tesla-style data + the true answers | Partly built: 2 stations, 1 week, crews, rotation | `generate_data.py`, `station_db.py` |
| 1 | Signal checker | Real drop or noise? | Mostly built at row level (rules + ML); no line-level KPI test yet | `train_model.py` |
| 2 | Cause finder | Method, people, station or machine? | **In progress (step 3)** | - |
| 3 | Impact ranker | What matters most, bottleneck, 7,500? | Not started | - |
| 4 | Floor listener | Handover notes -> structured data | Not started | - |
| 5 | Method checker | Fits 45 s, safe, trained? | Groundwork only (standard times, andon and rest rules) | - |
| 6 | Change manager | Versions, approval, rollout, after-check | Not started | - |
| 7 | Containment | Which cars to hold, stop or not | Groundwork only (suspect cars flagged) | - |
| 8 | Maintenance predictor | How often to check each machine | Not started | - |
| 9 | Agent | Uses all the others as tools | Not started (database built to be agent-readable) | - |

Also built: people model (`train_people_model.py`), charts (`plot_station.py`, `plot_people.py`).

---

## Log

### Step 1 - 2026-09-25 - Simple database + signal checker (models 0 and 1)

**Goal:** replace a multi-table setup with one simple table per station and find noisy or
ambiguous data with a small ML model that trains on a MacBook.

**What we did**
- Designed one SQLite table per station (`st012`, `st013`) with the same columns everywhere.
  Each row is one event (cycle, fault or maintenance). What each signal means (torque, angle,
  leak rate...) is written in the row, so any station can be read the same way.
- Built a line simulator for 1 week and 2 stations: ST012 subframe bolt-down (torque/angle) and
  ST013 coolant fill and leak test (leak rate/fill volume). ST013 receives ST012's cars.
- Planted known problems and saved them as ground truth (`data/injected_labels.csv`).
- Built the signal checker (`train_model.py`):
  - rules for data that contradicts itself;
  - an Isolation Forest per station on 4 features;
  - results written back into the same table.
- Added one chart per station (`plot_station.py`).

**How**
- Tools: Python, pandas, scikit-learn, matplotlib, SQLite.
- The Isolation Forest is trained only on clean-looking cycles.
- Parts that were correctly rejected are not flagged.

**Result:** precision about 90% and recall about 90% on planted problems; every rule-type
problem was found. Trains in about 0.5 s per station.

**Decisions:** SQLite (zero setup); 2 stations only; the cause of each flag is written in plain
language.

### Step 2 - 2026-09-25 - Human cycle + people model

**Goal:** do for people what step 1 did for stations: find unusual patterns in the operators' work.

**What we did**
- Added human-cycle columns to the same station tables: `operator_id`, `operator_skill`,
  `operator_time_s`, `wait_time_s`, `retries`, `andon_pulled`, `andon_response_s`.
- Simulated crews A/B/C with 4 operators each, 8-hour shifts and 2-hour rotation
  (ST013 then ST012). Each operator has a personal profile.
- Built the people model (`train_people_model.py`) at two levels:
  - **Every cycle:** rules for andon calls with no response, stale logins, and work outside the
    operator's shift. An Isolation Forest compares hands-on time with that person's own normal,
    which finds rushed cycles, struggle cycles and retry bursts.
  - **Every operator-shift:** new table `operator_shifts`, comparing each shift against its
    peers on fatigue, technique, retries, quality and pace.
- Fixed the station model: it now ignores slow cycles that an andon call explains, subtracts
  logged retries from cycle time, and has a 6-sigma guard.
- Added `plot_people.py`.

**Result:**
- Every cycle: precision 93%, recall 99%.
- Every operator-shift: precision 93%, recall 96%.
- It found OP-B2's different technique (all parts in spec), fatigue for OP-C4 and OP-B4,
  and OP-C1's high retries.

**Decisions:** findings are worded to support people, not rank them. There is a note on works
council and GDPR in the README.

### Step 3 - 2026-09-26 - Cause finder (model 2) - IN PROGRESS
Planned: root-cause scenarios in the simulator, then a learned classifier plus an evidence
graph that answers machine / people / method / station for every incident.

**Cause definitions (agreed 2026-09-26):**

| Cause | What it covers | Evidence it leaves |
|---|---|---|
| machine | The equipment: tool, sensor, scanner, fixture | Stays at one station for every operator; builds up over time; stops after a repair |
| people | One person: technique, fatigue, training | Follows the operator to the next station after rotation; others are fine |
| method | The work instruction itself | Starts when a new instruction version goes live; hits every operator and crew |
| station | The station's inputs and surroundings: part batch, supply, IT/MES | Starts and stops with a batch or supply gap; can hit both stations at once |
