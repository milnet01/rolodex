"""Shared test setup. pytest loads this before any test module imports rolodex.

The suite asserts English UI text, so it pins the UI language: rolodex chooses its translation
catalog once, at import (ROLO-0028), from LANGUAGE first. Without this, a run on a machine set
to a shipped language would fail on every asserted string.
"""

import os

os.environ["LANGUAGE"] = "en"
