# ai-materials-radar

Careers-page pipeline behind the "AI x Materials and Processes" tracker.

## How it works

1. `registry/employers.json`: the employers being tracked (exported from the tracker).
2. `discover-sources` workflow (manual): finds each employer's hiring system (Workday, Greenhouse,
   Lever, Ashby, Personio, SmartRecruiters, SuccessFactors, Eightfold, Phenom, iCIMS, Oracle, ...),
   verifies it by actually reading postings, and writes `data/sources.json`.
   `registry/overrides.json` pins a source by hand: `{"employer-id": {"type": "workday", "params": {...}}}`
   or `{"employer-id": {"type": "none", "note": "why"}}`.
3. `scrape-careers-pages` workflow (daily): reads every source and updates `data/state.json`.
   - postings are matched by the employer's own job ID;
   - a failed read changes nothing and is flagged in `data/health.json`;
   - a source that drops from 3 or more postings to zero is treated as suspect for 3 runs before
     it is believed;
   - a posting is closed only after 2 consecutive successful reads without it (3 for the generic page reader);
   - closed postings that come back are reopened.
4. A keyword pre-filter writes `data/candidates.json`; a weekly Claude task classifies only new
   candidates with Haiku and updates the tracker.

`data/report.md` is the human-readable status of the last run; `data/changes/` logs what opened and closed.
