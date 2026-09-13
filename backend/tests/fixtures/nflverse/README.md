# nflverse fixtures

**Provenance: recorded.** Rows from `injuries_2026.parquet`, pulled through `nflreadpy` on
2026-09-12 and trimmed to a slice that covers every `report_status` and `practice_status`
value present in week 1, plus the three players the Yahoo fixtures also use.

Two things in this recording contradict docs/DATA_SOURCES.md, which is why the file exists
rather than a hand-written one:

1. `practice_status` values are full phrases -- "Full Participation in Practice",
   "Limited Participation in Practice", "Did Not Participate In Practice" -- not
   `Full/Limited/DNP` as documented.
2. `report_status` is null on plenty of rows. A player can appear on the report with a
   practice note and no game designation.

The slice also keeps offensive linemen and defenders, because they make up most of a real
report and the adapter has to skip them rather than choke on them.

## malformed/

| File | The failure it reproduces |
|---|---|
| `missing_practice_status_column.json` | a column dropped upstream |
| `renamed_full_name_column.json` | a column renamed upstream |
| `empty.json` | an empty frame, which is the correct answer in the offseason |
