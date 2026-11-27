"""Every statement whychanged sends is checked to be a single read-only SELECT.

Structural, not textual. A denylist of words is defeated by a comment, a case
change, or a string literal; a parse tree is not.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


class UnsafeSQLError(ValueError):
    """The statement is not a single read-only SELECT."""


# Expression types that write, change structure, or grant. Presence of any of these
# anywhere in the tree, including inside a CTE, rejects the statement.
_FORBIDDEN: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Merge,
    exp.Command,  # anything sqlglot could not classify: COPY, VACUUM, CALL, ...
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Set,
    exp.Use,
)


def assert_read_only(sql: str, dialect: str = "postgres") -> exp.Expression:
    """Parse `sql` and return its tree, or raise if it is anything but one SELECT."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except sqlglot.ParseError as exc:
        raise UnsafeSQLError(f"could not parse statement: {exc}") from exc

    if not statements:
        raise UnsafeSQLError("empty statement")
    if len(statements) > 1:
        raise UnsafeSQLError(
            f"{len(statements)} statements in one call; only a single SELECT is allowed"
        )

    tree = statements[0]
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN):
            raise UnsafeSQLError(f"{type(node).__name__.upper()} is not permitted; SELECT only")

    if not isinstance(tree, exp.Select | exp.Union | exp.Subquery | exp.With):
        raise UnsafeSQLError(f"{type(tree).__name__.upper()} is not a SELECT")

    # `SELECT ... INTO new_table` parses as a Select and writes. So does `FOR UPDATE`,
    # which takes row locks on someone's production table.
    if tree.args.get("into") is not None:
        raise UnsafeSQLError("SELECT ... INTO writes a table; not permitted")
    if tree.args.get("locks"):
        raise UnsafeSQLError("locking clauses (FOR UPDATE / FOR SHARE) are not permitted")

    return tree
