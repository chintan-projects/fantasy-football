"""Config loading.

Both of these are regression tests for bugs that made `make auth` unrunnable on a clean
checkout -- BUG-002 and BUG-003 in BUGS.yaml. The failure mode they share is worth naming:
config that breaks only when the working directory or a blank field changes is config that
breaks at the keyboard on a Sunday morning, and neither bug was reachable from any test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ff.api.deps import build_deps
from ff.core.config import REPO_ROOT, Settings
from ff.core.errors import ConfigError


class TestEnvFileLocation:
    """BUG-002: every make target cds into backend/ first."""

    def test_the_env_file_is_anchored_to_the_repo_root(self) -> None:
        assert REPO_ROOT.name == "fantasy-football" or (REPO_ROOT / "Makefile").exists()

    def test_the_repo_root_is_where_the_makefile_lives(self) -> None:
        """A relative '.env' resolves against the process working directory, and every
        make target runs from backend/. The root has to be found, not assumed."""
        assert (REPO_ROOT / "Makefile").exists()
        assert (REPO_ROOT / "backend" / "pyproject.toml").exists()

    def test_settings_find_the_root_env_file_from_any_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FF_YAHOO_CLIENT_ID=from-the-root\n")
        monkeypatch.chdir(tmp_path / "..")
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
        assert settings.yahoo_client_id == "from-the-root"


class TestBlankValues:
    """BUG-003: a key present with no value is not the same as a key that is absent."""

    def test_a_blank_random_seed_reads_as_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FF_RANDOM_SEED", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FF_RANDOM_SEED=\n")
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
        assert settings.random_seed is None

    def test_whitespace_reads_as_none_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FF_RANDOM_SEED", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FF_RANDOM_SEED=   \n")
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
        assert settings.random_seed is None

    def test_a_real_seed_still_parses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FF_RANDOM_SEED", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FF_RANDOM_SEED=42\n")
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
        assert settings.random_seed == 42

    def test_a_seed_that_is_not_a_number_still_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Blank means "unset". "banana" means someone made a mistake, and should hear so."""
        monkeypatch.delenv("FF_RANDOM_SEED", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FF_RANDOM_SEED=banana\n")
        with pytest.raises(ValueError, match="random_seed"):
            Settings(_env_file=env_file)  # type: ignore[call-arg]


class TestShippedTemplate:
    """The template is what a new checkout copies. It has to load."""

    def test_the_committed_env_example_parses(self) -> None:
        settings = Settings(_env_file=REPO_ROOT / ".env.example")  # type: ignore[call-arg]
        assert settings.write_enabled is False
        assert settings.write_executor == "dryrun"
        assert settings.faab_budget == 100
        assert settings.random_seed is None

    def test_the_template_keeps_writes_off(self) -> None:
        """Safety invariant 5.5: dry run is the default and stays the default."""
        settings = Settings(_env_file=REPO_ROOT / ".env.example")  # type: ignore[call-arg]
        assert settings.write_enabled is False


class TestProjectionSourcesActuallyExist:
    """A name in the config with no adapter behind it is how two sources become one.

    Found live: the environment file listed espn and fantasypros, Settings counted two and
    passed, and build_deps quietly dropped the one it could not build. The server came up
    healthy reporting a single source -- which the whole projections design calls a
    misconfiguration, because with one source the epistemic spread is invented rather than
    measured.
    """

    def config(self, tmp_path: Path, sources: list[str]) -> Settings:
        return Settings(
            yahoo_league_key="470.l.1000",
            yahoo_team_key="470.l.1000.t.3",
            database_path=str(tmp_path / "ff.db"),
            projection_sources=sources,
        )

    def test_a_source_with_no_adapter_is_an_error_not_a_silent_drop(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="fantasypros"):
            build_deps(self.config(tmp_path, ["espn", "fantasypros"]))

    def test_the_error_names_what_is_available(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="espn, sleeper"):
            build_deps(self.config(tmp_path, ["espn", "nowhere"]))

    def test_the_shipped_pair_builds(self, tmp_path: Path) -> None:
        deps = build_deps(self.config(tmp_path, ["espn", "sleeper"]))
        assert [source.name for source in deps.sources] == ["espn", "sleeper"]
