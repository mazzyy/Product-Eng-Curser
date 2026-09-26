# TAKT - competition pitch (5-6 min)

Canva deck: **TAKT Competition Pitch** (10 slides, 16:9)
- Edit: https://www.canva.com/d/gT52P8Iq-0TVF1F
- View: https://www.canva.com/d/7pvF9023aQo88Zf

The full script is in the speaker notes of every slide, with its time slot. Present with
Canva's Presenter view to see the notes and a timer.

## Timing

| # | Slide | Time | Length | Story beat |
|---|---|---|---|---|
| 1 | TAKT - the production engineer's copilot | 0:00-0:15 | 15 s | Name = takt time, one car every 50 s |
| 2 | 50 seconds per car. 22,000 records a week. | 0:15-0:50 | 35 s | The engineer needs decisions, not numbers: 4 questions |
| 3 | What needs a look. Why. Who decides. | 0:50-1:15 | 25 s | What TAKT does - and that it never makes the call |
| 4 | The logic | 1:15-2:00 | 45 s | Five layers: line -> detect -> understand -> decide -> engineer, loop back |
| 5 | One problem, end to end | 2:00-3:00 | 60 s | NR-012 drift: note -> signal -> machine 87% -> High -> STOP, 327 cars -> every-shift check |
| 6 | Where it happened | 3:00-3:30 | 30 s | The factory map: every problem pinned, line back to its source |
| 7 | Live - every model on streaming data | 3:30-4:15 | 45 s | 10 of 11 confirmed live, median 13 h; 5 of 6 on new weeks; 51 s per week |
| 8 | Change without chaos | 4:15-4:45 | 30 s | 5 gates; 46/46 bad versions caught, 0 false alarms; WI-013 v8 blocked |
| 9 | Built to be trusted | 4:45-5:10 | 25 s | 95% on unseen weeks, 37/37 facts, 13 read-only tools, people data for support |
| 10 | Next steps | 5:10-5:30 | 20 s | Connect, scale, pilot. Thank you. |
| - | Buffer | 5:30-6:00 | 30 s | Questions or one live click in the app |

The script is 614 words: about 4:25 of pure speech at 140 words a minute, so 5:30 leaves room for
pauses and clicks. If you run long, shorten slide 4 (say only the five layer names) and slide 7
(skip the 51 seconds).

## Swap stock images for real screenshots (optional)

The deck uses stock photos where a screenshot would be stronger. Drag the file from
`presentation/images/` onto the photo in Canva (it replaces the image and keeps the frame):

| Slide | Stock image now | Drag in |
|---|---|---|
| 7 Live | laptop | `takt-live.png` |
| 9 Built to be trusted | lock | `takt-copilot.png` (a cited copilot answer) |
| 10 Next steps | car line with tablet | `takt-today.png` |
| 6 Where it happened | 3D factory with callouts | keep, or `takt-map-dark.png` (then delete the callout lines) |

Spare screenshots for backup or Q&A: `takt-workflow.png`, `takt-contain.png`, `takt-investigate.png`,
`takt-change.png`.

## Live demo option (inside the 30 s buffer)

`python app.py` -> Contain -> case 7 (NR-012) -> "On the map". Or Live line -> Start at top speed.
