"""The clone's names, connection settings and statements, without Snowflake."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from utility_snowflake_clone.clone import (
    CloneError,
    Warehouse,
    check_names,
    clone_database,
    is_identifier,
    private_key_der,
)


class FakeCursor:
    """Records statements; says a database exists when it is in `databases`."""

    def __init__(self, databases: set[str] | None = None) -> None:
        self.databases = databases or set()
        self.statements: list[tuple[str, Any]] = []
        self._row: tuple[int] | None = None

    def execute(self, command: str, params: Any = None) -> None:
        self.statements.append((command, params))
        if command.startswith("SELECT COUNT(*)"):
            self._row = (1 if params[0] in self.databases else 0,)

    def fetchone(self) -> tuple[int] | None:
        return self._row


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key: rsa.RSAPrivateKey, passphrase: bytes | None = None) -> str:
    encryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    ).decode()


def der(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_clones_into_a_new_database() -> None:
    cursor = FakeCursor()
    assert clone_database(cursor, "analytics", "analytics_dev") is True
    assert cursor.statements[-1] == (
        "CREATE DATABASE IF NOT EXISTS ANALYTICS_DEV CLONE ANALYTICS",
        None,
    )


def test_does_nothing_when_the_destination_exists() -> None:
    cursor = FakeCursor({"ANALYTICS_DEV"})
    assert clone_database(cursor, "ANALYTICS", "analytics_dev") is False
    assert not any(command.startswith("CREATE") for command, _ in cursor.statements)


def test_looks_for_the_destination_by_exact_name() -> None:
    # SHOW DATABASES LIKE 'A_B' would also match AXB
    cursor = FakeCursor({"ANALYTICSXDEV"})
    assert clone_database(cursor, "ANALYTICS", "ANALYTICS_DEV") is True
    assert cursor.statements[0][1] == ("ANALYTICS_DEV",)


@pytest.mark.parametrize(
    "name",
    ["ANALYTICS", "analytics_dev", "_A1$", "A" * 255],
)
def test_takes_unquoted_identifiers(name: str) -> None:
    assert is_identifier(name)


@pytest.mark.parametrize(
    "name",
    [None, "", "1ANALYTICS", "ANALYTICS DEV", 'A"B', "A;DROP DATABASE X", "A" * 256],
)
def test_refuses_anything_else(name: str | None) -> None:
    assert not is_identifier(name)
    with pytest.raises(CloneError, match="not a destination database name"):
        check_names("ANALYTICS", name or "1")


def test_refuses_the_warehouse_database_as_destination() -> None:
    with pytest.raises(CloneError, match="name a new one"):
        check_names("ANALYTICS", "analytics")


def test_needs_a_destination() -> None:
    with pytest.raises(CloneError, match="destination_database"):
        check_names("ANALYTICS", None)


def test_reads_the_warehouse_from_the_environment(key: rsa.RSAPrivateKey) -> None:
    warehouse = Warehouse.from_env(
        {
            "TARGET_SNOWFLAKE_ACCOUNT": "xy12345",
            "TARGET_SNOWFLAKE_USER": "LOADER",
            "TARGET_SNOWFLAKE_ROLE": "LOADER_ROLE",
            "TARGET_SNOWFLAKE_WAREHOUSE": "LOADING",
            "TARGET_SNOWFLAKE_DATABASE": "ANALYTICS",
            "TARGET_SNOWFLAKE_PRIVATE_KEY": pem(key),
        }
    )
    args = warehouse.connect_args()
    assert args["account"] == "xy12345"
    assert args["user"] == "LOADER"
    assert args["role"] == "LOADER_ROLE"
    assert args["warehouse"] == "LOADING"
    assert args["database"] == "ANALYTICS"
    assert args["private_key"] == der(key)
    assert "password" not in args


def test_falls_back_to_a_password() -> None:
    args = Warehouse.from_env(
        {
            "TARGET_SNOWFLAKE_ACCOUNT": "xy12345",
            "TARGET_SNOWFLAKE_USER": "LOADER",
            "TARGET_SNOWFLAKE_DATABASE": "ANALYTICS",
            "TARGET_SNOWFLAKE_PASSWORD": "secret",
        }
    ).connect_args()
    assert args["password"] == "secret"
    assert "private_key" not in args


def test_says_which_warehouse_settings_are_missing() -> None:
    with pytest.raises(CloneError, match="TARGET_SNOWFLAKE_ACCOUNT, TARGET_SNOWFLAKE_DATABASE"):
        Warehouse.from_env({"TARGET_SNOWFLAKE_USER": "LOADER"})


def test_takes_the_key_as_pem_base64_pem_or_base64_der(key: rsa.RSAPrivateKey) -> None:
    expected = der(key)
    assert private_key_der(pem(key)) == expected
    assert private_key_der(base64.b64encode(pem(key).encode()).decode()) == expected
    assert private_key_der(base64.b64encode(expected).decode()) == expected


def test_takes_an_encrypted_key_with_its_passphrase(key: rsa.RSAPrivateKey) -> None:
    assert private_key_der(pem(key, b"pass"), "pass") == der(key)
    with pytest.raises(CloneError, match="could not be read"):
        private_key_der(pem(key, b"pass"), "wrong")


def test_refuses_a_value_that_is_not_a_key() -> None:
    with pytest.raises(CloneError, match="neither a PEM private key nor base64"):
        private_key_der("not a key!")
