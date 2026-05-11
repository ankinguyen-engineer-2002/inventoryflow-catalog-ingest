"""Dagster resources for Track B.

Centralises connection configuration so individual assets remain pure
transformations. Resources are injected via Dagster's standard dependency
mechanism (see definitions.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from dagster import ConfigurableResource
from pyiceberg.catalog import Catalog, load_catalog


@dataclass(frozen=True)
class S3Config:
    """S3-compatible storage config (MinIO local, R2 production)."""

    endpoint: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"


class IcebergCatalogResource(ConfigurableResource):
    """Wraps a pyiceberg REST catalog connection.

    The REST catalog protocol means clients (Polars, DuckDB, Spark, Trino)
    can all read the same tables through a single metadata layer — the
    key vendor-neutrality property called out in ADR-008.
    """

    rest_uri: str = "http://localhost:8181"
    warehouse: str = "s3://catalog-warehouse/"
    s3_endpoint: str = "http://localhost:9100"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"

    def load(self) -> Catalog:
        os.environ["AWS_ACCESS_KEY_ID"] = self.s3_access_key
        os.environ["AWS_SECRET_ACCESS_KEY"] = self.s3_secret_key
        os.environ["AWS_REGION"] = self.s3_region

        return load_catalog(
            "rest",
            **{
                "type": "rest",
                "uri": self.rest_uri,
                "warehouse": self.warehouse,
                "s3.endpoint": self.s3_endpoint,
                "s3.access-key-id": self.s3_access_key,
                "s3.secret-access-key": self.s3_secret_key,
                "s3.path-style-access": "true",
            },
        )


class PostgresServingResource(ConfigurableResource):
    """Connection string for the serving-layer PostgreSQL.

    Track B's gold layer materialises to Iceberg and is also synchronised
    to this PostgreSQL instance so the existing Fastify catalog API in
    Track A continues to serve reads without modification.
    """

    dsn: str = "postgresql://dev:dev@localhost:5433/catalog"


class SourceXlsxResource(ConfigurableResource):
    """Pointer to the source Excel file."""

    path: str = "../shared/sample-data/example.xlsx"


def default_resources() -> dict[str, Any]:
    return {
        "iceberg_catalog": IcebergCatalogResource(),
        "postgres": PostgresServingResource(),
        "source_xlsx": SourceXlsxResource(),
    }
