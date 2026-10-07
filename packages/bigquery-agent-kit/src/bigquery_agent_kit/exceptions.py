"""Exception hierarchy for bigquery_agent_kit.

Catch :class:`BigQueryAgentKitError` to handle anything this package raises
on purpose, or a specific subclass to handle one failure mode.
"""

from __future__ import annotations


class BigQueryAgentKitError(Exception):
    """Base class for every error this package raises on purpose."""


class ReadOnlyQueryError(BigQueryAgentKitError, ValueError):
    """Raised when a query is not a single read-only SELECT/WITH statement.

    Also inherits ``ValueError`` so a caller matching on that (common for
    "bad input" handling) keeps catching it.
    """


class InvalidIdentifierError(BigQueryAgentKitError, ValueError):
    """Raised when a project/dataset id isn't a safe BigQuery identifier.

    Project and dataset ids reach this package's own SQL (schema/graph
    introspection) by direct string interpolation, not a parameterized
    query — BigQuery has no placeholder syntax for identifiers. This is the
    guard against an id containing a backtick or other character that could
    break out of that interpolation.
    """


class QueryTooExpensiveError(BigQueryAgentKitError, ValueError):
    """Raised when a query's own BigQuery dry run estimates it would exceed
    the configured byte-billed cap.

    Caught before the real (billed) query runs — a full-table scan is
    rejected outright instead of actually being billed for.
    """
