#!/usr/bin/env python
"""Run every MCP tool against recorded fixtures and print what Claude would see.

Yahoo has our application under review and answers every Fantasy endpoint with
``401 additional_authorization_required``, so this is currently the only way to see the
server produce a real recommendation end to end. It is also the fastest way to read the
tool descriptions as text, which is how Claude reads them.

    make mcp-demo

Nothing here touches the network and nothing writes to Yahoo: the executor is the dry-run
one, and the database is a throwaway file under the system temp directory.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path[:0] = [str(BACKEND / "src"), str(BACKEND / "tests")]

from fakes import FIXTURE_WEEK, FixtureLeague, fixture_sources  # noqa: E402

from ff.adapters.store import Store  # noqa: E402
from ff.api.deps import Deps  # noqa: E402
from ff.api.mcp import build_server  # noqa: E402
from ff.core.config import Settings  # noqa: E402

RULE = "-" * 78


class FixtureSleeper:
    def current_week(self) -> int:
        return FIXTURE_WEEK


def build_deps(db: Path) -> Deps:
    config = Settings(
        yahoo_league_key="470.l.1000",
        yahoo_team_key="470.l.1000.t.3",
        database_path=str(db),
        monte_carlo_draws=5_000,
        random_seed=7,
    )
    return Deps(
        yahoo=FixtureLeague(),
        sources=list(fixture_sources()),
        store=Store(config.database_path),
        sleeper=FixtureSleeper(),  # type: ignore[arg-type]
        config=config,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        server = build_server(build_deps(Path(tmp) / "demo.db"))

        print(RULE)
        print("TOOLS, AS CLAUDE SEES THEM")
        print(RULE)
        for tool in asyncio.run(server.list_tools()):
            annotations = tool.annotations
            flags = (
                f"read_only={annotations.read_only_hint} destructive={annotations.destructive_hint}"
                if annotations
                else "no annotations"
            )
            print(f"\n{tool.name}  ({flags})")
            print(f"  {tool.title}")
            for line in (tool.description or "").splitlines():
                print(f"  | {line}")

        print(f"\n{RULE}")
        print("RESULTS")
        print(RULE)

        def show(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
            result = asyncio.run(server.call_tool(name, args or {}))
            payload: dict[str, Any] = result.structured_content or {}
            print(f"\n$ {name}({json.dumps(args or {})})")
            print(json.dumps(payload, indent=2)[:4000])
            return payload

        show("ff_recommend_lineup")
        board = show("ff_waiver_board", {"limit": 3})
        show("ff_my_roster")
        show("ff_league_transactions", {"limit": 5})
        show("ff_record_preference", {"text": "Never drop Bijan.", "kind": "do_not_drop"})
        show("ff_list_preferences")

        targets = board.get("targets") or []
        if not targets:
            print("\nNo waiver targets in the fixture pool; skipping the claim demo.")
            return 0

        player = targets[0]["player"]
        show("ff_recommend_bid", {"player": player})
        plan = show("ff_propose_claim", {"player": player})
        if plan.get("approval_id"):
            show("ff_confirm", {"approval_id": plan["approval_id"]})
            # Second call proves the approval is single use, which is the safety invariant.
            show("ff_confirm", {"approval_id": plan["approval_id"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
