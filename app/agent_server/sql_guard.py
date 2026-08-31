"""Validation for model-generated read-only SQL."""

import os
import re


CATALOG = os.getenv("DBAI_CATALOG", "globalmart")
ALLOWED_TABLES = frozenset(
    {
        f"{CATALOG}.supply_chain.dim_products",
        f"{CATALOG}.supply_chain.dim_vendors",
        f"{CATALOG}.supply_chain.fact_inventory_status",
    }
)
MAX_ROWS = 100
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(?:alter|analyze|attach|call|comment|copy|create|delete|detach|drop|grant|insert|merge|refresh|replace|revoke|truncate|update|use)\b",
    re.IGNORECASE,
)
TABLE_REFERENCE = re.compile(r"\b(?:from|join)\s+([`\w.-]+)", re.IGNORECASE)
LIMIT_CLAUSE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


def _normalize_identifier(identifier):
    return identifier.replace("`", "").lower()


def validate_read_only_sql(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL must be a non-empty string.")

    statement = sql.strip()
    if len(statement) > 8_000:
        raise ValueError("SQL exceeds the maximum length of 8,000 characters.")
    if "\x00" in statement or re.search(r"--|/\*|\*/|#", statement):
        raise ValueError("SQL comments and control characters are not allowed.")
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    if ";" in statement:
        raise ValueError("Multiple SQL statements are not allowed.")
    if not re.match(r"^(?:select|with)\b", statement, re.IGNORECASE):
        raise ValueError("Only SELECT or WITH queries are allowed.")
    if FORBIDDEN_KEYWORDS.search(statement):
        raise ValueError("The SQL contains a write or administrative operation.")

    references = TABLE_REFERENCE.findall(statement)
    if not references:
        raise ValueError("The query must read an approved Gold table.")
    unknown_tables = {
        _normalize_identifier(reference)
        for reference in references
        if _normalize_identifier(reference) not in ALLOWED_TABLES
    }
    if unknown_tables:
        names = ", ".join(sorted(unknown_tables))
        raise ValueError(f"Query references tables outside the allow-list: {names}.")

    limit_match = LIMIT_CLAUSE.search(statement)
    if limit_match and int(limit_match.group(1)) > MAX_ROWS:
        raise ValueError(f"LIMIT cannot exceed {MAX_ROWS} rows.")
    if not limit_match:
        statement = f"{statement}\nLIMIT {MAX_ROWS}"
    return statement
