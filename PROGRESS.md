# Progress log - TAKT (production engineer copilot)

This file records what was built, how, and where it stands. A new step is added at the
bottom every time we work on the project, so you can see what was done at each point.

## Where we are

| # | Model | Job in one line | Status | Files |
|---|---|---|---|---|
| 0 | Line simulator | Dummy Tesla-style data + the true answers | **Built for 2 stations**: crews, rotation, WI versions, batches, repairs, 16 root-cause scenario types, demo week + random training weeks. Missing: full line, 45 s takt, notes text, maintenance history | `generate_data.py`, `station_db.py` |
| 1 | Signal checker | Real drop or noise? | Mostly built at row level (rules + ML); block-level KPI z-scores now exist in the cause finder | `train_model.py` |
| 2 | Cause finder | Method, people, station or machine? | **v1 built (step 3)**: 95% on held-out weeks, 11/11 on the demo week | `cause_finder.py`, `train_cause_model.py`, `find_causes.py`, `plot_causes.py` |
| 3 | Impact ranker | What matters most, bottleneck, 7,500? | **v1 built (step 4)**: formula + High/Medium/Low, next-week projection, bottleneck and 7,500 check, catalog of possible problems | `impact_ranker.py`, `plot_impacts.py` |
| 4 | Floor listener | Handover notes -> structured data | **v1 built (step 6)**: Azure GPT-5 (strict JSON) + offline fallback; links notes to incidents (agrees / early warning / notes only / disputes) | `generate_notes.py`, `floor_listener.py`, `plot_floor.py` |
| 5 | Method checker | Fits takt, safe, trained? | **v1 built (step 6)**: 15 fixed rules vs 50 s takt; history + proposals; 46/46 bad versions caught on 30 random weeks | `method_data.py`, `method_checker.py`, `plot_method.py`, `proposals/` |
| 6 | Change manager | Versions, approval, rollout, after-check | **v1 built (step 8)**: gates check -> approve (by role) -> pilot -> release -> after-check; replay of the week; handover sheet | `change_manager.py` |
| 7 | Containment | Which cars to hold, stop or not | **v1 built (step 8)**: scope by genealogy, car-by-car rework/check/hold/release, stop / quarantine / roll-back advice | `containment.py` |
| 8 | Maintenance predictor | How often to check each machine | **v1 built (step 8)**: Weibull fit on fleet history, wear / silent / random policies, next due | `maintenance.py` |
| 9 | Agent | Uses all the others as tools | **v1 built (step 9)**: Azure GPT-5 with 13 read-only tools, cited answers, follow-ups, cache + offline fallback | `agent.py`, `agent_tools.py` |

Also built: **the app** (`app.py` + `frontend/`, step 10), **the live line + workflow diagram** (`live.py`, step 11), **the factory map** (step 12), **the pitch deck** (`presentation/`, step 14), people model (`train_people_model.py`), charts (`plot_station.py`, `plot_people.py`, `plot_causes.py`, `plot_impacts.py`, `plot_method.py`, `plot_floor.py`).

Plan for the rest: [System design for the remaining models](https://claude.ai/code/artifact/1c103c0d-14c3-49d5-ba62-1e113bc3e8df) (step 5).

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

**Note:** the folder is a git repo; step 3 is in your commits `c363237`, `4ffabd6` and `cc9d6d4`.

### Step 4 - 2026-09-26 - Impact ranker (model 3) v1

**Goal:** from everything already in the database, rank the impacts - the ones we had and the ones
we could have - with an explicit formula, and put each in High / Medium / Low. Also answer: where is
the bottleneck, and can we hit 7,500 cars a week?

**Decisions agreed (2026-09-26):** the priority order is Safety > Quality > Delivery > Cost (plus
people), and 7,500 means cars per week.

**What we did**

1. **Collect impact items** from what the models already wrote, without re-analysing raw data:
   - cause-finder cases (11);
   - signal-checker flags outside incidents, grouped by kind and station;
   - people-model findings (rushed, struggle, re-hit bursts, stale logins, andon without team
     lead, rest-time breach);
   - faults grouped by code, and unplanned repairs no incident explains.

   Flags that fall inside a case's window are folded into that case (e.g. extra double scans
   under WI-013 v8). That gives **49 items**.
2. **Measure each item** in four currencies: risk cars, lost cars, rework hours and people hours.
   - Lost cars use the bottleneck logic: ST012 at 50 s/car loses everything; ST013 only loses what
     its 3 s spare time per car can't absorb.
   - Pattern risk uses "share of the safety margin used up".
3. **Score:** log sub-scores 0-10, then 10 × (0.40·SQ + 0.30·DL + 0.15·CO + 0.15·PE).
4. **Categories:**
   - High from 50, Medium from 25, Low below.
   - Hard rules for High: known-bad cars passed; definite cars ≥ 10·3^(9−S); ≥ 30% of the margin
     on a safety-critical signal; ≥ 75 lost cars; a legal rest-time breach.
   - Medium floor: severity ≥ 8 with any risk cars.
5. **Future:**
   - Active items are projected over the next 7 days; recurring ones repeat. Priority = max(now,
     next week).
   - `impact_catalog` scores 16 problem types × 2 stations "if it happened next week" with the same
     formula.
6. **7,500 check:** capacity from the bottleneck minus planned maintenance, cars built at the last
   station, lost cars, and next week's projection (`impact_summary`).
7. **New tables:** `impacts`, `impact_catalog`, `impact_summary`. New column: `severity` in the
   station config. Chart: `plot_impacts.py`.

**How - key decisions**

- **Log scales:** so 10× more impact is a fixed step and huge counts can't dominate.
- **Hard rules:** some things can't be averaged away - a known-bad safety part, a legal breach, or
  a big output loss.
- **Bottleneck-aware lost cars:** a slow non-bottleneck station doesn't cost output until it uses
  up its spare time. So faults at ST013 score Low and faults at ST012 score higher.
- **Sanity check:** the ranked problems explain 698 lost cars. The actual gap between capacity and
  cars built is 640, so the estimate is about 9% high - good enough to rank with.

**Result (demo week)**

- **Categories:** 13 High, 20 Medium, 16 Low.
- **Top items:**
  1. Nutrunner NR-012 drift - 64, used 46% of the safety margin.
  2. MES outage - 136 cars to verify.
  3. WI-013 v8 - still active, grows next week.
  4. Andon calls without team lead - 99 lost cars.
  5. WI-012 v4 - 252 lost cars.
- **People cases:** all Medium.
- **Can we hit 7,500?** Yes: 11,241 built (150%); ~11,617 projected for next week. On a 5-day week
  it would be 8,041 (107%), which is the more realistic frame.
- **Could happen next:** a failing VIN scanner and a calibration drift on ST012 would be the worst.
- **Tested on:** pandas 2.3 and 3.0, same results. It runs in under 1 s.

**Limits / next ideas**

- The weights, severities, rework minutes and thresholds are assumptions. They are constants at the
  top of `impact_ranker.py` and should be tuned with the plant.
- The 7,500 check uses a 24/7 simulated week; a real shift calendar would make it tighter.
- There are no € values yet; adding cost per lost car and per rework hour would allow ranking in money.
- **Next:** model 7 (containment) can take the "definite cars" lists straight from these items.
  Model 9 (agent) can read `impacts` to answer "what should I do first today?".

**Run:** `python impact_ranker.py && python plot_impacts.py` (after the other models).
Step 4 is not committed to git yet.

### Step 5 - 2026-09-26 - System design for the remaining models (no code)

**What**

- Wrote the system design doc:
  [Production Copilot - System Design for the Remaining Models](https://claude.ai/code/artifact/1c103c0d-14c3-49d5-ba62-1e113bc3e8df).
- **Done so far:** 0 simulator (2 stations), 1 signal checker (row level), 2 cause finder v1,
  3 impact ranker v1, plus the people model.
- **Remaining:** 1 upgrade (line level), 4 floor listener, 5 method checker, 6 change manager,
  7 containment, 8 maintenance predictor, 9 agent, and simulator v3.

**How - the design**

- **Shared foundations first:** `pipeline.py` runs every model in order; `config.yaml` holds all
  constants; one data contract (every model writes to the station tables or its own table);
  `evaluate.py` scores all models against the answer keys; `tools.py` exposes each model to the agent.
- **Simulator v3:** 6 stations, 45 s takt, 5-day shift calendar, shipping status per car,
  WI steps + operator skills, handover-note text, 26 weeks of maintenance history.
- **Per model:** 4 = LLM to JSON with a fixed schema; 5 = fixed rules (time, safety, training);
  6 = state machine Draft -> Checked -> Approved -> Pilot -> Rolled out -> After-check;
  7 = car genealogy graph + hold/stop rules; 8 = survival model; 9 = LLM agent that only quotes tools.
- **Build order (about 14 working days):** A foundations + sim v3 -> B agent v0 (read-only, 3-pane
  app) -> C method checker + change manager -> D containment -> E floor listener -> F stretch
  (line-level checks, maintenance). Each phase ends with a demo gate checked by `evaluate.py`.

**Open decisions** (defaults in the doc): 6 stations; 5-day week at 45 s (makes 7,500 about 83% of
~9,000 capacity instead of 150%); Claude API for models 4 and 9; Streamlit; production engineer as
the main user; competition deadline still unknown.

**Next:** confirm the decisions, then start Phase A.
Steps 4 and 5 are not committed to git yet.

### Step 6 - 2026-09-26 - Floor listener (model 4) and method checker (model 5) v1

**Decisions:** the goal is a prototype, not scale, so we kept 2 stations and today's line. The method
checker uses a **50 s takt** (the current simulated line). The floor listener uses **Azure OpenAI GPT-5**
(Responses API at `tasting-resource.services.ai.azure.com`), with the key in `.env`.

**What - model 5, method checker**

1. `method_data.py`: gives every WI version a step list: time, tool, torque, lift, hazard/PPE,
   control-plan flag, qualification, minimum skill. Also writes operator qualifications (from skill)
   and WI sign-offs. The bad versions get the matching bad change:
   - speed: an extra check on every car;
   - data: a second VIN scan;
   - quality: the result check moved before the process step;
   - pattern: a preparation step removed.
2. `method_checker.py`: 15 fixed rules in 5 groups, each with a verdict of PASS, WARN or BLOCK:
   - TIME: fits the takt; added work;
   - SAFETY: result check after critical steps, control plan, reaction arm, manual torque, lifting,
     PPE, hands in the machine zone;
   - TRACE: exactly one VIN scan;
   - PEOPLE: qualifications per crew, minimum skill, sign-off before the first cycle;
   - REALITY: measured vs planned time, incidents the cause finder blamed on the version.
3. Three ways to run it: over every WI version in the DB (history), on a JSON change file
   (`--propose`, two examples in `proposals/`), or `--eval N` on random simulated weeks.
4. New tables: `work_instructions`, `wi_steps`, `qualifications`, `wi_signoffs`, `method_checks`,
   `method_verdicts`, `method_eval`. Chart: `charts/method.png`.

**What - model 4, floor listener**

1. `generate_notes.py`: 31 notes for the demo week: 21 handovers, 3 maintenance log entries and
   7 supervisor answers to copilot questions about incidents. Written in floor style: short, some
   German, "C1", "around 3". They include:
   - early warnings;
   - safety items that only people see;
   - neutral changes.

   The answer key is `data/injected_note_facts.csv` (37 facts).
2. `floor_listener.py`:
   - note -> facts (station, time, category, subject ID, symptom, action, status, severity, quote);
   - supervisor answers also get confirms / cause / decision;
   - backends: Azure GPT-5 (strict JSON schema, retries, cached in `data/llm_cache.json`) or offline
     keyword rules when no key is set;
   - links each fact to the incidents: agrees, early warning, notes only, disputes, unclear;
   - scores against the answer key.
3. New tables: `floor_notes`, `floor_facts`, `floor_eval`. Chart: `charts/floor.png`.
4. `.env.example` (settings template) and `.gitignore` (so `.env` with the key is never committed).

**How - key decisions**

- **Rules, not ML, for the method checker:** every BLOCK needs a reason an engineer can act on,
  and the limits are plant standards, not patterns to learn.
- **The checker never sees the answer:** it reads only steps and records, never which version is
  "bad".
- **TIME-2 compares with the station's line-balance standard, not the version before.** Comparing with
  the version before raised false alarms when a fix put a removed step back (3 in 12 weeks -> 0).
- **REA-1 also warns when work is faster than planned.** WI-013 v8 is 9% faster than planned, which
  means operators skip the second scan.
- **The LLM output is forced into a strict JSON schema, then cleaned** (IDs like "c1" -> "OP-C1"), so
  the rest of the pipeline never sees free text.
- **One failed LLM call doesn't stop the run.** That note falls back to the offline parser and the run
  says so.
- **The cache is keyed by endpoint + model + prompt version + note.** Once GPT-5 has read every note,
  the demo replays without a key.
- **Tested the Azure client against a local mock of the Responses API:** request shape, `api-key`
  header, JSON schema, a 429 retry, and fallback on a wrong key. A live call still needs your key.

**Result (demo week)**

- **Method checker: 2 BLOCK, 5 PASS.**
  - WI-012 v4 is blocked: 52.4 s > 50 s takt; 120 Nm by hand on every car; crew B has 2 of 4 operators
    without the click-wrench qualification. 38% of its cycles were built before the operator had
    signed it.
  - WI-013 v8 is blocked: two VIN scans per car.
  - Proposal WI-012 v7 passes at 45 s. Proposal WI-013 v9 is blocked (no PPE, nobody qualified).
  - On 30 random weeks: 46/46 bad versions caught, 0 false alarms on 176 harmless versions.
- **Floor listener (offline rules): 37/37 facts found**, precision 92%, 100% of fields right, and
  7/7 supervisor answers read right.
  - The floor reported all 11 problems the data found.
  - 4 early warnings: BL-4471 about 2 h early, NR-012 4 h early, CF-013 10 h early.
  - 2 safety items that only people saw.
  - 1 supervisor dispute (OP-B2 -> "check NR-012 first").
- **Tested on:** pandas 2.3 and 3.0, same results.

**Limits / next ideas**

- The offline parser was written for this note style, so 100% flatters it. The real score is the
  GPT-5 run. Put the key in `.env` and run `python floor_listener.py`.
- The step catalog, qualifications and rule limits are prototype assumptions. They are constants at
  the top of `method_data.py` and `method_checker.py`.
- The method checker's evaluation is circular (its rules and the simulator's bad changes come from
  the same author). It shows the rules behave, not that they would catch unknown real problems.
- **Next:** model 6 (change manager) can use the method checker as its "Checked" gate. Model 9 (agent)
  can call `floor_listener` / `method_checker` as tools.

**Run:** `python method_data.py && python method_checker.py && python method_checker.py --propose proposals/*.json && python generate_notes.py && python floor_listener.py && python plot_method.py && python plot_floor.py`
Steps 4-6 are not committed to git yet.

### Step 7 - 2026-09-26 - First live GPT-5 run of the floor listener + fixes

**What**

- You ran `python floor_listener.py` with the Azure key. GPT-5 (reasoning low) read all 31 notes
  in 45 s.
- **Result:** 35/37 facts found, precision 92%, fields 93% right, 7/7 supervisor answers right.
  - The floor reported all 11 problems the data found.
  - 4 early warnings: BL-4471 2 h, NR-012 4 h, CF-013 10 h.
  - 1 dispute (OP-B2).
  - Safety items only people saw: coolant puddle, light curtain, forklift in the aisle.
- It passes the 90% gate from the system design.

**How - what we changed after looking at every difference**

1. **Answer key.** 3 labels were honestly ambiguous. The key now lists a second accepted reading
   (`alt_*` columns in `injected_note_facts.csv`):
   - forklift in the aisle: other or safety;
   - preventive repair: fixed or info;
   - MES answer: station line.

   With that, the same GPT-5 run scores 36/37, precision 95%, fields 96%. The note texts did not change.
2. **Prompt v2** (`PROMPT_VERSION = "floor-v2"`, so the old cached answers are no longer used). It
   addresses the real errors:
   - clearer status definitions: open even if "maint. informed"; monitoring only for early signs or
     supporting a person; fixed when a new WI removes a problem; info for "no NOKs";
   - category = the cause the note points to (a WI named as the reason = method);
   - one fact per problem, so a wrong count is a symptom, not a second fact;
   - station from station words ("re-hits" = ST012).
3. `--misses` prints every difference from the answer key, per note.
4. Fixed a scoring bug where an empty alternative label counted a missing station as right.

**Next:** re-run `python floor_listener.py --misses` to measure prompt v2 (about 45 s, 31 Azure calls).
Steps 4-7 are not committed to git yet.

### Step 8 - 2026-09-26 - Change manager (6), containment (7), maintenance predictor (8) v1

**Why:** these are the three parts of the engineer's job that were still missing:
- approve a change before the line uses it, so the next shift has the current method;
- decide whether a product waits for a check or moves on, and recommend a stop;
- set how often machines are checked.

**What - model 6, change manager** (`change_manager.py`)

- States: DRAFT -> CHECKED -> APPROVED -> PILOT (one crew, one shift) -> RELEASED -> after-check ->
  CLOSED, or BLOCKED / ROLLED BACK.
- Gates:
  - the method checker (BLOCK stops the change; WARN needs a written reason);
  - approvals by role (engineer always, quality for critical / check / scan / control-plan steps,
    supervisor for operator work or training);
  - the pilot needs the crew's sign-offs; the release needs the whole rotation's;
  - the after-check on the first shift: cycle vs takt, hands-on vs plan, incidents blamed on it.
- `replay` runs the week's real WI changes through the gates. The handover sheet shows the current
  method per station, what is next, who must sign, and alerts.
- Tables: `changes`, `change_events`.

**What - model 7, containment** (`containment.py`)

- Per case, at detection time, it finds the cars in scope by genealogy:
  - machine drift: back to the last good check;
  - bad batch: every car with that batch;
  - WI version: every car built with it;
  - people: the operator's cars in the affected shifts;
  - data: cars with no record, with the VIN inferred.
- Each car gets REWORK / CHECK / HOLD (for a 1-in-20 audit) / RELEASE, plus whether it is in the plant
  or already shipped.
- Advice: STOP / QUARANTINE BATCH / ROLL BACK / FIX WI / 100% CHECK / MANUAL RECORD / SUPPORT / NO HOLD.
- Tables: `containment_cases`, `car_holds`.

**What - model 8, maintenance predictor** (`maintenance.py`)

- 8 failure modes (4 per station). The history covers 2 years for 12 identical units of each type
  (fleet data) plus the demo week's repairs.
- Weibull maximum-likelihood fit that allows for machines still running and machines already old
  when the history starts.
- Policies:
  - wear: lowest cost per hour;
  - silent drift: `I* = sqrt(2 x check cost / (drift rate x cars/h x re-check cost))`, capped at one
    shift for severity 9;
  - random: no interval.
- Output per mode: next due date, and the chance of failure in the next 7 days.
- Tables: `maint_plan`, `maint_history`.

**How - key decisions**

- **The models feed each other:**
  - the change manager uses the method checker as its "check" gate, and the cause finder in its
    after-check;
  - containment's advice for a WI problem is a roll-back through the change manager;
  - for a silent drift, containment shows what the maintenance plan's interval would do to the window.
- **The replay proves the value on the demo week:** v4 and v8 would have stopped at the check. Even
  without the checker, the pilot's after-check would have rolled them back after one shift, avoiding
  75-80% of the damage.
- **"Back to the last good check"** is how real containment scopes a drifting tool. It is also why the
  check interval matters: the window equals the interval.
- **The engineer recommends, others decide:** stops are the supervisor's decision and releases of held
  cars are quality's. The output says so.
- **Fixed in `method_data.py`:** each WI version now builds on the one before. Before, a "photos and
  wording" version silently dropped the audit step from the fix before it. The method checker is
  unchanged: 2 BLOCK / 5 PASS, and still 46/46 bad versions caught with 0 false alarms on 30 random
  weeks.

**Result (demo week)**

- **Change manager:**
  - WI-012 v4 and WI-013 v8 stop at the check. In reality they ran 32 h (252 lost cars) and 40 h
    (185 cars to check).
  - Proposal v7 is approved and released after the sign-offs; v9 is blocked.
  - The handover sheet raises an alert: ST013 is still on v8, which fails the check.
- **Containment:**
  - NR-012 drift -> STOP ST012: 1,696 cars in scope going back 27 h, 327 to check, 1,357 held for a
    68-car audit. With every-shift checks the window would be at most 8 h.
  - BL-4471 -> quarantine the batch, 510 cars to check.
  - CF-013 -> 100% check at ST013.
  - MES -> check 79 cars.
  - WI-013 v8 -> fix the WI.
  - People -> support. Speed problems -> no hold.
- **Maintenance:**
  - The fitted shapes are within 0.2 of the true ones.
  - Replace the socket and the seal weekly; check both calibrations every shift (window 24 h -> 8 h).
  - Service the clamp and the pump monthly; the pump is overdue.
  - About 43% less maintenance + failure labour per year in the model's cost units.
- **Tested on:** pandas 2.3 and 3.0, same results. `scipy` was added to `requirements.txt` (sklearn
  already needs it).

**Limits / next ideas**

- The costs, the true Weibull parameters and the containment thresholds are assumptions. They are
  constants at the top of each file.
- Containment has no real shipping data: a car counts as "shipped" 24 h after its last station.
- The change manager keeps its own simulated clock: proposals roll out on 22.09.
- **Next:** the brief builder and the frontend (Today / Investigate / Change / Capacity).

**Run:** `python method_data.py && python method_checker.py && python maintenance.py && python containment.py && python change_manager.py demo`
Steps 4-8 are not committed to git yet.

### Step 9 - 2026-09-26 - Agent (model 9) v1 - the copilot

**What**

1. `agent_tools.py`: 13 read-only tools over all the models:
   - `morning_brief` (top problems, 7,500, current alerts, floor highlights);
   - `list_incidents`, `explain_problem` (cause + evidence + what the floor said + impact + containment +
     change record + maintenance, in one call);
   - `signal_check` (is a drop real: flag rate in the window vs the rest of the week);
   - `people_findings`, `impacts` (ranking, 7,500, biggest capacity losses), `floor_notes`;
   - `method_check`, `check_proposal` (runs the checker and names the approvals needed, saves nothing);
   - `change_status` (with the handover sheet), `containment_advice`, `maintenance_plan`;
   - `run_sql` (single SELECT only, read-only connection).
2. `agent.py`:
   - Azure GPT-5 through the Responses API with strict function tools; up to 8 tool rounds per question;
     parallel tool calls;
   - follow-ups keep the conversation via `previous_response_id`;
   - the answer comes with the tool trace, for "show your work" in the frontend.
3. **Prompt:** the engineer's role and boundaries, taken from the job description. The answer shape is
   answer / Why / Next. Facts only from tools, with IDs cited. It never approves, stops or releases
   anything.
4. **Demo safety:**
   - answers are cached with their trace and replay without a key;
   - if Azure fails, it falls back to the cache or an offline keyword router over the same tools;
   - the cache key includes a fingerprint of the model results, so stale answers are not replayed after
     re-running the models.
5. CLI: `python agent.py "question"`, `--chat`, `--demo` (7 demo questions), `--trace`, `--offline`,
   `--no-cache`. Log table: `agent_log`.

**How - key decisions**

- **Tools, not raw tables.** Each tool returns a small, clean JSON with IDs. The model can't
  hallucinate joins, and the answers stay citable.
- **One tool per question the engineer actually asks** (is it real? why? what first? stop? which cars?
  will the change pass? how often to maintain?). `run_sql` is the escape hatch.
- **Read-only by design.** Approvals and stops stay human (change manager gates, supervisor,
  quality), exactly as the role describes.

**Result**

- The offline router answers all 7 demo questions from the tools. For example, "should we stop
  ST012?" -> case 7 STOP, 327 cars to check, 1,357 held for a 68-car audit, the supervisor decides,
  and the maintenance plan would cut the window to 8 h.
- The Azure loop passed a local mock of the Responses API: request shape and strict tool schemas,
  2 parallel tool calls then a third round, the chained conversation, a follow-up, a 429 retry,
  fallback when Azure is unreachable, and replay from the cache with no key.
- All 13 tools also run on pandas 3.0.

**Next:** run `python agent.py --demo` with the key (fills `data/agent_cache.json` for a safe demo),
then the frontend: Today / Investigate / Change / Capacity + the copilot panel.
Steps 4-9 are not committed to git yet.

### Step 10 - 2026-09-26 - The app: frontend integrated with all models

**What**

- **`app.py`** (FastAPI):
  - 16 read endpoints over the models: overview, alerts, timeline, case detail, people, containment,
    methods, changes, maintenance (with reliability curves), capacity, notes, replay events, copilot
    presets;
  - the change-manager actions as POSTs (check, submit, approve, sign, pilot, release, reset demo);
    gate refusals come back as HTTP 409 and show as "Gate refused";
  - `POST /api/copilot/ask` with a session per conversation;
  - it serves `frontend/`.
- **`frontend/`:** plain HTML/CSS/JS modules, no build step, offline.
  - Pages: Today, Investigate, Contain, Change, Maintain, Capacity, Shift notes.
  - Around them: the alert center + toasts, the copilot drawer, the week-replay player, dark/light
    themes, keyboard shortcuts.
  - Charts are inline SVG: timeline, sparklines, columns, waterfall, reliability curve.
  - Motion: staggered page entry, count-up KPIs, growing bars, toasts, a shaking bell, a pulsing STOP,
    a card that shakes when a gate refuses.
- **Prioritisation:**
  - the Today queue is the impact ranker's order: High/Medium/Low, sub-scores, hard rules, owner,
    focus/pin;
  - alerts are grouped critical / serious / watch / info, and critical ones pop as toasts;
  - nav badges count open serious alerts per page.

**How - key decisions**

- **Pages follow the role description** (morning results -> why -> contain -> change -> maintain ->
  planning). The boundaries are shown in the UI: "Supervisor decides the stop", "Quality releases held
  cars", and approvals by role.
- **Replay the week** turns a static dataset into a live demo. The events come from the models (WI
  go-lives with their check verdict, floor early warnings, cause finder detections, containment
  decisions, repairs).
- **Demo-safe:** everything is local; the copilot replays saved GPT-5 answers if the network fails.
- **Fixed demo question 2** to "early on Thursday 17.09 (00:00-08:30)". The first live run read
  "Thursday night" as Thu 22:00+ and wrongly called the drift noise. Run `python agent.py --demo` once
  to save that new answer.

**Tested**

- Headless Chromium screenshots of every page, dark and light, with no console errors.
- Flows: first-load toasts, the alert center, a copilot answer with its trace, replay at 4x, and the
  full gate path in the UI (reset -> submit v7 -> approve x2 -> pilot refused -> sign -> pilot ->
  release refused -> sign all -> release).
- API checked on pandas 2.3 (your real database) and pandas 3.0.

**Run:** `pip install -r requirements.txt && python app.py` -> http://localhost:8000
Steps 4-10 are not committed to git yet.

**Fix (same day):**
- **Problem:** `http://localhost:8000` stayed blank on the Mac, while `http://127.0.0.1:8000` worked. On
  macOS, "localhost" tries IPv6 first, and another program was holding port 8000 there without answering.
- **Change to `app.py`:**
  - it now checks the port on both IPv4 and IPv6, and moves to the next free port if needed;
  - it prints the exact `127.0.0.1` address and opens the browser itself;
  - it says to keep the window open; `--port` and `--no-browser` options were added.
- **Change to the frontend:** the KPI count-up now also finishes when the tab is in the background.

### Step 11 - 2026-09-26 - Workflow diagram + live line (all models on streaming data)

**What**

- **Workflow diagram** (`frontend/js/diagram.js`, new page **How it works**, key `9`):
  - five layers: the line -> detect -> understand -> decide & act -> the engineer; the 9 models plus
    the people model, the copilot and the feedback loop (released WI -> next shift's method);
  - every box shows what it produced this week (`GET /api/flow`); click a box for input, output,
    when it runs and a link to its page;
  - "Trace a problem": six animated walk-throughs (nutrunner drift, bad WI change, tired operator,
    the floor says it first, check interval, a copilot question);
  - PNG export in the current theme; `charts/workflow.png` (light) and `charts/workflow-dark.png`.
- **Live line** (`live.py`, new page **Live line**, key `8`):
  - the simulator builds a week (the demo story or a random seed) in memory; a clock releases it car
    by car; every model runs when it would on a real line (every car / 2-hour block / shift end /
    note written / before a WI goes live / repair); nothing is written to the database;
  - page: control bar (demo or random week, 4 speeds, pause, stop, restart, progress with event
    ticks), 6 live KPIs, two streaming station charts (canvas: key signal, spec limits, flagged cars,
    rejects, rolling median, cycle time vs takt), the event feed, the workflow diagram with packets
    and pulses, and the problem cases decided live (action, priority, cars to check / on hold, "floor
    first" badge, details on click);
  - `POST /api/live/start`, `POST /api/live/control`, `GET /api/live/state?ev=&pt=` (only new events
    and points);
  - `python live.py --fast` runs a whole week without waiting and scores it.
- **Refactors** so the models run on data in memory (database outputs checked identical row by row):
  `find_causes.find()`, `train_people_model.run_people()` / `write_back()` / `report()`,
  `generate_notes.build()`, `containment.prepare()`. The action labels moved into `containment.py`.

**How - key decisions**

- **No peeking:** the signal checker uses a trailing window; the cause finder only sees the blocks so
  far; containment decides at the moment the cause is confirmed. That is why live detection is slower
  than the batch run - "follows the person after rotation" or "stops after the repair" only exists
  once it has happened.
- **Act only on a stable cause:** it must hold for 2 passes in a row (>= 60%), or be >= 85% sure.
  A WI that ran for weeks is not blamed unless the finder is sure (the WI system knows its age).
  A confirmed case that disappears for 3 passes while still recent is shown as "revised".
- **Few, meaningful alerts:** flagged cars are grouped into bursts (6 in an hour, then 3 h quiet);
  escalations and fixes are announced once; toasts only for critical and rate-limited serious events.
- **Floor listener in live mode** uses saved GPT-5 answers when the note was already parsed (all 24
  demo-week notes are), live Azure calls when a key is set, the offline rules otherwise.

**Results** (`python live.py --fast`)

| Week | Planted problems confirmed live | Median hours after the problem started | Other cases |
|---|---|---|---|
| demo (seed 7) | 10 of 11 (not: supply gap) | 13 | 1 (revised 6 h later) |
| random seed 21 | 5 of 6 | 11 | 2 |
| random seed 5 | 5 of 6 | 17.5 | 3 |

Same results on your Mac (pandas 2.3.3), where `--fast` runs the whole week through all models in 51 s
and one cause-finder pass takes about 0.5 s - so even at 1 h = ½ s the analysis keeps up with the clock
(in the slower cloud test it ran a few simulated hours behind at that speed; the page shows the lag).

**Tested:** headless Chromium on both new pages, dark and light: a full random week at the top speed,
pause / resume, leaving and re-opening the page mid-week, no console errors. Code checked on pandas
3.0 (cloud) and 2.3 (your Mac).

**Run:** `python app.py` -> **Live line** -> Start.

### Step 12 - 2026-09-26 - Factory map: where on the site each problem happened

**What**

- **New page "Factory map"** (key `8`; Live line is `9`, How it works `0`) - the site plan from the
  `gigafactory-monitor` prototype, rebuilt in the app's own JS (no npm):
  - zoom and pan (wheel, drag, buttons), "Our line", "whole site", blueprint / plan colours;
  - the zones from gigafactory-monitor, plus three buildings used here: DF IT & MES data center, ED
    engineering & change office, WO maintenance workshop;
  - **our line drawn inside General Assembly (A109)**: conveyor with moving cars, ST011-ST014, the six
    machines and the operator positions - shown when zoomed in;
  - animated flows: bolt batches / coolant from Storage & Logistics, finished cars to the yard and the
    gate, data to MES, work instructions from engineering, technicians from the workshop.
- **Every problem pinned where it happened** (`frontend/js/map/site.js` `locate()`): machine causes and
  maintenance on the machine, people at the operator position, methods at the WI board, batches and
  supply at the parts rack, MES at the VIN scanner; a dashed line back to where it came from; held cars
  in the quarantine lane (LN). Zoomed out: one badge per station / building; zones glow by severity.
- **Click a pin:** decision, cars to check / on hold, the path site -> hall -> station -> machine, links to
  Investigate / Contain / copilot. Side panel: counts by place and the full list.
- **Three sources:** this week (`GET /api/map`); the week replay (`R`) drops pins when their problem
  started; the live line drops pins as the live models confirm them, with ripples, a ticker, Follow and
  optional alarm tones (Web Audio).
- "On the map" buttons on Investigate and Contain.
- **Bridge to the original HMI:** `ws://.../ws/alerts` streams live-line alerts in the HMI's ML-feed format;
  `gigafactory-monitor/.env.example` + a README section explain how to connect it (`pip install websockets`).

**How - key decisions**

- The map shows *where*, the other pages show *why* and *what to do* - every pin links there.
- Positions are illustrative (the line is placed in A109, the added buildings are unlabelled on the
  plan); the zones and building codes are the ones from gigafactory-monitor.
- Constant-size pins at every zoom, clustering when zoomed out, and pins that share a spot fan out.

**Tested:** headless Chromium, dark and light: overview, our line, selecting cases / cars, the live line
at top speed with Follow, the week replay, Contain -> "On the map"; the WebSocket bridge received 14
live alerts. No console errors. `/api/map` checked on pandas 2.3 (your Mac) and 3.0.

### Step 13 - 2026-09-26 - The name TAKT and a clean automotive design

**What**

- **Name: TAKT.** *Takt time* is the heartbeat of the line (one car every 50 s); it is a German word,
  which fits a Giga Berlin-style plant; and it says what the tool is for: keeping the line in rhythm.
  - It is used in the rail, the browser tab, the copilot drawer ("TAKT Copilot"), the How it works page,
    the start message of `python app.py`, the FastAPI title, the README and this log.
  - The mark `frontend/img/takt-mark.svg` is original: a ring (one cycle of the line) and a blue dot (the
    car that arrives every takt).
- **Design, inspired by automotive software:** quiet and monochrome, with colour only for meaning.
  - **Day mode (default):** white cards on light grey, near-black text, one blue action colour.
  - **Night mode:** pure black with dark-grey cards.
  - Wide-spaced wordmark, uppercase micro-labels, large light numbers, flat 4 px buttons, segmented
    controls, and no gradients or glows.
  - Toasts, the drawers, charts, the live line, the diagram and the factory map follow the new tokens.
  - The theme is saved as `takt-theme`, so everyone starts in day mode once.
- Re-rendered `charts/workflow.png`, `workflow-dark.png`, `factory-map.png` and `factory-map-line.png`.

**Not done on purpose:** the Tesla logo. I don't draw other companies' logos, so TAKT has its own mark.

**Tested:** every page in day and night mode in headless Chromium, with no console errors.

### Step 14 - 2026-09-26 - Competition pitch deck in Canva (5-6 min)

**What**

- A 10-slide Canva deck, **TAKT Competition Pitch**
  ([edit](https://www.canva.com/d/gT52P8Iq-0TVF1F), [view](https://www.canva.com/d/7pvF9023aQo88Zf)),
  focused on the logic and on visuals:
  1. title and the name;
  2. the problem (50 s per car, 22,000 records a week, the engineer's four questions);
  3. what TAKT does and who decides;
  4. the logic in five layers with the loop back to the line;
  5. one problem end to end (NR-012 drift, from the floor note to the check interval);
  6. where it happened (the factory map idea);
  7. live results;
  8. change without chaos (the five gates);
  9. why trust it;
  10. next steps.
- **Timed script in the speaker notes** of every slide (e.g. `[2:00 - 3:00 | 60 s]`): 5:30 in total plus a
  30 s buffer, 614 words (about 112 words a minute with pauses).
- **Checked every slide after generation** and fixed: typos (IDS -> IDs, "Impact Ranger"), a duplicated
  banner on slide 5 (now the "floor first" point), map labels that broke mid-word and now match the app
  (machine repairs, work instructions, part batches, scan & data gaps), a generic "Stop example" (now
  WI-013 v8 blocked), and capitals / line breaks on slides 3 and 9.
- `presentation/README.md`: the timing table, what to cut if you run long, which screenshot from
  `presentation/images/` to drag onto which slide, and a live-demo option for the buffer.

**How - key decisions**

- **Every number on the slides comes from the log above** (steps 3-11), so the pitch matches what the app
  shows.
- **One story carries the logic:** the NR-012 drift runs through slide 5 and is pinned on the map on slide 6,
  so the audience follows one problem through all the models instead of ten separate features.
- **The longest slot (60 s) goes to the end-to-end story**; the setup is kept under 1:15.

**Not done:** uploading the app screenshots into Canva. The network policy blocks canva.com uploads from
here, so they are in `presentation/images/` for a manual drag-in (about 1 minute).
