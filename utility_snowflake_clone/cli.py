"""The `utility-snowflake-clone` command."""

from __future__ import annotations

import logging
import os
import sys

import click

from utility_snowflake_clone.clone import (
    DESTINATION_ENV,
    CloneError,
    Warehouse,
    check_names,
    clone_database,
)

logger = logging.getLogger("utility-snowflake-clone")


def _log_to_stderr() -> None:
    """Log to this run's stderr, which the workflow log shows, whatever configured logging."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


@click.command()
@click.option(
    "--destination-database",
    envvar=DESTINATION_ENV,
    help=f"The new database to clone into. Defaults to ${DESTINATION_ENV}.",
)
@click.option(
    "--source-database",
    help="The database to clone. Defaults to the warehouse's, $TARGET_SNOWFLAKE_DATABASE.",
)
@click.version_option(package_name="utility-snowflake-clone")
def main(destination_database: str | None, source_database: str | None) -> None:
    """Clone the workflow warehouse's Snowflake database, zero-copy, into a new database.

    Connects as the workflow's target-snowflake warehouse, whose settings reach it as
    TARGET_SNOWFLAKE_* variables. Does nothing when the new database exists already.
    """
    _log_to_stderr()
    try:
        warehouse = Warehouse.from_env(os.environ)
        source, destination = check_names(
            source_database or warehouse.database, destination_database
        )
    except CloneError as e:
        logger.error("Snowflake Clone: %s", e)  # noqa: TRY400
        sys.exit(2)

    # slow to import, and not needed for --help or a setting that is missing
    import snowflake.connector  # noqa: PLC0415
    from snowflake.connector.errors import Error as SnowflakeError  # noqa: PLC0415

    logger.info("Snowflake Clone: cloning database %s into %s", source, destination)
    try:
        with (
            snowflake.connector.connect(
                **{
                    **warehouse.connect_args(),
                    "database": source,
                }
            ) as connection,
            connection.cursor() as cursor,
        ):
            if clone_database(cursor, source, destination):
                logger.info("Snowflake Clone: cloned %s into %s", source, destination)
            else:
                logger.info(
                    "Snowflake Clone: database %s exists already, so nothing was cloned",
                    destination,
                )
    except SnowflakeError as e:
        # Snowflake's own message says why, e.g. a role without CREATE DATABASE on the account
        logger.error("Snowflake Clone: cloning %s into %s failed: %s", source, destination, e)  # noqa: TRY400
        sys.exit(1)
