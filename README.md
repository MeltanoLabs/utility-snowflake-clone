# utility-snowflake-clone

A [Meltano](https://meltano.com) utility that clones a Snowflake database into a new one with
Snowflake's zero-copy [`CREATE DATABASE … CLONE`](https://docs.snowflake.com/en/sql-reference/sql/create-clone).
The clone is quick, and takes no extra storage until either database changes.

It connects as the workflow's `target-snowflake` warehouse and clones that warehouse's database.
Its one setting is the new database's name.

## How it runs

Put it in a workflow after a `target-snowflake` warehouse:

```yaml
plugins:
  loaders:
  - name: target-snowflake
    variant: meltanolabs
  utilities:
  - name: utility-snowflake-clone
    variant: meltanolabs
    pip_url: git+https://github.com/MeltanoLabs/utility-snowflake-clone.git@v0.1.0
```

```sh
meltano invoke utility-snowflake-clone
```

The warehouse's settings reach the utility as `TARGET_SNOWFLAKE_*` environment variables, as Meltano
Cloud gives every plugin in a workflow. It reads:

| Variable | Used for |
| --- | --- |
| `TARGET_SNOWFLAKE_ACCOUNT`, `_USER` | The connection (required) |
| `TARGET_SNOWFLAKE_DATABASE` | The database cloned (required) |
| `TARGET_SNOWFLAKE_ROLE`, `_WAREHOUSE` | The connection, when set |
| `TARGET_SNOWFLAKE_PRIVATE_KEY` (or `_PRIVATE_KEY_PATH`), `_PRIVATE_KEY_PASSPHRASE` | Key-pair authentication: PEM, base64 of PEM, or base64 of DER |
| `TARGET_SNOWFLAKE_PASSWORD` | Password authentication, without a key |

## Settings

| Setting | Environment variable | Description |
| --- | --- | --- |
| `destination_database` (required) | `UTILITY_SNOWFLAKE_CLONE_DESTINATION_DATABASE` | The new database to clone into. Letters, digits, `_` and `$`, starting with a letter or `_`; Snowflake names it in upper case. |

Without `destination_database` the utility doesn't run, so a workflow installed in production,
where it is not set, stays a draft in Meltano Cloud. A clone of the workspace sets it.

`--destination-database` and `--source-database` override the setting and the warehouse's database.

## What it does

1. Checks both names are unquoted Snowflake identifiers, and that the destination is not the
   warehouse's own database.
1. Looks for the destination by its exact name. If it exists already, it does nothing and exits 0,
   so running it again is safe.
1. Otherwise runs `CREATE DATABASE IF NOT EXISTS <DESTINATION> CLONE <SOURCE>`.

Exit codes: `0` cloned or already there, `1` Snowflake refused (its message is logged), `2` a
setting is missing or invalid.

## Snowflake permissions

The warehouse's role needs:

- `CREATE DATABASE` on the account
- `USAGE` on the source database and its schemas
- `SELECT` on the tables and views in them

```sql
GRANT CREATE DATABASE ON ACCOUNT TO ROLE <role>;
```

The new database belongs to that role.

## Development

```sh
uv sync
uv run pytest
uvx --with=tox-uv tox -e typing
```
