"""The command: settings from the environment, exit codes, and what it logs."""

from __future__ import annotations

from typing import Any

import pytest
import snowflake.connector
from click.testing import CliRunner
from snowflake.connector.errors import ProgrammingError

from tests.test_clone import FakeCursor
from utility_snowflake_clone.cli import main

ENV = {
    "TARGET_SNOWFLAKE_ACCOUNT": "xy12345",
    "TARGET_SNOWFLAKE_USER": "LOADER",
    "TARGET_SNOWFLAKE_DATABASE": "ANALYTICS",
    "TARGET_SNOWFLAKE_PASSWORD": "secret",
    "UTILITY_SNOWFLAKE_CLONE_DESTINATION_DATABASE": "ANALYTICS_DEV",
}


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> FakeConnection:  # noqa: PYI034
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def cursor(self) -> Any:
        cursor = self._cursor

        class Context:
            def __enter__(self) -> FakeCursor:
                return cursor

            def __exit__(self, *_: object) -> None:
                pass

        return Context()


@pytest.fixture
def connections(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    made: list[dict[str, Any]] = []
    cursor = FakeCursor()

    def connect(**kwargs: Any) -> FakeConnection:
        made.append({**kwargs, "cursor": cursor})
        return FakeConnection(cursor)

    monkeypatch.setattr(snowflake.connector, "connect", connect)
    return made


def run(env: dict[str, str], *args: str) -> Any:
    return CliRunner().invoke(main, list(args), env=env, catch_exceptions=False)


def test_clones_the_warehouse_database_into_the_destination(
    connections: list[dict[str, Any]],
) -> None:
    result = run(ENV)
    assert result.exit_code == 0, result.output
    assert connections[0]["database"] == "ANALYTICS"
    assert connections[0]["account"] == "xy12345"
    assert connections[0]["cursor"].statements[-1][0] == (
        "CREATE DATABASE IF NOT EXISTS ANALYTICS_DEV CLONE ANALYTICS"
    )
    assert "cloned ANALYTICS into ANALYTICS_DEV" in result.output


def test_takes_the_names_as_options(connections: list[dict[str, Any]]) -> None:
    env = {k: v for k, v in ENV.items() if not k.startswith("UTILITY_")}
    result = run(env, "--source-database", "RAW", "--destination-database", "raw_dev")
    assert result.exit_code == 0, result.output
    assert connections[0]["database"] == "RAW"
    assert connections[0]["cursor"].statements[-1][0].endswith("RAW_DEV CLONE RAW")


def test_exits_2_without_a_destination(connections: list[dict[str, Any]]) -> None:
    env = {k: v for k, v in ENV.items() if not k.startswith("UTILITY_")}
    result = run(env)
    assert result.exit_code == 2
    assert "destination_database" in result.output
    assert connections == []


def test_exits_1_with_snowflake_s_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(**_: Any) -> None:
        msg = "Insufficient privileges to operate on account"
        raise ProgrammingError(msg)

    monkeypatch.setattr(snowflake.connector, "connect", refuse)
    result = run(ENV)
    assert result.exit_code == 1
    assert "cloning ANALYTICS into ANALYTICS_DEV failed" in result.output
    assert "Insufficient privileges" in result.output
