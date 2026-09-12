from collections.abc import Callable

import pytest

from tests.support.cases import run_case


@pytest.fixture
def case() -> Callable[..., dict[str, object]]:
    return run_case
