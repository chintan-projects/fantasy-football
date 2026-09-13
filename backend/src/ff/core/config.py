"""One config object. Read once at startup; never reach for os.environ elsewhere."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ff.core.errors import ConfigError


def _find_repo_root() -> Path:
    """Walk up from this file until the Makefile turns up.

    A bare ``env_file=".env"`` resolves against the *process working directory*, and every
    make target runs from ``backend/`` -- so the root .env was never read and `make auth`
    reported missing credentials that were sitting right there. Anchoring to the file's own
    location makes the working directory irrelevant. (BUG-002.)
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "Makefile").exists() and (candidate / "backend").is_dir():
            return candidate
    # Installed non-editable, or an unusual layout. Fall back to the old behaviour rather
    # than guessing at a path that does not exist.
    return Path.cwd()


REPO_ROOT = _find_repo_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FF_", env_file=REPO_ROOT / ".env", extra="ignore")

    # Yahoo
    yahoo_client_id: str = ""
    yahoo_client_secret: str = ""
    yahoo_redirect_uri: str = "https://localhost:8080/callback"
    yahoo_league_key: str = ""
    yahoo_team_key: str = ""
    yahoo_token_path: str = ".tokens/yahoo.json"

    # Optional paid source
    fantasypros_api_key: str = ""

    # Safety. Dry run is the default and stays the default.
    write_enabled: bool = False
    write_executor: Literal["dryrun", "yahoo", "assisted"] = "dryrun"
    approval_ttl_seconds: int = 6 * 3600
    faab_budget: int = 100

    # Simulation
    monte_carlo_draws: int = 20_000
    random_seed: int | None = None

    database_url: str = "sqlite:///./ff.db"
    log_level: str = "INFO"
    projection_sources: list[str] = Field(default_factory=lambda: ["espn", "fantasypros"])

    @field_validator("random_seed", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        """``FF_RANDOM_SEED=`` means "no seed", not "the integer empty string".

        A key present with no value is how people write "leave this alone" in a .env, and
        the shipped template does exactly that. Pydantic hands it through as ``''`` and the
        whole app fails to start. Only blanks are forgiven -- a typo like "banana" still
        raises, because that is a mistake someone needs to hear about. (BUG-003.)
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def validate_for_run(self) -> None:
        """Fail loudly at startup rather than quietly at kickoff."""
        if len(self.projection_sources) < 2:
            raise ConfigError(
                "At least two projection sources are required. A single source is a "
                "misconfiguration -- ensembles beat individual sources in 63% of "
                "comparisons. See docs/DECISIONS.md."
            )
        if self.write_enabled and self.write_executor == "dryrun":
            raise ConfigError("write_enabled is true but the executor is dryrun. Pick one.")
        if self.write_executor == "yahoo" and not self.yahoo_client_id:
            raise ConfigError("Yahoo executor selected but no client id. See docs/YAHOO_SETUP.md.")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
