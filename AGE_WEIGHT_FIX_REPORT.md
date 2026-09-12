# age_weight half-life fix — 2026-09-12

## 1. Every `hl=` / `half_life=` location in the repo

Repo-wide grep for `age_weight`, `half_life`, and `hl=` as a default or explicit argument. Exactly three definitions exist; nothing else references either name:

| File | Definition | Value | Status |
|---|---|---|---|
| `analysis/build_consensus.py:74` | `age_weight(observed, half_life_years=5.0)` | 5.0 | correct (updated) |
| `analysis/validate_consensus.py:90` | `age_weight(observed, hl=5.0)` | 5.0 | correct (updated) |
| `analysis/consensus_geometry.py:100` | `age_weight(observed, hl=3.0)` | 3.0 | **stale — the bug** |

No call site anywhere in the repo passes `hl=` or `half_life_years=` explicitly (`build_consensus.py:144`, `validate_consensus.py:128`, `consensus_geometry.py:187` all call `age_weight(t.get("observed_at"))` with no override) — so each file's default *is* the effective value. `consensus_geometry.py` was the only remaining copy on the pre-tuning half-life. There is no second hidden copy.

## 2. Fix applied

`analysis/consensus_geometry.py:100`:
```python
def age_weight(observed, hl=3.0):     # before
def age_weight(observed, hl=5.0):     # after
```
One line. No other code changed.

## 3. What actually changed in published output

`consensus_geometry.py`'s `age_weight()` default is shared by every centre — the bug wasn't Canotek-specific, it affected whatever this script produced for every centre, including Walkley (see §5). Diffed `--dry-run` output built with the old value (`hl=3.0`, re-created by temporarily reverting the line) against the corrected value (`hl=5.0`), for all three centres:

**Canotek** (`reddit_traces.json` + `ocr_traces.json`, `--centre canotek`):
- Route-line count: 2 → 2 (unchanged)
- Family/segment/threshold summary: unchanged (same "3 below threshold 0.5" in family 3, same "6 below threshold 0.5" in family 4)
- 6 segment weight values increased (older evidence decays less under a longer half-life), all within the same two published route lines — **no segment's publish/drop status flipped**:

| Segment | Weight before (hl=3.0) | Weight after (hl=5.0) | last_seen |
|---|---|---|---|
| appleford × ogilvie | 0.53 | 0.66 | 2024-06-10 |
| elmridge × ogilvie | 0.53 | 0.66 | 2024-06-10 |
| appleford × elmridge | 0.53 | 0.66 | 2024-06-10 |
| appleford × crownhill | 0.53 | 0.66 | 2024-06-10 |
| blair × montreal | 1.07 | 1.20 | ~2023-06 |
| montreal × st joseph | 0.53 | 0.66 | 2024-06-10 |

**Smiths Falls** (`reddit_traces.json`, `--centre smithsfalls`):
- **Byte-identical before and after** (confirmed via `diff`, exit code 0). Smiths Falls only has 5 traces and forms 0 route families regardless of clustering/weighting (`min_family=3` can't be met), so nothing is published either way — the age_weight bug had zero effect here because there's nothing for it to weight.

## 4. Re-validated F1, against what was recorded earlier

`validate_consensus.py` has always used `hl=5.0` (it was never affected by this bug — it's a separate, independent implementation, not something `consensus_geometry.py` calls). Its threshold-1.0 default doesn't match the tuned publish threshold used elsewhere, so I swept thresholds to find the number that matches what's on record:

**Canotek**, `--sweep`:
```
 thresh   prec    rec     F1
   0.30   0.82   0.73   0.78
```
Weighted-consensus F1 at threshold 0.30 is **0.78**, matching your recollection exactly (the git log's "Canotek F1 0.63→0.77" is close — likely measured on a slightly different snapshot of the trace files that same day, not a discrepancy introduced by anything here).

**Smiths Falls**, `--sweep`: F1 at threshold 0.30 is 0.79 — no prior number was recorded for Smiths Falls to compare against, but this is internally consistent with Canotek's and Walkley's numbers at the same threshold.

Since `validate_consensus.py` never had the bug, these numbers are **unchanged by today's fix** — they were already correct before and after. What the fix does is make `consensus_geometry.py` (the thing that actually builds `consensus_routes.geojson`) consistent with the model these numbers describe, which it previously was not.

## 5. Walkley — it had the same bug, silently, all session

This needed checking rather than assuming, and the answer is not the reassuring one: **Walkley's `consensus_geometry.py` output was also running on the stale `hl=3.0` the entire time**, including through every determinism-fix verification run earlier this session (the `order_walk()` fix, its 5-run-per-centre byte-identical checks, all of it) — because the age_weight default is one function shared by every centre, not something set per-centre. There was no separate "Walkley was already fine" path; it just hadn't been re-validated against `validate_consensus.py` today the way Canotek's number was, so the mismatch had nothing to surface it.

Diffed Walkley's `--dry-run` output the same way as Canotek:
- Route-line count: 6 → 6 (unchanged)
- Family/segment/threshold summary: unchanged
- 23 segment weight values increased across both families (all upward, same decay-softening direction as Canotek) — **no segment's publish/drop status flipped** (same "6 below threshold 0.5" both times, same min/max authors per feature)

Walkley's `validate_consensus.py` numbers (unaffected, as expected — separate code path):
```
weighted consensus   prec 0.75  rec 0.70  F1 0.72   tp 116  fp 39  fn 50
frequency baseline   prec 0.71  rec 0.86  F1 0.77   tp 142  fp 59  fn 24
```
Identical to every earlier reading this session.

## 6. Test suite

`extraction/test_reddit_parser.py`: reran, passes — all FIXED cases hold, KNOWN_GAPS unchanged. Unaffected by this change, as expected (it doesn't touch `consensus_geometry.py`).

## Unrelated issue found and fixed during this check

While diffing, `git status` showed `extraction/ocr_traces.py` and `extraction/test_reddit_parser.py` with their module docstrings deleted (top ~20-40 lines blanked, rest of each file intact) — damage I did not intend and could not trace to any command I ran on those files during this session (no edit, no sed, no write targeted either file; the only interaction was reading them and running `test_reddit_parser.py` as a subprocess). Both were restored immediately via `git checkout -- <file>` before writing this report, verified byte-identical to `HEAD` afterward, and the test suite result above is from *after* that restore. Flagging this so you're aware something wrote to disk outside of an operation I can account for — worth keeping an eye out for recurrence, though I have no further evidence of scope beyond these two files (checked every other modified file's diff and found only the expected, intentional changes in each).
