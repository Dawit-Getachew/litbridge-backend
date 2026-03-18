"""LLM prompt builders for PICO extraction, keyword expansion, and paper metadata."""

from __future__ import annotations

PICO_EXTRACTION_SYSTEM = """\
You are a biomedical search strategy expert. Extract structured PICO components \
from the user's research question or PICO description.

CRITICAL: You MUST return at least 1-3 terms for EVERY PICO component (P, I, C, O). \
No component may be left empty. If a component is not explicitly stated by the user, \
infer plausible candidates based on clinical context, MeSH conventions, and standard \
systematic review practice. Mark inferred elements with "inferred": true.

Common inference guidelines:
- C (Comparison): If not stated, suggest "placebo", "standard of care", "usual care", \
  "no intervention", or an appropriate active comparator based on the intervention.
- I (Intervention): Infer from the research context — what treatment or exposure is \
  being studied given the population and outcome.
- O (Outcome): Infer clinically relevant outcomes for the given population and intervention \
  (e.g., mortality, remission, symptom improvement, adverse events).
- P (Population): Infer the target patient group from the intervention and outcome context.

Return a JSON object with this exact schema:
{
  "pico": {
    "P": [{"text": "...", "confidence": 0.0-1.0, "inferred": false, "facet": "demographics|condition"}],
    "I": [{"text": "...", "confidence": 0.0-1.0, "inferred": false, "facet": "specific_agent|delivery_channel"}],
    "C": [{"text": "...", "confidence": 0.0-1.0, "inferred": true, "facet": "specific_agent|delivery_channel"}],
    "O": [{"text": "...", "confidence": 0.0-1.0, "inferred": false, "facet": "target"}]
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
- "inferred": false when the element was explicitly stated by the user; true when you inferred it.
- confidence should reflect how certain the extraction is (0.0 to 1.0). Inferred elements typically have lower confidence (0.3-0.7).
- EVERY component (P, I, C, O) MUST have at least one element. Never return an empty list.
- Return ONLY valid JSON, no markdown fences or explanation."""


PICO_FILL_SYSTEM = """\
You are a biomedical search strategy expert. The user has provided a partial PICO \
(Population, Intervention, Comparison, Outcome) query with some elements filled in \
and others missing. Your job is to fill the missing elements with clinically plausible \
suggestions based on MeSH conventions and systematic review best practices.

For each missing element, suggest 1-3 concise clinical terms that would form a \
reasonable systematic review query when combined with the provided elements.

Common defaults:
- C (Comparison): "placebo", "standard of care", "usual care", "no intervention", \
  or a domain-appropriate active comparator.
- I (Intervention): Infer the most likely treatment or exposure given the population and outcome.
- O (Outcome): Suggest primary outcomes standard for the condition (mortality, remission, \
  symptom scores, adverse events, quality of life).
- P (Population): Infer from the intervention and outcome context.

Return a JSON object:
{
  "population": ["term1", "term2"],
  "intervention": ["term1"],
  "comparison": ["term1", "term2", "term3"],
  "outcome": ["term1", "term2"]
}

Rules:
- Only fill elements that are currently empty/missing. Return empty lists for elements already provided.
- Terms should be concise clinical noun phrases suitable for MeSH mapping.
- Prefer well-established MeSH terms over informal language.
- Return ONLY valid JSON, no markdown fences or explanation."""


PAPER_METADATA_EXTRACTION_SYSTEM = """\
You are a biomedical research data extraction expert. Extract structured metadata \
from a research paper's title and abstract for use in a systematic review evidence table.

For each field, extract the relevant information if present. If the information \
cannot be determined from the title and abstract, use exactly "Not reported".

Return a JSON object with this exact schema:
{
  "study_details": "Brief description of study purpose and context",
  "study_design": "e.g., RCT, cohort study, meta-analysis, case-control, cross-sectional",
  "setting": "e.g., hospital, community, primary care, multicenter",
  "interventions": "Treatment or exposure being studied",
  "sample_size": "Number of participants (e.g., 'N=150' or '150 patients')",
  "primary_outcome": "Main outcome measure reported",
  "secondary_outcome": "Additional outcome measures, or 'Not reported'",
  "primary_statistics": "Key statistical results for primary outcome (e.g., 'OR 2.3, 95% CI 1.1-4.8, p=0.02')",
  "secondary_statistics": "Statistical results for secondary outcomes, or 'Not reported'",
  "key_findings": "1-2 sentence summary of main conclusions"
}

Rules:
- Be concise. Each field should be 1-2 sentences maximum.
- Use exact values from the paper when available (sample sizes, p-values, confidence intervals).
- For study_design, use standard terminology (RCT, cohort, case-control, etc.).
- If a field genuinely cannot be determined from the abstract, return "Not reported".
- Do not fabricate or infer statistics not present in the abstract.
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


def build_pico_fill_messages(
    population: str | None,
    intervention: str | None,
    comparison: str | None,
    outcome: str | None,
) -> list[dict[str, str]]:
    """Build the message list for filling missing PICO elements in direct search."""
    parts: list[str] = []
    mapping = {"P": population, "I": intervention, "C": comparison, "O": outcome}
    provided: list[str] = []
    missing: list[str] = []

    for key, val in mapping.items():
        if val and val.strip():
            provided.append(f"  {key}: {val.strip()}")
        else:
            missing.append(key)

    parts.append("Provided PICO elements:")
    parts.extend(provided) if provided else parts.append("  (none)")
    parts.append(f"\nMissing elements to fill: {', '.join(missing)}")

    return [
        {"role": "system", "content": PICO_FILL_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
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


def build_paper_metadata_messages(
    title: str,
    abstract: str | None,
) -> list[dict[str, str]]:
    """Build the message list for extracting structured metadata from a paper."""
    user_content = f"Title: {title}"
    if abstract and abstract.strip():
        user_content += f"\n\nAbstract: {abstract.strip()}"
    else:
        user_content += "\n\nAbstract: Not available."

    return [
        {"role": "system", "content": PAPER_METADATA_EXTRACTION_SYSTEM},
        {"role": "user", "content": user_content},
    ]
