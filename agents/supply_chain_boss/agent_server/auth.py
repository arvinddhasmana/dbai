"""Request-scoped identity for user-governed Databricks MCP Services."""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from collections.abc import Iterator

_user_access_token: ContextVar[str | None] = ContextVar(
    "boss_user_access_token", default=None
)


@contextmanager
def use_user_access_token(token: str | None) -> Iterator[None]:
    context_token: Token[str | None] = _user_access_token.set(token)
    try:
        yield
    finally:
        _user_access_token.reset(context_token)


def current_user_access_token() -> str | None:
    return _user_access_token.get()
