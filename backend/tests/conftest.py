"""Keep the module-level API TestClients independent across tests."""

import sys

import pytest


@pytest.fixture(autouse=True)
def reset_application_request_limits():
    # Importing main here would initialize settings before some tests set DEMO_MODE.
    main_module = sys.modules.get("main")
    limiter = getattr(main_module, "request_limiter", None)
    if limiter is not None:
        limiter.clear()
    yield
    if limiter is not None:
        limiter.clear()
