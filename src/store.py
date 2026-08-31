"""DuckDB persistence for the long-format observations table.

Schema matches the spec's required shape exactly: anomaly flags, tiers,
reasons, and fence bounds are columns computed once by the pipeline, never
recomputed at render time by the dashboard or the AI agent (src/tools.py
reads is_anomaly/anomaly_reason directly).
"""

import duckdb
import pandas as pd

from config import DB_PATH

SCHEMA_SQL = """
CREATE OR REPLACE TABLE observations (
    timestamp TIMESTAMP NOT NULL,
    site_id VARCHAR NOT NULL,
    site_name VARCHAR NOT NULL,
    metric VARCHAR NOT NULL,
    value DOUBLE,
    unit VARCHAR NOT NULL,
    quality_flag VARCHAR NOT NULL,
    is_anomaly BOOLEAN NOT NULL,
    anomaly_tier INTEGER,
    anomaly_reason VARCHAR,
    fence_lower DOUBLE,
    fence_upper DOUBLE
)
"""


def load(long_df: pd.DataFrame, db_path=DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(SCHEMA_SQL)
        con.execute("INSERT INTO observations SELECT * FROM long_df")
    finally:
        con.close()


def connect(db_path=DB_PATH) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path), read_only=True)


if __name__ == "__main__":
    con = connect()
    print(con.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM observations").fetchdf())
    print(con.execute("SELECT metric, anomaly_tier, COUNT(*) FROM observations GROUP BY 1, 2 ORDER BY 1, 2").fetchdf())
    con.close()
