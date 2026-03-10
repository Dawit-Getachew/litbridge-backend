"""LLM prompt builders for PICO extraction and keyword expansion."""

from __future__ import annotations

PICO_EXTRACTION_SYSTEM = """\
You are a biomedical search strategy expert. Extract structured PICO components \
from the user's research question or PICO description.

Return a JSON object with this exact schema:
{
  "pico": {
    "P": [{"text": "...", "confidence": 0.0-1.0, "facet": "demographics|condition"}],
    "I": [{"text": "...", "confidence": 0.0-1.0, "facet": "specific_agent|delivery_channel"}],
    "C": [{"text": "...", "confidence": 0.0-1.0, "facet": "specific_agent|delivery_channel"}],
    "O": [{"text": "...", "confidence": 0.0-1.0, "facet": "target"}]
  },
  "atomic_targets": {
    "P": ["minimal noun phrase 1", ...],
    "I": ["minimal noun phrase 1", ...],
    "C": ["minimal noun phrase 1", ...],
    "O": ["minimal noun phrase 1", ...]
  },
  "modifiers": {
    "P": {"demographics": ["adults"], "population_filter": []},
    "I": {"polarity": []},
    "C": {},
    "O": {"time_window": [], "setting": []}
  }
}

Rules:
- Each PICO element should be a concise clinical term, not a full sentence.
- Atomic targets are minimal noun phrases suitable for MeSH mapping (e.g., "type 2 diabetes" not "patients with type 2 diabetes").
- Modifiers are contextual qualifiers that refine the search but are not core concepts.
- facet values: P uses "demographics" or "condition"; I/C use "specific_agent" or "delivery_channel"; O uses "target".
- If a PICO component cannot be determined, return an empty list for it.
- confidence should reflect how certain the extraction is (0.0 to 1.0).
- Return ONLY valid JSON, no markdown fences or explanation."""


KEYWORD_EXPANSION_SYSTEM = """\
You are a biomedical search strategy expert. Generate keyword synonyms for \
systematic review literature searching.

For each base term provided, generate relevant synonyms that a researcher \
would use to build a comprehensive search strategy.

Return a JSON object with this exact schema:
{
  "keywords": {
    "<base_term_1>": [
      {"term": "...", "variant": "synonym|abbreviation|spelling|lay_term|phrase_variant", "confidence": 0.0-1.0}
    ],
    "<base_term_2>": [...]
  }
}

Rules:
- Generate 3-7 synonyms per base term.
- Include medical synonyms, common abbreviations, spelling variants, and lay terms where applicable.
- variant must be one of: synonym, abbreviation, spelling, lay_term, phrase_variant.
- Do NOT include the base term itself as a synonym.
- Do NOT include overly broad or unrelated terms.
- Prefer terms that would appear in biomedical literature titles and abstracts.
- Return ONLY valid JSON, no markdown fences or explanation."""


def build_pico_extraction_messages(question: str) -> list[dict[str, str]]:
    """Build the message list for PICO extraction."""
    return [
        {"role": "system", "content": PICO_EXTRACTION_SYSTEM},
        {"role": "user", "content": question},
    ]


def build_keyword_expansion_messages(
    concept: str,
    base_terms: list[str],
) -> list[dict[str, str]]:
    """Build the message list for keyword expansion of one concept."""
    terms_str = ", ".join(f'"{t}"' for t in base_terms)
    user_msg = (
        f"Concept: {concept}\n"
        f"Base terms to expand: {terms_str}\n\n"
        f"Generate synonyms for each base term."
    )
    return [
        {"role": "system", "content": KEYWORD_EXPANSION_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
