import os

from hypothesis import settings

# CI must not be flaky: derandomize makes Hypothesis deterministic.
settings.register_profile("ci", derandomize=True, deadline=None, max_examples=200)
settings.register_profile("dev", deadline=None, max_examples=60)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))
