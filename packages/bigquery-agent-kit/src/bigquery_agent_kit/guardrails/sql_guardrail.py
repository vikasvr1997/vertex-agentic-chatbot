"""The SQL-level guardrail: reject anything that isn't one read-only query.

This is the last line of defense between model-generated SQL and BigQuery.
It is a lexical guard — it strips comments and string/identifier literals,
then checks the remaining keywords — not a full SQL parser. Treat it as a
safety net layered on top of least-privilege IAM (grant the runtime
identity only ``roles/bigquery.dataViewer`` and ``roles/bigquery.jobUser``,
never a write role), not as the only control.
"""

from __future__ import annotations

import re

from bigquery_agent_kit.exceptions import InvalidIdentifierError, ReadOnlyQueryError

# BigQuery dataset ids are only ever letters, digits, and underscores.
# Project ids are narrower still in practice, but this also allows the
# legacy domain-scoped form ("example.com:my-project"). Deliberately not
# enforcing BigQuery's exact length limits here — that's a business rule,
# not a security boundary; only the character set matters for safely
# interpolating these into this package's own SQL.
_SAFE_DATASET_ID = re.compile(r"[A-Za-z0-9_]+")
_SAFE_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")

_FORBIDDEN_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "MERGE",
    "GRANT",
    "REVOKE",
    "CALL",
    "EXEC",
    "EXECUTE",
}
_ALLOWED_LEADING_KEYWORDS = {"SELECT", "WITH"}
_SQL_TOKEN_PATTERN = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"|" r"`(?:``|[^`])*`|(\w+)",
    re.DOTALL,
)


def validate_identifier(value: str, *, kind: str) -> None:
    """Reject anything that isn't a safe BigQuery project or dataset id.

    Project and dataset ids reach this package's own introspection SQL
    (``describe_dataset``/``describe_graphs``) by direct string
    interpolation — BigQuery has no placeholder syntax for identifiers, so
    a value containing a backtick or other SQL metacharacter could break
    out of that interpolation. ``kind`` is ``"project"`` or ``"dataset"``.
    """
    pattern = _SAFE_DATASET_ID if kind == "dataset" else _SAFE_PROJECT_ID
    if not pattern.fullmatch(value):
        raise InvalidIdentifierError(f"{value!r} is not a valid BigQuery {kind} id.")


def validate_read_only(sql: str) -> None:
    """Reject write operations while ignoring words inside literals and comments."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise ReadOnlyQueryError("Empty query.")
    if ";" in stripped:
        raise ReadOnlyQueryError("Multiple statements are not allowed.")

    words = [
        match.group(1).upper()
        for match in _SQL_TOKEN_PATTERN.finditer(stripped)
        if match.group(1) is not None
    ]
    if not words or words[0] not in _ALLOWED_LEADING_KEYWORDS:
        first_word = words[0] if words else ""
        raise ReadOnlyQueryError(
            f"Only SELECT/WITH queries are allowed; got a statement starting with '{first_word}'."
        )

    blocked = set(words) & _FORBIDDEN_KEYWORDS
    if blocked:
        raise ReadOnlyQueryError(
            f"Query contains forbidden keyword(s): {', '.join(sorted(blocked))}"
        )
