# Sleeper fixtures

`projections_week2.json` is **recorded live** — `api.sleeper.com/projections/nfl/2026/2`
on 2026-09-13, trimmed from 3304 rows to 25: the 18 highest-projected skill players, 4
team defenses, and 3 rows carrying no scored projection at all.

That last group is deliberate and is half the point of the fixture. Sleeper returns every
player it knows about, most of whom have no projection. A parser that reads a missing
`pts_ppr` as zero would quietly project a punter to score nothing and then let him into an
ensemble. The unscored rows are there so a test can prove they are skipped rather than
counted.

The defenses are there for the other join: `match_key` identifies a DEF by team
abbreviation, not by name, because Sleeper calls them "Ravens" while Yahoo calls them
"Baltimore" and ESPN calls them something else again.

## malformed/

| file                        | what it proves                                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `empty_list.json`           | A 200 with nothing in it is drift, not "no players this week"                                                       |
| `not_a_list.json`           | The endpoint is undocumented; the top-level type is not a promise                                                   |
| `no_scored_projection.json` | The dangerous case: rows parse fine, no points anywhere, and without this check the app silently runs on one source |
| `row_without_player.json`   | The join key is gone                                                                                                |
| `points_not_a_number.json`  | A string where a float belongs, which `float()` would raise on far from here                                        |

All five are edits of the recorded payload, so they drift in shape exactly when the real
one does.

## Two hosts

The documented Sleeper API is `api.sleeper.app/v1`. Projections live on `api.sleeper.com`
with no version prefix and no entry in the public docs. Treat that endpoint like ESPN's:
validate every fetch, never assume the shape held.
