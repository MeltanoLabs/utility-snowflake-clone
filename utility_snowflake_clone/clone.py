"""Clone a Snowflake database, zero-copy, into a new one."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from cryptography.hazmat.primitives import serialization

if TYPE_CHECKING:
    from collections.abc import Mapping

# The workflow's target-snowflake warehouse: Meltano Cloud, like Meltano, gives a workflow's plugins
# its other plugins' settings as environment variables named for the plugin, e.g. the account as
# TARGET_SNOWFLAKE_ACCOUNT. Its database is the one cloned.
WAREHOUSE_PREFIX = "TARGET_SNOWFLAKE_"

# This utility's one setting, as Meltano names it for the `utility-snowflake-clone` plugin.
DESTINATION_ENV = "UTILITY_SNOWFLAKE_CLONE_DESTINATION_DATABASE"

# An unquoted Snowflake identifier, which Snowflake resolves in upper case. Only these are taken, so
# a name never needs quoting, and so never goes into SQL as text that could end the statement.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,254}")


class CloneError(Exception):
    """A clone that can't be done as asked; its message says why, and what to change."""


class Cursor(Protocol):
    """The part of a DB-API cursor the clone uses."""

    def execute(self, command: str, params: Any = None) -> Any:  # noqa: ANN401
        """Run a statement."""
        ...

    def fetchone(self) -> Any:  # noqa: ANN401
        """The next row of the last query's result."""
        ...


def is_identifier(name: str | None) -> bool:
    """Whether `name` is a database name the clone takes: an unquoted Snowflake identifier."""
    return name is not None and _IDENTIFIER.fullmatch(name) is not None


def normalise(name: str) -> str:
    """The name as Snowflake resolves an unquoted identifier."""
    return name.upper()


@dataclass(frozen=True)
class Warehouse:
    """The Snowflake connection of the workflow's warehouse, and the database it loads into."""

    account: str
    user: str
    database: str
    role: str | None = None
    warehouse: str | None = None
    password: str | None = None
    private_key: bytes | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Warehouse:
        """The warehouse's settings, from the environment the workflow runs in.

        Raises:
            CloneError: when a setting the connection needs is missing.
        """

        def setting(name: str) -> str | None:
            value = env.get(WAREHOUSE_PREFIX + name.upper())
            return value or None

        missing = [name for name in ("account", "user", "database") if not setting(name)]
        if missing:
            names = ", ".join(WAREHOUSE_PREFIX + name.upper() for name in missing)
            msg = (
                f"No {names}: add the target-snowflake warehouse to the workflow, before this "
                "utility, so its settings reach it."
            )
            raise CloneError(msg)

        key = setting("private_key")
        key_path = setting("private_key_path")
        if key is None and key_path is not None:
            with open(key_path, "rb") as file:  # noqa: PTH123
                key = file.read().decode()
        passphrase = setting("private_key_passphrase")

        return cls(
            account=setting("account") or "",
            user=setting("user") or "",
            database=setting("database") or "",
            role=setting("role"),
            warehouse=setting("warehouse"),
            password=setting("password"),
            private_key=private_key_der(key, passphrase) if key else None,
        )

    def connect_args(self) -> dict[str, Any]:
        """Arguments for `snowflake.connector.connect`, on the database being cloned."""
        args: dict[str, Any] = {
            "account": self.account,
            "user": self.user,
            "database": self.database,
            "application": "MeltanoLabs_utility-snowflake-clone",
        }
        if self.role:
            args["role"] = self.role
        if self.warehouse:
            args["warehouse"] = self.warehouse
        if self.private_key is not None:
            args["private_key"] = self.private_key
        elif self.password:
            args["password"] = self.password
        return args


def private_key_der(value: str, passphrase: str | None = None) -> bytes:
    """The private key as unencrypted PKCS#8 DER, which the Snowflake connector takes.

    Accepts the key as PEM text (as target-snowflake takes it), as base64 of PEM (as Meltano Cloud
    stores it), or as base64 of DER.

    Raises:
        CloneError: when the value is not a private key, or its passphrase is wrong.
    """
    pem = value.strip()
    if "-----BEGIN" not in pem:
        try:
            decoded = base64.b64decode(pem, validate=True)
        except (binascii.Error, ValueError) as e:
            msg = "TARGET_SNOWFLAKE_PRIVATE_KEY is neither a PEM private key nor base64"
            raise CloneError(msg) from e
        if b"-----BEGIN" in decoded:
            pem = decoded.decode()
        else:
            return _der(serialization.load_der_private_key, decoded, passphrase)
    return _der(serialization.load_pem_private_key, pem.encode(), passphrase)


def _der(load: Any, data: bytes, passphrase: str | None) -> bytes:  # noqa: ANN401
    try:
        key = load(data, password=passphrase.encode() if passphrase else None)
    except (ValueError, TypeError) as e:
        msg = f"TARGET_SNOWFLAKE_PRIVATE_KEY could not be read: {e}"
        raise CloneError(msg) from e
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def check_names(source: str | None, destination: str | None) -> tuple[str, str]:
    """The source and destination as Snowflake names them, once they are ones the clone can do.

    Raises:
        CloneError: when either is missing or not an unquoted identifier, or they are the same.
    """
    if not destination:
        msg = (
            "No destination database: set the utility's destination_database, the new database "
            "to clone into."
        )
        raise CloneError(msg)
    for what, name in (("source", source), ("destination", destination)):
        if not is_identifier(name):
            msg = (
                f"'{name}' is not a {what} database name the clone takes: use letters, digits, "
                "_ and $, starting with a letter or _."
            )
            raise CloneError(msg)
    source_name, destination_name = normalise(source or ""), normalise(destination)
    if source_name == destination_name:
        msg = (
            f"The destination database is {destination_name}, the database the warehouse loads "
            "into: name a new one."
        )
        raise CloneError(msg)
    return source_name, destination_name


def exists(cursor: Cursor, database: str) -> bool:
    """Whether the database exists, exactly by name.

    `SHOW DATABASES LIKE` would read an underscore as any one character. Any database's
    INFORMATION_SCHEMA.DATABASES lists the account's databases the role can see.
    """
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.DATABASES WHERE DATABASE_NAME = %s",
        (normalise(database),),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def clone_database(cursor: Cursor, source: str, destination: str) -> bool:
    """Clone the source database into the destination, unless the destination exists already.

    Returns:
        Whether it was cloned now: false when the destination was there already, so a second run
        does nothing.
    """
    source_name, destination_name = check_names(source, destination)
    if exists(cursor, destination_name):
        return False
    # zero-copy: the clone shares storage with its source until either changes. Both names are
    # unquoted identifiers (check_names), so neither can end the statement.
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {destination_name} CLONE {source_name}")
    return True
