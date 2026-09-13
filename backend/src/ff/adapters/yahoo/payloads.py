"""Yahoo write payloads, built as XML.

Every shape here is verified against Yahoo's documentation -- see
.claude/skills/yahoo-fantasy-api for the sources and the two documented gotchas that these
builders get right:

1. ``<faab_bid>`` is a direct child of ``<transaction>``, a sibling of ``<type>``, and must
   come *before* ``<players>``. It is not inside ``transaction_data``.
2. Yahoo's own published FAAB example uses ``destination_team_key`` on the drop leg. That is
   a bug in their docs. The drop leg takes ``source_team_key``.
"""

from __future__ import annotations

from xml.sax.saxutils import escape


def _esc(value: str) -> str:
    return escape(value)


def set_lineup_xml(week: int, assignments: list[tuple[str, str]]) -> str:
    """PUT /team/{team_key}/roster.

    The week goes in the body, not the URL. Yahoo applies this atomically: an invalid move
    rejects the whole request and changes nothing, which is exactly the behaviour we want.
    Players you omit stay where they are.
    """
    players = "".join(
        f"<player><player_key>{_esc(pk)}</player_key><position>{_esc(pos)}</position></player>"
        for pk, pos in assignments
    )
    return (
        '<?xml version="1.0"?>'
        "<fantasy_content><roster>"
        "<coverage_type>week</coverage_type>"
        f"<week>{int(week)}</week>"
        f"<players>{players}</players>"
        "</roster></fantasy_content>"
    )


def add_drop_xml(
    team_key: str,
    add_player_key: str,
    drop_player_key: str | None = None,
    faab_bid: int | None = None,
) -> str:
    """POST /league/{league_key}/transactions.

    There is no separate creation type for a waiver claim. POST a normal add or add/drop and
    Yahoo converts it to a claim if the player is on waivers.
    """
    add_leg = (
        f"<player><player_key>{_esc(add_player_key)}</player_key><transaction_data>"
        f"<type>add</type>"
        f"<destination_team_key>{_esc(team_key)}</destination_team_key>"
        f"</transaction_data></player>"
    )
    bid = f"<faab_bid>{int(faab_bid)}</faab_bid>" if faab_bid is not None else ""

    if drop_player_key is None:
        # Single-player form: no <players> wrapper.
        return (
            "<?xml version='1.0'?>"
            f"<fantasy_content><transaction><type>add</type>{bid}{add_leg}"
            "</transaction></fantasy_content>"
        )

    drop_leg = (
        f"<player><player_key>{_esc(drop_player_key)}</player_key><transaction_data>"
        f"<type>drop</type>"
        f"<source_team_key>{_esc(team_key)}</source_team_key>"
        f"</transaction_data></player>"
    )
    return (
        "<?xml version='1.0'?>"
        f"<fantasy_content><transaction><type>add/drop</type>{bid}"
        f"<players>{add_leg}{drop_leg}</players>"
        "</transaction></fantasy_content>"
    )


def edit_claim_xml(
    transaction_key: str,
    faab_bid: int | None = None,
    waiver_priority: int | None = None,
) -> str:
    """PUT /transaction/{transaction_key}. Only waiver and pending_trade accept PUT.

    The Python client library has no public method for this, so we build it ourselves and
    send it on the same authenticated session.
    """
    bid = f"<faab_bid>{int(faab_bid)}</faab_bid>" if faab_bid is not None else ""
    prio = (
        f"<waiver_priority>{int(waiver_priority)}</waiver_priority>"
        if waiver_priority is not None
        else ""
    )
    return (
        "<?xml version='1.0'?>"
        "<fantasy_content><transaction>"
        f"<transaction_key>{_esc(transaction_key)}</transaction_key>"
        "<type>waiver</type>"
        f"{prio}{bid}"
        "</transaction></fantasy_content>"
    )


def pending_claims_path(league_key: str, team_key: str) -> str:
    """The only way to discover your own pending claim keys.

    Yahoo, verbatim: "Pending transactions will not show up if you simply ask for all of the
    transactions in the league, because they can only be seen by certain teams." The
    ``waiver`` and ``pending_trade`` types are valid *only* when team_key is also supplied.
    """
    return f"/league/{league_key}/transactions;types=waiver,pending_trade;team_key={team_key}"
