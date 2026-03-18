"""PICO input schema for structured biomedical queries."""

from pydantic import BaseModel


class PICOInput(BaseModel):
    """Structured query components for PICO search mode."""

    population: str | None = None
    intervention: str | None = None
    comparison: str | None = None
    outcome: str | None = None

    population_inferred: bool = False
    intervention_inferred: bool = False
    comparison_inferred: bool = False
    outcome_inferred: bool = False
