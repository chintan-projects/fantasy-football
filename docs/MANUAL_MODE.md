# Manual league mode

Use this while Yahoo hasn't approved API access. You send screenshots of the Yahoo app to
Claude, and Claude passes the players to the server. The lineup and bid math is the same
as in Yahoo mode. Only the source of the league data changes.

Turn it on with `FF_LEAGUE_SOURCE=manual` (set in `fly.toml`). Turn it off by removing
that line once Yahoo approves. Nothing else changes.

## What to send, and when

| When          | Screen in the Yahoo app                        | Tool Claude calls                       |
| ------------- | ---------------------------------------------- | --------------------------------------- |
| Once          | League settings: roster slots, number of teams | `ff_update_league`                      |
| Tuesday       | My Team, after waivers clear                   | `ff_enter_roster`                       |
| Tuesday       | Your FAAB balance (just the number)            | `ff_update_league`                      |
| Tuesday       | Players, filtered to available                 | `ff_enter_free_agents`                  |
| Sometimes     | League → Transactions                          | `ff_enter_transactions`                 |
| Before Sunday | This week's matchup                            | `ff_enter_roster` with `whose=opponent` |

Ask "what do you need from me?" and Claude calls `ff_league_inputs`. It lists anything
missing or out of date and tells you which screen to send.

## How it stays safe

- **A roster saves only if every name matches.** "B. Robinson, RB, ATL" could be Bijan or
  Brian, so nothing is saved and Claude asks again. A lineup built on 15 of 16 players
  would look right and be wrong.
- **Corrected spellings are shown to you.** If Claude reads "Puka Nakua", it saves Puka
  Nacua and tells you. You're the only one who can see the screen, so you're the one who
  can catch a wrong correction.
- **Old inputs are refused.** After 7 days the roster, free-agent list and FAAB balance
  stop being used, and the tools ask for fresh ones. Each week's opponent counts only for
  that week.
- **Every answer says where its inputs came from**: when the roster was sent, that the
  FAAB balance came from you rather than Yahoo, and whether the league settings are still
  assumed.
- **Nothing is written to Yahoo.** The entry tools save to this app's own database. Bids
  still go through propose, then your approval, then a link you tap in the Yahoo app.

## What's lost compared with the API

- **Transaction history.** Until you send the Transactions page, the bid tools assume a
  quarter of the league wants each player. That's a guess, and every bid says so.
- **Live FAAB balance.** The balance is the number you last gave. Yahoo still refuses a
  bid you can't afford, so the risk is a wrongly sized bid, not overspending.
- **League settings.** Until you confirm them, the app assumes Yahoo's default roster
  (QB, 2 RB, 3 WR, TE, FLEX, K, DEF, 6 bench, 1 IR) and 12 teams.

## How names are matched

Names are matched against Sleeper's full player list (free, refreshed daily), using the
Sleeper player id. The projection sources match players by name and position, never by
id, so the change of id doesn't affect them. The matcher accepts:

- full names, with or without punctuation and suffixes ("Amon Ra St Brown", "Kenneth
  Walker III")
- the abbreviated first names the Yahoo mobile app shows ("J. Allen"), narrowed down by
  position and team
- small misspellings, which are always reported back to you
- any way of writing a team defense ("Broncos D/ST", "Denver", "DEN")

Position must match. Team is used to choose between candidates but never rules out a
single match, because Yahoo shows a traded player's new team a day before Sleeper does.
