# Production Engineer Copilot - prototype

**Goal:** a "Cursor for production engineers" on a Giga-style vehicle line. Instead of a
dashboard full of numbers, the engineer gets the few things that need a look, **why** they
happened, and a sentence explaining it.

This prototype uses 2 stations, 1 table per station, and 9 small models:

- **Signal checker:** what looks wrong in the data.
- **People model:** what looks wrong in the human work.
- **Cause finder:** why it happened - machine, people, method or station.
- **Impact ranker:** what matters most - High, Medium or Low - and can we hit 7,500 cars a week.
- **Floor listener:** what the shift notes and supervisors say, as structured facts (LLM: Azure GPT-5).
- **Method checker:** does a work instruction fit the 50 s takt, is it safe, are people trained.
- **Change manager:** no change reaches the line without a check and an approval; the next shift
  always knows the current method.
- **Containment:** which cars wait for a check, which move on, and whether to recommend a stop.
- **Maintenance predictor:** how often each machine should be checked, and what is due next.

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
python impact_ranker.py        # impact ranker    -> impacts, impact_catalog, impact_summary tables
python method_data.py          # WI steps, qualifications, sign-offs for the method checker
python method_checker.py       # method checker   -> method_checks, method_verdicts tables
python method_checker.py --propose proposals/*.json   # check new versions before rollout
python generate_notes.py       # shift notes + supervisor answers -> floor_notes table
python floor_listener.py       # floor listener   -> floor_facts table (Azure GPT-5, or offline rules)
python maintenance.py          # maintenance predictor -> maint_plan, maint_history tables
python containment.py          # containment      -> containment_cases, car_holds tables
python change_manager.py demo  # change manager   -> changes, change_events (replay + example proposals)
python plot_station.py && python plot_people.py && python plot_causes.py && python plot_impacts.py   # charts/
python plot_method.py && python plot_floor.py
```

## The database

`data/factory.db` (SQLite) has:

- **One table per station** (`st012`, `st013`), with the same columns everywhere. A row is
  one event (cycle, fault or maintenance). It holds the machine side and the human side of the
  work, plus what every model found about it.
- **Summary tables** that the models rebuild: `operator_shifts`, `incidents`, and the impact tables
  `impacts`, `impact_catalog` and `impact_summary`.
- **Method tables:** `work_instructions`, `wi_steps`, `qualifications`, `wi_signoffs` (input) and
  `method_checks`, `method_verdicts`, `method_eval` (output).
- **Floor tables:** `floor_notes` (free text) and `floor_facts` (what the listener extracted).
- **Change tables:** `changes`, `change_events`. **Containment:** `containment_cases`, `car_holds`.
  **Maintenance:** `maint_plan`, `maint_history`.

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

## Model 3 - impact ranker (`impact_ranker.py`)

**Question:** of everything the other models found, what matters most, and can we hit 7,500
cars a week?

It doesn't re-analyse the raw data. It takes the cause-finder cases, the signal-checker flags
that aren't part of an incident, the people-model findings, and faults and repairs. That gives
49 impact items this week, which it ranks.

**1. Four measurements per item**

| Measure | What it counts |
|---|---|
| **R** risk cars | Affected cars, weighted by how likely each is not OK without anyone knowing. Weight 1.0: known bad (passed OK although out of spec), no VIN, double scan, no valid measurement, no upstream record ("definite" cars). Weight *m*: built while the signal was shifted, where *m* = share of the safety margin used up. Lower weights: possible skipped step 0.5, stale login 0.3, rejected with no reason 0.2, 3+ re-hits or reworked 0.1. |
| **L** lost cars | Extra seconds at the bottleneck (ST012, 50 s/car) divided by its cycle time. Other stations count only beyond their spare time; they recover a stop within about an hour. |
| **C** rework hours | 20 min per extra reject, 10 min per car to verify, 10 min per fault with no code, fault and repair time. |
| **P** people hours | Hours worked under strain: fatigue, re-hit bursts, waiting for help, working without 11 h rest. |

**2. Sub-scores 0-10 (log scale)**

- SQ = S/10 × 10·log(1+R)/log(101)
- DL = 10·log(1+L)/log(76)
- CO = 10·log(1+C)/log(41)
- PE = 10·log(1+P)/log(9)

S is the severity of the station's key characteristic: ST012 = 9 (safety-critical joint), ST013 = 8
(EV coolant). The reference points are 100 risk cars; 75 lost cars (1% of 7,500); 40 h (a
person-week); 8 h (a full shift).

**3. Score 0-100** = 10 × (0.40·SQ + 0.30·DL + 0.15·CO + 0.15·PE): safety & quality > delivery >
cost, people.

**4. Future**

- **Still active** at the end of the data (no repair, no new instruction version, batch still in
  use, seen in the last 24 h): projected over the next 7 days.
- **Recurring** (seen on 3+ days): repeats next week.
- **Priority** = max(score now, score next week).

**5. Category**

- **High** from 50, **Medium** from 25, **Low** below.
- **Hard rules** make an item High regardless of score:
  - known-bad cars passed on a station with S ≥ 8;
  - definite cars ≥ 10·3^(9−S) (10 cars at S=9, 30 at S=8);
  - a safety-critical signal used ≥ 30% of its margin;
  - ≥ 75 lost cars;
  - a legal rest-time breach.
- **Medium at least** if S ≥ 8 and there are any risk cars.

**Also**

- **Can we hit 7,500?** Capacity is 11,881 cars (bottleneck ST012). 11,241 were built (150%), 698
  were lost to the ranked problems, and next week is projected at ~11,617. The simulated line runs
  24/7; on a 5-day week the same daily rate is 8,041 (107%).
- **Could happen next** (`impact_catalog`): 16 problem types × 2 stations scored with the same
  formula at a typical size. The worst are a failing VIN scanner and a calibration drift on the
  safety-critical joint (High).

**This week's top items**

| # | Item | Category | Why |
|---|---|---|---|
| 1 | Nutrunner NR-012 torque drift | High 64 | used 46% of the safety margin on a safety-critical joint (fixed) |
| 2 | MES / line network outage | High 49 | 136 cars with no trusted VIN record at a safety-critical station |
| 3 | WI-013 v8 double scans | High 47 | still active - ~780 cars would need checks next week |
| 4 | 17 andon calls unanswered | High 37 | 99 lost cars - a team-lead coverage gap |
| 5 | WI-012 v4 extra step | High 30 | 252 lost cars (3.4% of target), fixed with v5 |

The people cases are Medium: OP-B2 (technique), OP-C1 (re-hits), and OP-B4/OP-C4 (fatigue).
The weights, reference points and thresholds are constants at the top of `impact_ranker.py`, so
they can be tuned.

## Model 4 - floor listener (`floor_listener.py`)

Turns shift handover notes, maintenance log entries and supervisor answers into structured facts,
then puts them next to what the data found.

- **Input:** `floor_notes`. For the prototype, `generate_notes.py` writes 31 notes for the demo week
  the way team leads write them: short, some German ("Nacharbeit", "Störung"), times like "around 3",
  operator codes like "C1". It also writes 7 answers to questions the copilot asks the supervisor about
  incidents. An answer key lists every fact in every note.
- **Parse:** each note goes to **Azure OpenAI GPT-5** (Responses API, strict JSON schema). Each fact has
  station, time, category (machine, method, people, material, supply, it, safety, other), subject ID,
  symptom, action, status (open, monitoring, fixed, info) and severity. Supervisor answers also get
  confirms (yes / no / unclear), the cause the supervisor believes, and the decision.
- **No key?** An offline keyword parser runs instead, so the prototype always works. LLM answers are
  cached in `data/llm_cache.json`. Once all notes are cached, the GPT-5 results replay without a key.
- **Link:** each fact is matched to the cause finder's incidents by station, shift and cause:
  - *agrees* - the note and the data point to the same cause;
  - *early warning* - the floor wrote it before the data found it;
  - *notes only* - only people saw it (safety, aisles, harmless changes);
  - *disputes* / *unclear* - the supervisor disagrees with the cause finder, or doesn't know.

**Set up Azure:** copy `.env.example` to `.env` and paste the key. `.env` is in `.gitignore`.

```
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://tasting-resource.services.ai.azure.com/openai/v1
AZURE_OPENAI_MODEL=gpt-5          # your deployment name
```

**Result (demo week, offline rules):**

- 40 facts from 31 notes: 37/37 true facts found (precision 92%), fields 100% right, 7/7 supervisor
  answers read right.
- The floor also reported all 11 problems the cause finder found.
- **Early warnings** (the floor saw it before the data did):
  - the bad bolt batch BL-4471, about 2 h early;
  - the NR-012 nutrunner drift ("feels different"), 4 h early;
  - the CF-013 fill-head seal, 10 h early.
- **Notes only:** a coolant puddle (slip risk) and a flickering light curtain. These are safety items
  that no sensor model can see.
- **Disputes:** the supervisor disagrees that OP-B2's angle pattern is a people issue, and asks
  maintenance to check NR-012 first. The copilot should show that, not hide it.
- **Caveat:** the offline parser was written for this note style, so its score flatters.

**Result with Azure GPT-5 (first live run, prompt v1, 45 s for 31 notes):**

- Found 35/37 facts, precision 92%, fields 93% right, 7/7 supervisor answers right. The same early
  warnings, safety items and dispute were found.
- 3 of the misses were ambiguous labels in the answer key, not model errors. The key now accepts a
  second reading for these (`alt_*` columns):
  - a forklift parked in the aisle counts as safety or other;
  - a preventive repair counts as fixed or info;
  - an MES answer can have station "line".

  With that, the same run scores 36/37, precision 95%, fields 96%.
- The remaining real errors:
  - 6 status mix-ups (for example, "maint. informed" was read as monitoring instead of open);
  - one symptom split into a second fact (the wrong board count);
  - "double scans (WI-013 v8)" was filed under it / MES instead of method.

  Prompt v2 addresses each of them. Re-run to measure it.
- `python floor_listener.py --misses` lists every difference from the answer key.

## Model 5 - method checker (`method_checker.py`)

Fixed, explainable rules - no ML. It checks a work instruction (WI) before rollout, and checks
every past version in the database: what the checker would have said, and what happened after it.

- **Input:** `method_data.py` gives every WI version a step list with standard time, tool, torque,
  lift, hazard / PPE, control-plan flag, qualification and minimum skill. It also writes operator
  qualifications and WI sign-offs. The simulator's bad versions get the matching bad change, but the
  checker never sees which versions are bad.
- **Proposals:** a JSON file of changes to an existing version (`add`, `remove`, `move`, `edit` a step).
  See `proposals/`.

| Group | Rule | Blocks when |
|---|---|---|
| TIME | TIME-1 | planned cycle > 50 s takt (warn > 95 %) |
| | TIME-2 | hands-on work > +10 % over the station's line-balance standard (warn) |
| SAFETY | SAF-1 | a critical step has no result check after it |
| | SAF-2 | a control-plan step was removed |
| | SAF-3 | a powered tool above 50 Nm has no reaction arm |
| | SAF-4 | manual torque >= 60 Nm on every car (a sampled audit is fine) |
| | SAF-5 | an unassisted lift > 15 kg (warn > 10 kg) |
| | SAF-6 | a chemical step lists no PPE |
| | SAF-7 | hands in the machine zone during an automatic step |
| TRACE | TRC-1 | not exactly one VIN scan per car |
| PEOPLE | TRN-1 | a crew has 2+ operators without a needed qualification (1 = warn) |
| | TRN-2 | operators below a step's minimum skill (warn: pair them) |
| | TRN-3 | cycles built before the operator signed the version (warn, history) |
| REALITY | REA-1 | measured hands-on time differs from the plan by > 8 % (warn, history) |
| | REA-2 | incidents the cause finder blamed on this version (info) |

**Result:**

- **Demo week:** 2 BLOCK, 5 PASS. Both bad versions would have been stopped before rollout.
  - WI-012 v4: 52.4 s > 50 s takt; 120 Nm by hand on every car; crew B has 2 of 4 operators without
    the click-wrench qualification. 38% of its cycles were built before the operator had signed it.
  - WI-013 v8: two VIN scans per car, which double-books MES. Measured hands-on time is 9% *below*
    plan, so operators skip the second scan most of the time.
- **Proposals:**
  - WI-012 v7 (pre-kitted bolts) passes at 45.0 s.
  - WI-013 v9 is blocked. It fixes the double scan but adds a coolant top-up with no PPE, and nobody
    is qualified for it.
- **30 random simulated weeks:** caught 46/46 bad versions, with 0 false alarms on 176 harmless ones.
  This shows that the rules fire when they should and stay quiet otherwise. It does not prove they
  would catch real problems they were not written for.

## Model 6 - change manager (`change_manager.py`)

No work-instruction change reaches the line without a check and an approval, and the next shift always
knows the current method.

`DRAFT -> CHECKED -> APPROVED -> PILOT (one crew, one shift) -> RELEASED -> after-check -> CLOSED`,
or `BLOCKED` (the checker said BLOCK) or `ROLLED BACK` (the after-check failed).

| Gate | Rule |
|---|---|
| check | runs the method checker; BLOCK stops the change here; WARN needs a written reason |
| approve | engineer always; quality when a critical step, result check, VIN scan or control-plan step changes; supervisor when operators' work or qualifications change |
| pilot | one crew for one shift, only when every operator of that crew has signed |
| release | all crews, only after the pilot shift and when the whole rotation has signed |
| after-check | first shift on the new version: median cycle within takt, hands-on within 8 % of plan, no incident blamed on it |

**Result (`python change_manager.py demo`):**

- **Replay of the week's 5 real WI changes:**
  - WI-012 v4 stops at the check. In reality it ran 32 h and cost 252 cars. Even without the check,
    the pilot's after-check would have rolled it back after 8 h (52.2 s > takt; incident #4).
  - WI-013 v8 stops at the check (2 VIN scans). In reality it ran 40 h, leaving 185 cars to check.
  - The other 3 versions pass, and their after-checks are OK.
- **Proposals:**
  - WI-012 v7 needs engineer and supervisor approval. The pilot and the release are both refused
    until the operators have signed; then it is released.
  - WI-013 v9 is blocked, and its approval is refused.
- **Handover sheet:** ST013 is still running WI-013 v8, so the sheet raises an alert: "fails the method
  check - roll back to v7". v9 is marked "not for use".

## Model 7 - containment (`containment.py`)

For each problem case, at the moment the cause finder would have found it:

1. **Scope by genealogy.** Which cars count depends on the cause:
   - machine drift: every car back to the machine's last good check;
   - bad batch: every car with that part batch;
   - method: every car on that WI version;
   - people: that operator's cars in the affected shifts;
   - data: cars with no VIN or no upstream record. The VIN is inferred from its neighbour.
2. **Sort each car:**
   - REWORK: rejected, or out of spec;
   - CHECK: re-hits, flagged by the signal checker, more than half its tolerance used, or a bad batch
     on a safety-critical joint;
   - HOLD: waits for a 1-in-20 audit;
   - RELEASE.

   The report also says whether each car is still in the plant or already shipped.
3. **Advise** one of: STOP, QUARANTINE BATCH, ROLL BACK / FIX WI, 100% CHECK, MANUAL RECORD, SUPPORT
   or NO HOLD. The engineer recommends; the supervisor decides a stop; quality releases held cars.

**Result (demo week):**

- **NR-012 drift → STOP ST012.** 28% of cars in the last 2 h are suspect on a safety-critical joint.
  The scope is 1,696 cars, going back 27 h to the last good check: 327 to check, 1,357 held for a
  68-car audit. With the maintenance plan's every-shift check, this window would be at most 8 h.
- **BL-4471 → quarantine the batch.** 510 cars need their bolts checked; no line stop is needed.
- **CF-013 seal → 100% check at ST013.** 149 cars to check.
- **MES outage → check 79 cars** with no record.
- **WI-013 v8 → fix the WI.** The double scans only affect the count; 8 cars have no VIN.
- **People cases → support, no stop.**
- **Speed problems → no hold** (no product risk).

## Model 8 - maintenance predictor (`maintenance.py`)

Each of the 8 failure modes gets a policy based on a Weibull life model. The model is fitted by maximum
likelihood, allowing for machines still running and machines already old when the history starts. The
data is 2 years of fleet history (12 identical units per equipment type) plus our own unit's demo week.

- **wear** (socket, seal, clamp, pump): planned replacement at the interval with the lowest cost per hour.
- **silent** (torque calibration, leak tester): check interval `I* = sqrt(2 x check cost / (drift rate x cars/h x re-check cost))`,
  capped at one shift on a severity-9 joint. The interval is also the containment window.
- **random** (VIN scanners, shape ~1): no fixed interval; keep a spare and let the signal checker watch it.

**Result:**

- The fitted shapes match the simulator's true ones (2.7 vs 2.8, 1.9 vs 1.8, 3.1 vs 3.2, ...).
- Replace the NR-012 socket and the CF-013 seal weekly. Check both calibrations every shift instead
  of daily, which cuts the worst-case containment window from 24 h (1,728 cars) to 8 h (576 cars).
- Service the clamp and the pump monthly; the pump is overdue and goes into the next maintenance
  window.
- About 43% less maintenance and failure labour per year (in the model's cost units).
- The costs and the true Weibull parameters are assumptions (constants in `MODES`).

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

-- the ranked impact list
SELECT rank, category, priority, status, title, rule, action FROM impacts ORDER BY rank;

-- can we hit 7,500?
SELECT * FROM impact_summary;

-- method checker: verdict per WI version, and why
SELECT wi_version, source, verdict, planned_cycle_s, headline FROM method_verdicts;

-- what the floor said that the data did not see
SELECT shift_date, shift, station, category, subject, quote FROM floor_facts
WHERE link IN ('early warning', 'notes only', 'disputes');

-- which cars wait for a check, and the advice per case
SELECT case_id, culprit, action, cars_in_scope, "check", hold, why FROM containment_cases;
SELECT * FROM car_holds WHERE case_id = 7 AND disposition = 'CHECK';

-- how often to check each machine
SELECT equipment, failure_mode, policy_now, policy_rec, next_due, p_fail_7d FROM maint_plan;

-- change history and where each change stopped
SELECT change_id, wi_version, state, verdict, needs, outcome FROM changes;

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
| `impact_ranker.py` | Impact ranker: the formula, High/Medium/Low, next-week projection, 7,500 check, catalog |
| `method_data.py` | WI step catalog, qualifications and sign-offs (input of the method checker) |
| `method_checker.py` | Method checker: rules, history run, proposals, evaluation on random weeks |
| `proposals/*.json` | Example WI change proposals |
| `generate_notes.py` | Shift notes, maintenance log and supervisor answers + their answer key |
| `floor_listener.py` | Floor listener: Azure GPT-5 or offline parser, links to incidents, scoring |
| `change_manager.py` | Change manager: gates, approvals, pilot / release, after-check, replay, handover sheet |
| `containment.py` | Containment: scope by genealogy, car-by-car disposition, stop / quarantine / roll-back advice |
| `maintenance.py` | Maintenance predictor: fleet history, Weibull fit, check / replacement intervals, next due |
| `.env.example` | Azure settings template (copy to `.env`, which git ignores) |
| `plot_station.py`, `plot_people.py`, `plot_causes.py`, `plot_impacts.py`, `plot_method.py`, `plot_floor.py` | Charts in `charts/` |
| `data/injected_*.csv` | Answer keys from the simulator, used only for the evaluation printouts |
| `PROGRESS.md` | Step-by-step log of what was built and how |
