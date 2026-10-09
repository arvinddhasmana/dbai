"""Keep forwarded user identity scoped to one Boss request.

The Atlassian MCP Service is called with the signed-in user's Databricks token,
whereas other Boss integrations use the App service principal. A ``ContextVar``
keeps this distinction safe across concurrent async requests without placing
credentials in LangGraph state, checkpoints, or logs.
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from collections.abc import Iterator

_user_access_token: ContextVar[str | None] = ContextVar(
    "boss_user_access_token", default=None
)


@contextmanager
def use_user_access_token(token: str | None) -> Iterator[None]:
    """Temporarily bind an incoming user's token and always restore prior state."""
    context_token: Token[str | None] = _user_access_token.set(token)
    try:
        yield
    finally:
        _user_access_token.reset(context_token)


def current_user_access_token() -> str | None:
    """Return the token bound to the current async execution context, if any."""
    return _user_access_token.get()
