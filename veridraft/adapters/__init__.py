"""Importing this package registers every v1 adapter + documented stub.

Import side effects trigger the `@register(...)` decorators. The harness imports
this module once at startup so the registry is populated before preflight.
"""
from __future__ import annotations

from . import (  # noqa: F401  (imported for registration side effects)
    detector_binoculars,
    engine_minimal_latex,
    engine_minimal_patent,
    engine_paperorchestra,
    engine_patent_llm,
    sink_latex_pdf,
    source_caw02_bundle,
    stubs,
)
