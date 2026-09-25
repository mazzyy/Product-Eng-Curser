# Progress log - Production Engineer Copilot

This file records what was built, how, and where it stands. A new step is added at the
bottom every time we work on the project, so you can see what was done at each point.

## Where we are

| # | Model | Job in one line | Status | Files |
|---|---|---|---|---|
| 0 | Line simulator | Dummy Tesla-style data + the true answers | **Built for 2 stations**: crews, rotation, WI versions, batches, repairs, 16 root-cause scenario types, demo week + random training weeks. Missing: full line, 45 s takt, notes text, maintenance history | `generate_data.py`, `station_db.py` |
| 1 | Signal checker | Real drop or noise? | Mostly built at row level (rules + ML); block-level KPI z-scores now exist in the cause finder | `train_model.py` |
| 2 | Cause finder | Method, people, station or machine? | **v1 built (step 3)**: 95% on held-out weeks, 11/11 on the demo week | `cause_finder.py`, `train_cause_model.py`, `find_causes.py`, `plot_causes.py` |
| 3 | Impact ranker | What matters most, bottleneck, 7,500? | Not started | - |
| 4 | Floor listener | Handover notes -> structured data | Not started | - |
| 5 | Method checker | Fits 45 s, safe, trained? | Groundwork only (standard times, andon and rest rules, WI versions in data) | - |
| 6 | Change manager | Versions, approval, rollout, after-check | Groundwork only (WI version per cycle) | - |
| 7 | Containment | Which cars to hold, stop or not | Groundwork only (suspect cars flagged; incidents link to cars) | - |
| 8 | Maintenance predictor | How often to check each machine | Not started (repairs + drift now in the data) | - |
| 9 | Agent | Uses all the others as tools | Not started (database built to be agent-readable) | - |

Also built: people model (`train_people_model.py`), charts (`plot_station.py`, `plot_people.py`, `plot_causes.py`).

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

### Step 3 - 2026-09-26 - Cause finder (model 2) v1

**Goal:** for every problem, answer *why* - machine, people, method or station - and name the
culprit. The model should be learned, with an evidence graph.

**Cause definitions (agreed 2026-09-26):**

| Cause | What it covers | Evidence it leaves |
|---|---|---|
| machine | The equipment: tool, sensor, scanner, fixture | Stays at one station for every operator; builds up over time; stops after a repair |
| people | One person: technique, fatigue, training | Follows the operator to the next station after rotation; others are fine |
| method | The work instruction itself | Starts when a new instruction version goes live; hits every operator and crew |
| station | The station's inputs and surroundings: part batch, supply, IT/MES | Starts and stops with a batch or supply gap; can hit both stations at once |

**What we did**

1. **Simulator v2 (model 0)**
   - New columns: `work_instruction`, `part_batch`, `machine_time_s`, `incident_id`.
   - New events: unplanned repairs, with decoy repairs and harmless decoy instruction changes.
   - 16 root-cause scenario types: 4 causes x 4 symptom families (quality, speed, pattern,
     data). The same symptom can come from any cause.
   - A fixed demo week with 11 root causes, and random weeks for training. True causes are
     saved to `data/injected_causes.csv`.
   - `build_week()` makes a week in memory, so training can simulate many weeks.
   - The old fixed people plan (fatigue, technique, retries) and the old drift are now
     scenarios in the same system.
2. **Cause finder** (`cause_finder.py`)
   - 2-hour rotation blocks, with 10 KPIs per block.
   - Robust z-scores: a lower-tail baseline for "only goes up" KPIs, count data stabilised,
     and a temperature-compensated signal level.
   - Incidents are grouped per symptom family and shift.
   - 35 evidence features, forming an evidence graph that links each incident to its candidate
     culprits.
   - Plain-language symptom, culprit and evidence text.
3. **Training** (`train_cause_model.py`)
   - 40 random simulated weeks (about 650 incidents), run in parallel.
   - Random forest; the last 20% of weeks are held out for testing, then it is refit on all
     weeks and saved.
4. **Running** (`find_causes.py`)
   - Writes the `incidents` table (cause, confidence, culprit, evidence, graph, block list,
     case_id) and `incident_id` on every cycle of an incident.
   - Below 50% confidence the cause is "unclear".
   - Re-checks the people model's findings (`operator_shifts.cause_check`).
5. **Chart** (`plot_causes.py`): timeline of incidents coloured by cause, held-out confusion
   matrix, and the evidence the model leans on most.

**Fixes to earlier models found along the way**

- **People model:** pace and fatigue are now measured against others on the same station and
  shift. Before, the new work instruction WI-012 v4 made *every* operator look "fatigued" on
  Tuesday. People model operator-shift precision went from 79% to 100%.
- **People model naming:** the finding "method" was renamed to **"technique"**, so it isn't
  confused with the cause "method" (the work instruction).
- **Simulator:** operator retry spread was narrowed, and forced rejects now keep physics
  consistent (angle follows torque).

**How - key decisions**

- **Blocks** follow the 2-hour rotation, because rotation is what separates people from
  machine: the symptom either moves with the person or stays with the station.
- **Healthy baseline** from the lower tail of each KPI, because a problem that lasts two days
  would otherwise become the "normal".
- **Evidence alignment features** (starts at a version change, ends at a repair, starts and ends
  with a batch) turned out to be the strongest and most robust signals.
- **Training scenarios:** people problems may overlap other problems, because the demo week
  showed that clean, non-overlapping training was too easy.
- **Parallel simulation:** training uses ProcessPoolExecutor and was tested with macOS-style
  "spawn".

**Result**

- **Held-out weeks:** 95% of incidents correct (machine 100%, people 97%, method 92%, station
  95% recall); 94% of planted root causes detected; 90% get the right cause by majority vote.
- **Demo week:** 31 incidents in 11 cases, all 11 planted root causes correct, with the right
  culprit.
- **Cross-check:** all 16 people-model shift findings were confirmed by the cause finder.
- **Speed:** the whole pipeline takes about 25 s; training takes about 15 s.
- **Other models on the new demo week:** the signal checker has precision 95%/83% and recall
  81%/98% (ST012/ST013). Most of its "not planted" flags are real effects of the root causes,
  and they now link to an incident.
- **Tested on:** Python 3.10 with pandas 2.3 and sklearn 1.7, and Python 3.11 with pandas 3.0
  and sklearn 1.8.

**Limits / next ideas**

- The simulated fingerprints are clean; real data needs labelled incidents to re-train.
- Only 2 stations. An upstream-station cause (ST012 causing ST013 defects) needs more of the line.
- Incidents are grouped per shift. Cases merge them afterwards, but a multi-day incident object
  would be cleaner.
- **Next:** model 3 (impact ranker) can use `incidents` directly: how many cars and minutes each
  case cost, and what that means for the 7,500 target.

**Run:** `python generate_data.py && python train_model.py && python train_people_model.py &&
python train_cause_model.py && python find_causes.py && python plot_causes.py`

**Note:** the folder is now a git repo (your commit `c363237` on 2026-09-26 01:30). The final
fixes of step 3 are not committed yet.
