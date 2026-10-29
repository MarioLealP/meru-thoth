"""What routes get injected - overridden in tests."""

from functools import cache

from sqlalchemy import Engine

from thoth.db import make_engine


@cache
def get_engine() -> Engine:
    return make_engine()
