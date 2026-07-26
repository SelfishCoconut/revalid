"""Shared fixtures and deterministic LLM stand-ins for the test suite.

The FR-03 extraction stand-in lives in :mod:`tests._extract_helpers` so every
tier drives the same offline ``FunctionModel`` (no network): it turns a whole
report into its list of findings in one call, matching the production shape
(ADR-0047).
"""

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from revalid.extract import (
    ExtractedFinding,
    ReportMetadata,
    build_extraction_agent,
    build_metadata_agent,
)
from tests._extract_helpers import fake_extractor


@pytest.fixture
def extraction_agent() -> Agent[None, list[ExtractedFinding]]:
    """A finding-extraction agent backed by a deterministic FunctionModel (no network)."""
    return build_extraction_agent(FunctionModel(fake_extractor))


@pytest.fixture
def metadata_agent() -> Agent[None, ReportMetadata]:
    """A document-metadata agent backed by TestModel — valid output, no network (#133)."""
    return build_metadata_agent(TestModel())
