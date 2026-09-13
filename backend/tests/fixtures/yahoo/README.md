# Yahoo fixtures

**Provenance: hand-built, not recorded.** Every other adapter in this repo has a fixture
captured from a live response. Yahoo's API has no anonymous tier -- a bare GET returns 401 --
so recording one needs a real OAuth token against a real league, which nobody has yet on
this machine. See the open question `yahoo-api-access` in PROGRESS.yaml.

These files are written to the shapes documented in `.claude/skills/yahoo-fantasy-api` §2,
which describes Yahoo's JSON as a mechanical XML transliteration with three quirks:
numbered pseudo-arrays, entities split across single-key dicts, and interleaved
sub-resources. All three appear here.

**What this means for confidence.** The parsers are tested against what the documentation
says Yahoo returns. That is weaker evidence than a recording, and the difference matters:
the parsers are written key-first rather than index-first precisely so that a field Yahoo
adds, moves, or renames raises `SchemaDrift` with a reason instead of silently reading the
wrong value. Re-record these against a live league as soon as a token exists, and delete
this paragraph when you do.

| File | What it stands in for |
|---|---|
| `game_nfl.json` | `GET /game/nfl` -- the runtime game id lookup |
| `league_settings.json` | `GET /league/{lk}/settings` |
| `roster_week2.json` | `GET /team/{tk}/roster;week=2` |
| `roster_week2_opponent.json` | the second roster call `matchup()` makes for opponent starters |
| `matchup_week2.json` | `GET /team/{tk}/matchups;weeks=2` |
| `free_agents.json` | `GET /league/{lk}/players;status=FA.../percent_owned` |

## malformed/

| File | The real failure it reproduces |
|---|---|
| `throttled_999.html` | HTTP 999 with an HTML body. The reason every call checks the status before parsing. |
| `settings_no_roster_positions.json` | 200 with a field missing -- how an undocumented API breaks |
| `settings_unknown_slot.json` | a roster slot we do not know; silently dropping it would shrink the lineup |
| `roster_empty.json` | 200 with zero players, which is never a real roster |
| `player_no_position.json` | a position string that maps to nothing |
