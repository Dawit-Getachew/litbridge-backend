"""Deduplication service for building golden publication records."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

import structlog
from rapidfuzz import fuzz

from src.core.config import Settings, get_settings
from src.core.exceptions import DeduplicationError
from src.schemas.enums import AgeGroup, OAStatus, QueryType, SourceType, StudyType
from src.schemas.records import RawRecord, UnifiedRecord


class _UnionFind:
    """Simple union-find with path compression and union by size."""

    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.component_size = [1] * size

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]

        while self.parent[item] != item:
            next_item = self.parent[item]
            self.parent[item] = root
            item = next_item
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return

        if self.component_size[left_root] < self.component_size[right_root]:
            left_root, right_root = right_root, left_root

        self.parent[right_root] = left_root
        self.component_size[left_root] += self.component_size[right_root]

    def size_of(self, item: int) -> int:
        return self.component_size[self.find(item)]


class DedupService:
    """Build golden records from raw multi-source publication records."""

    FUZZY_TITLE_THRESHOLD = 90
    SOFT_MATCH_WINDOW = 10

    # Tokens that carry no retrieval signal and should be dropped from the
    # query before computing the title-match boost. Kept in sync with
    # ``src/ai/adapters/base.py``'s trimmed ``_STOP_WORDS``.
    _BOOST_STOP_WORDS: frozenset[str] = frozenset(
        {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "by",
            "does",
            "for",
            "from",
            "how",
            "in",
            "into",
            "is",
            "it",
            "of",
            "on",
            "or",
            "that",
            "the",
            "to",
            "what",
            "when",
            "where",
            "which",
            "with",
            "without",
        }
    )

    # Pattern for extracting meaningful ≥3-char tokens from titles/queries.
    _TOKEN_PATTERN: re.Pattern[str] = re.compile(r"[a-z0-9\-]{3,}")

    def __init__(self, settings: Settings | None = None) -> None:
        self.logger = structlog.get_logger(__name__).bind(service="dedup_service")
        self.settings = settings or get_settings()

    def deduplicate(
        self,
        records: list[RawRecord],
        query: str | None = None,
        query_type: QueryType | None = None,
    ) -> list[UnifiedRecord]:
        """Deduplicate records into unified golden records.

        When ``query`` is provided and ``query_type`` is not BOOLEAN, clusters
        are re-ordered by a weighted Reciprocal Rank Fusion score combined
        with title-match and recency boosts (configurable via ``RANKING_*``
        settings). BOOLEAN queries keep their source-default (date) order to
        preserve PRISMA reproducibility. When ``query`` is ``None`` or every
        record has ``source_rank == 0`` (legacy callers / tests), the existing
        first-seen ordering is returned unchanged — so backward compatibility
        is preserved by construction.
        """
        if not records:
            return []

        try:
            union_find = _UnionFind(len(records))
            normalized_dois = [self._normalize_doi(record.doi) for record in records]
            normalized_pmids = [self._normalize_pmid(record.pmid) for record in records]
            normalized_titles = [self._normalize_title(record.title) for record in records]

            self._apply_doi_hard_match(union_find=union_find, normalized_dois=normalized_dois)
            self._apply_pmid_hard_match(
                records=records,
                union_find=union_find,
                normalized_pmids=normalized_pmids,
            )
            self._apply_soft_title_match(
                records=records,
                union_find=union_find,
                normalized_titles=normalized_titles,
            )

            clusters = self._build_clusters(records=records, union_find=union_find)
            rrf_applied = self._should_apply_rrf(
                query=query,
                query_type=query_type,
                clusters=clusters,
            )
            if rrf_applied:
                ordered_clusters = self._rank_clusters_by_rrf(
                    clusters=clusters, query=query,
                )
                ordered_clusters, mmr_applied = self._maybe_mmr_rerank(
                    clusters=ordered_clusters, query=query,
                )
            else:
                ordered_clusters = clusters
                mmr_applied = False
            unified = [
                self._build_golden_record(cluster=cluster)
                for cluster in ordered_clusters
            ]

            self.logger.info(
                "dedup_completed",
                input_records=len(records),
                output_records=len(unified),
                duplicates_removed=len(records) - len(unified),
                rrf_applied=rrf_applied,
                mmr_applied=mmr_applied,
                query_type=query_type.value if query_type else None,
                top5_sources=[
                    self._pick_winner(cluster).source.value
                    for cluster in ordered_clusters[:5]
                ],
            )
            return unified
        except Exception as exc:  # pragma: no cover - defensive wrapping
            self.logger.exception(
                "dedup_failed",
                input_records=len(records),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise DeduplicationError("Failed to deduplicate records.") from exc

    def _should_apply_rrf(
        self,
        query: str | None,
        query_type: QueryType | None,
        clusters: list[list[RawRecord]],
    ) -> bool:
        """Decide whether to re-rank clusters with weighted RRF.

        RRF is only applied when we have (a) a user-provided query to
        compute a title-match boost against, (b) a non-BOOLEAN query type
        (BOOLEAN is PRISMA-style; reviewers expect date-sorted output), and
        (c) at least one record carrying a non-zero ``source_rank`` so the
        RRF sum is non-degenerate. Any failure of these conditions falls
        back to the existing first-seen ordering — which is exactly what
        legacy tests and the workflow service expect.
        """
        if not query or not query.strip():
            return False
        if query_type is QueryType.BOOLEAN:
            return False
        for cluster in clusters:
            for record in cluster:
                if record.source_rank and record.source_rank > 0:
                    return True
        return False

    def _rank_clusters_by_rrf(
        self,
        clusters: list[list[RawRecord]],
        query: str | None,
    ) -> list[list[RawRecord]]:
        """Sort clusters by fused RRF score (+boosts) with deterministic ties.

        The sort key is, in order of priority:

            1. Descending fused score (RRF × title_boost × recency_boost)
            2. Descending publication year (newer wins at equal fused score)
            3. Descending distinct-source count (multi-source cluster wins)
            4. Ascending winner source enum value (alphabetical, stable)
            5. Ascending winner source_id (lexicographic tiebreaker of last
               resort, guarantees two identical runs return identical orders)
        """
        query_terms = self._query_terms(query)
        current_year = datetime.now(UTC).year

        scored: list[tuple[tuple[float, int, int, str, str], list[RawRecord]]] = []
        for cluster in clusters:
            winner = self._pick_winner(cluster)
            fused = self._fused_score(
                cluster=cluster,
                winner=winner,
                query_terms=query_terms,
                current_year=current_year,
            )
            sort_key = (
                -fused,
                -(winner.year or 0),
                -len({record.source for record in cluster}),
                winner.source.value,
                winner.source_id,
            )
            scored.append((sort_key, cluster))

        scored.sort(key=lambda entry: entry[0])
        return [cluster for _, cluster in scored]

    def _maybe_mmr_rerank(
        self,
        clusters: list[list[RawRecord]],
        query: str | None,
    ) -> tuple[list[list[RawRecord]], bool]:
        """Diversify the top-K clusters with Maximal Marginal Relevance.

        Applies only when ``RANKING_MMR_LAMBDA < 1.0`` (default 1.0 = off)
        and we have at least two clusters to reorder. Runs over the already
        RRF-sorted input so the long tail past ``RANKING_MMR_K`` stays in
        pure relevance order; only the head gets the diversity pass.

        Similarity is Jaccard over normalized title tokens — fast, no
        embeddings, deterministic. ``lambda`` balances relevance vs.
        diversity (1.0 pure relevance, 0.0 pure diversity).

        Returns ``(reordered_clusters, applied)`` so the caller can log
        whether MMR actually ran.
        """
        lam = self.settings.RANKING_MMR_LAMBDA
        if lam >= 1.0 or lam < 0.0 or len(clusters) < 2:
            return clusters, False

        k = min(self.settings.RANKING_MMR_K, len(clusters))
        if k < 2:
            return clusters, False

        query_terms = self._query_terms(query)
        current_year = datetime.now(UTC).year

        head = clusters[:k]
        tail = clusters[k:]

        winners: list[RawRecord] = [self._pick_winner(cluster) for cluster in head]
        title_tokens: list[frozenset[str]] = [
            frozenset(self._TOKEN_PATTERN.findall((winner.title or "").lower()))
            for winner in winners
        ]
        fused_scores: list[float] = [
            self._fused_score(
                cluster=cluster,
                winner=winner,
                query_terms=query_terms,
                current_year=current_year,
            )
            for cluster, winner in zip(head, winners, strict=True)
        ]

        remaining: list[int] = list(range(len(head)))
        selected: list[int] = [remaining.pop(0)]

        while remaining and len(selected) < k:
            best_index: int | None = None
            best_score: float | None = None
            for candidate in remaining:
                max_sim = max(
                    (
                        self._jaccard_similarity(
                            title_tokens[candidate], title_tokens[chosen],
                        )
                        for chosen in selected
                    ),
                    default=0.0,
                )
                score = lam * fused_scores[candidate] - (1.0 - lam) * max_sim
                if best_score is None or score > best_score:
                    best_score = score
                    best_index = candidate
            assert best_index is not None  # noqa: S101 - loop guarantees a pick
            selected.append(best_index)
            remaining.remove(best_index)

        reordered_head = [head[index] for index in selected]
        return reordered_head + tail, True

    @staticmethod
    def _jaccard_similarity(
        left: frozenset[str],
        right: frozenset[str],
    ) -> float:
        """Return Jaccard similarity over title-token sets in [0, 1]."""
        if not left or not right:
            return 0.0
        intersection = len(left & right)
        if intersection == 0:
            return 0.0
        return intersection / len(left | right)

    def _fused_score(
        self,
        cluster: list[RawRecord],
        winner: RawRecord,
        query_terms: frozenset[str],
        current_year: int,
    ) -> float:
        """Compute weighted RRF × title_boost × recency_boost for one cluster."""
        rrf_k = self.settings.RANKING_RRF_K
        source_weight = {
            SourceType.PUBMED: self.settings.RANKING_PUBMED_WEIGHT,
            SourceType.EUROPEPMC: 1.0,
            SourceType.OPENALEX: 1.0,
            SourceType.CLINICALTRIALS: 1.0,
        }

        rrf = 0.0
        for record in cluster:
            if not record.source_rank or record.source_rank <= 0:
                continue
            rrf += source_weight.get(record.source, 1.0) / (rrf_k + record.source_rank)

        if rrf <= 0.0:
            # No rank info available for this cluster. Zero means it lands at
            # the very end after sort; combined with the deterministic
            # tiebreakers, this preserves first-seen order among unranked
            # clusters (critical for backward compatibility).
            return 0.0

        title_boost = self._title_match_boost(winner.title, query_terms)
        recency_boost = self._recency_boost(winner.year, current_year=current_year)
        return rrf * title_boost * recency_boost

    def _title_match_boost(self, title: str | None, query_terms: frozenset[str]) -> float:
        """Return 1.0..(1.0 + alpha) based on query-term coverage in title.

        A title that contains every meaningful query term gets the full
        ``1 + RANKING_TITLE_BOOST`` lift. Partial coverage scales linearly.
        Short stop-word-only queries (e.g. ``"of the"``) are treated as
        neutral (1.0) to avoid artificially boosting every paper.
        """
        alpha = self.settings.RANKING_TITLE_BOOST
        if alpha <= 0.0 or not title or not query_terms:
            return 1.0
        title_tokens = set(self._TOKEN_PATTERN.findall(title.lower()))
        if not title_tokens:
            return 1.0
        matched = len(query_terms & title_tokens)
        return 1.0 + alpha * (matched / len(query_terms))

    def _recency_boost(self, year: int | None, *, current_year: int) -> float:
        """Return 1.0..(1.0 + beta) decaying linearly over 5 years.

        Current-year papers get the full lift; age 5+ (or missing year) is
        neutral. Cap keeps an outstanding older paper from being displaced
        by a mediocre recent one.
        """
        beta = self.settings.RANKING_RECENCY_BOOST
        if beta <= 0.0 or not year:
            return 1.0
        age = max(0, current_year - year)
        if age >= 5:
            return 1.0
        return 1.0 + beta * (1.0 - age / 5.0)

    def _query_terms(self, query: str | None) -> frozenset[str]:
        """Tokenize a query into lowercased ≥3-char tokens minus stop-words."""
        if not query:
            return frozenset()
        tokens = {
            token
            for token in self._TOKEN_PATTERN.findall(query.lower())
            if token not in self._BOOST_STOP_WORDS
        }
        return frozenset(tokens)

    def _pick_winner(self, cluster: list[RawRecord]) -> RawRecord:
        """Return the cluster's representative record (same heuristic as
        ``_build_golden_record``'s ``base`` selection, so ranking signals and
        the persisted UnifiedRecord stay consistent)."""
        return max(cluster, key=lambda record: self._completeness_score(record))

    def _apply_doi_hard_match(self, union_find: _UnionFind, normalized_dois: list[str | None]) -> None:
        doi_groups: dict[str, list[int]] = defaultdict(list)
        for index, doi in enumerate(normalized_dois):
            if doi:
                doi_groups[doi].append(index)

        for indices in doi_groups.values():
            if len(indices) < 2:
                continue
            anchor = indices[0]
            for duplicate_index in indices[1:]:
                union_find.union(anchor, duplicate_index)

    def _apply_pmid_hard_match(
        self,
        records: list[RawRecord],
        union_find: _UnionFind,
        normalized_pmids: list[str | None],
    ) -> None:
        unmatched_indices = [index for index in range(len(records)) if union_find.size_of(index) == 1]
        pmid_groups: dict[str, list[int]] = defaultdict(list)
        for index in unmatched_indices:
            pmid = normalized_pmids[index]
            if pmid:
                pmid_groups[pmid].append(index)

        for indices in pmid_groups.values():
            if len(indices) < 2:
                continue
            anchor = indices[0]
            for duplicate_index in indices[1:]:
                union_find.union(anchor, duplicate_index)

    def _apply_soft_title_match(
        self,
        records: list[RawRecord],
        union_find: _UnionFind,
        normalized_titles: list[str],
    ) -> None:
        unmatched_indices = [
            index
            for index in range(len(records))
            if union_find.size_of(index) == 1 and normalized_titles[index]
        ]
        if len(unmatched_indices) < 2:
            return

        sorted_candidates = sorted(
            unmatched_indices,
            key=lambda index: self._title_sort_key(normalized_titles[index]),
        )

        for offset, left_index in enumerate(sorted_candidates):
            left_title = normalized_titles[left_index]
            left_year = records[left_index].year
            left_numeric_tokens = self._numeric_tokens(left_title)

            comparison_limit = min(offset + self.SOFT_MATCH_WINDOW + 1, len(sorted_candidates))
            for right_offset in range(offset + 1, comparison_limit):
                right_index = sorted_candidates[right_offset]
                right_title = normalized_titles[right_index]
                right_year = records[right_index].year
                right_numeric_tokens = self._numeric_tokens(right_title)

                if not self._years_compatible(left_year, right_year):
                    continue

                # Avoid false positives for templated titles differing only by numbers.
                if left_numeric_tokens and right_numeric_tokens and left_numeric_tokens != right_numeric_tokens:
                    continue

                if self._title_length_gap_too_large(left_title, right_title):
                    continue

                score = fuzz.token_sort_ratio(left_title, right_title)
                if score >= self.FUZZY_TITLE_THRESHOLD:
                    union_find.union(left_index, right_index)

    def _build_clusters(self, records: list[RawRecord], union_find: _UnionFind) -> list[list[RawRecord]]:
        grouped_indices: dict[int, list[int]] = defaultdict(list)
        for index in range(len(records)):
            grouped_indices[union_find.find(index)].append(index)

        clusters = [
            [records[index] for index in indices]
            for _, indices in sorted(grouped_indices.items(), key=lambda item: item[1][0])
        ]
        return clusters

    def _build_golden_record(self, cluster: list[RawRecord]) -> UnifiedRecord:
        cluster_id = str(uuid4())

        base = self._pick_winner(cluster)

        title = base.title.strip() or self._first_non_empty_string(cluster, "title") or ""
        authors = list(base.authors) if base.authors else self._first_non_empty_list(cluster, "authors")
        journal = base.journal or self._first_non_empty_string(cluster, "journal")
        year = base.year if base.year is not None else self._first_non_empty_int(cluster, "year")
        doi = self._normalize_doi(base.doi or self._first_non_empty_string(cluster, "doi"))
        pmid = self._normalize_pmid(base.pmid or self._first_non_empty_string(cluster, "pmid"))
        pdf_url = base.pdf_url or self._first_non_empty_string(cluster, "pdf_url")
        abstract = self._longest_text(cluster, "abstract")

        oa_status = base.oa_status
        if oa_status is OAStatus.UNKNOWN:
            for record in cluster:
                if record.oa_status is not OAStatus.UNKNOWN:
                    oa_status = record.oa_status
                    break

        sources_found_in: list[SourceType] = []
        seen_sources: set[SourceType] = set()
        for record in cluster:
            if record.source in seen_sources:
                continue
            seen_sources.add(record.source)
            sources_found_in.append(record.source)

        age_groups, age_min, age_max = self._extract_age_metadata(cluster)
        study_type = self._extract_study_type(cluster, title, abstract)

        return UnifiedRecord(
            id=str(uuid4()),
            title=title,
            authors=authors,
            journal=journal,
            year=year,
            doi=doi,
            pmid=pmid,
            source=base.source,
            sources_found_in=sources_found_in,
            oa_status=oa_status,
            pdf_url=pdf_url,
            abstract=abstract,
            duplicate_cluster_id=cluster_id,
            age_groups=age_groups,
            age_min=age_min,
            age_max=age_max,
            study_type=study_type,
        )

    # -- ClinicalTrials.gov stdAge label -> AgeGroup mapping --
    _CT_AGE_MAP: dict[str, AgeGroup] = {
        "child": AgeGroup.CHILD,
        "adult": AgeGroup.ADULT,
        "older_adult": AgeGroup.OLDER_ADULT,
    }

    # -- OpenAlex/Crossref work-type -> StudyType mapping --
    _OPENALEX_TYPE_MAP: dict[str, StudyType] = {
        "clinical-trial": StudyType.INTERVENTIONAL,
        "article": StudyType.OBSERVATIONAL,
        "review": StudyType.OTHER,
        "book-chapter": StudyType.OTHER,
        "book": StudyType.OTHER,
        "dataset": StudyType.OTHER,
        "preprint": StudyType.OTHER,
        "dissertation": StudyType.OTHER,
        "editorial": StudyType.OTHER,
        "letter": StudyType.OTHER,
        "erratum": StudyType.OTHER,
        "report": StudyType.OTHER,
    }

    _STUDY_TYPE_PATTERNS: list[tuple[re.Pattern[str], StudyType]] = [
        (re.compile(r"\b(randomized|randomised|rct|controlled trial|phase [i1-4]+)\b", re.IGNORECASE), StudyType.INTERVENTIONAL),
        (re.compile(r"\b(cohort|cross.?sectional|case.?control|longitudinal|registry|surveillance|epidemiolog|prevalence)\b", re.IGNORECASE), StudyType.OBSERVATIONAL),
        (re.compile(r"\bexpanded access\b", re.IGNORECASE), StudyType.EXPANDED_ACCESS),
        (re.compile(r"\b(diagnostic accuracy|sensitivity and specificity|screening test|predictive value|ROC curve|biomarker validation)\b", re.IGNORECASE), StudyType.DIAGNOSTIC),
    ]

    _AGE_PATTERNS: list[tuple[re.Pattern[str], AgeGroup]] = [
        (re.compile(r"\b(child(?:ren)?|pediatric|paediatric|infant|neonat|adolescent|juvenile)\b", re.IGNORECASE), AgeGroup.CHILD),
        (re.compile(r"\b(adult|grown.?up)\b", re.IGNORECASE), AgeGroup.ADULT),
        (re.compile(r"\b(elder|elderly|geriatric|older adult|aged (?:6[5-9]|[7-9]\d|1\d\d))\b", re.IGNORECASE), AgeGroup.OLDER_ADULT),
    ]

    def _extract_age_metadata(
        self, cluster: list[RawRecord],
    ) -> tuple[list[AgeGroup], int | None, int | None]:
        """Derive age groups and numeric bounds from cluster records."""
        age_groups: set[AgeGroup] = set()
        age_min: int | None = None
        age_max: int | None = None

        for record in cluster:
            groups, lo, hi = self._age_from_raw(record)
            age_groups.update(groups)
            if lo is not None:
                age_min = lo if age_min is None else min(age_min, lo)
            if hi is not None:
                age_max = hi if age_max is None else max(age_max, hi)

        if not age_groups:
            text = self._combined_text(cluster)
            for pattern, group in self._AGE_PATTERNS:
                if pattern.search(text):
                    age_groups.add(group)

        ordered = sorted(age_groups, key=lambda g: list(AgeGroup).index(g))
        return ordered, age_min, age_max

    def _age_from_raw(self, record: RawRecord) -> tuple[list[AgeGroup], int | None, int | None]:
        """Extract age metadata from ClinicalTrials raw_data or return empty."""
        raw = record.raw_data
        if record.source is not SourceType.CLINICALTRIALS or not raw:
            return [], None, None

        protocol = raw.get("protocolSection", {})
        if not isinstance(protocol, dict):
            return [], None, None

        eligibility = protocol.get("eligibilityModule", {})
        if not isinstance(eligibility, dict):
            return [], None, None

        groups: list[AgeGroup] = []
        std_ages = eligibility.get("stdAges", [])
        if isinstance(std_ages, list):
            for label in std_ages:
                if isinstance(label, str):
                    mapped = self._CT_AGE_MAP.get(label.strip().lower().replace(" ", "_"))
                    if mapped and mapped not in groups:
                        groups.append(mapped)

        lo = self._parse_age_string(eligibility.get("minimumAge"))
        hi = self._parse_age_string(eligibility.get("maximumAge"))
        return groups, lo, hi

    @staticmethod
    def _parse_age_string(value: object) -> int | None:
        """Parse ClinicalTrials age strings like '18 Years' to integer years."""
        if not isinstance(value, str):
            return None
        match = re.search(r"(\d+)", value)
        if not match:
            return None
        number = int(match.group(1))
        lower = value.lower()
        if "month" in lower:
            return max(number // 12, 0)
        if "day" in lower:
            return 0
        return number

    def _extract_study_type(
        self, cluster: list[RawRecord], title: str, abstract: str | None,
    ) -> StudyType | None:
        """Derive study type from structured fields or text heuristics."""
        for record in cluster:
            st = self._study_type_from_raw(record)
            if st is not None:
                return st

        text = f"{title} {abstract or ''}"
        for pattern, study_type in self._STUDY_TYPE_PATTERNS:
            if pattern.search(text):
                return study_type
        return None

    def _study_type_from_raw(self, record: RawRecord) -> StudyType | None:
        """Extract study type from ClinicalTrials or OpenAlex raw_data."""
        raw = record.raw_data
        if not raw:
            return None

        if record.source is SourceType.CLINICALTRIALS:
            protocol = raw.get("protocolSection", {})
            if isinstance(protocol, dict):
                design = protocol.get("designModule", {})
                if isinstance(design, dict):
                    raw_type = design.get("studyType", "")
                    if isinstance(raw_type, str):
                        mapped = raw_type.strip().lower().replace(" ", "_")
                        try:
                            return StudyType(mapped)
                        except ValueError:
                            if mapped:
                                return StudyType.OTHER
            return None

        if record.source is SourceType.OPENALEX:
            work_type = raw.get("type", "")
            if isinstance(work_type, str) and work_type.strip():
                return self._OPENALEX_TYPE_MAP.get(work_type.strip().lower())

        return None

    @staticmethod
    def _combined_text(cluster: list[RawRecord]) -> str:
        """Concatenate title + abstract from all cluster records for heuristic matching."""
        parts: list[str] = []
        for record in cluster:
            if record.title:
                parts.append(record.title)
            if record.abstract:
                parts.append(record.abstract)
        return " ".join(parts)

    @staticmethod
    def _normalize_doi(doi: str | None) -> str | None:
        if not doi:
            return None

        normalized = doi.strip().lower()
        prefixes = (
            "https://doi.org/",
            "http://doi.org/",
            "doi.org/",
            "doi:",
        )
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break
        return normalized or None

    @staticmethod
    def _normalize_pmid(pmid: str | None) -> str | None:
        if not pmid:
            return None
        normalized = pmid.strip()
        return normalized or None

    @staticmethod
    def _normalize_title(title: str | None) -> str:
        if not title:
            return ""

        normalized = title.lower().strip().replace("&", " and ")
        normalized = re.sub(r"[^a-z0-9\s]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    @staticmethod
    def _title_sort_key(normalized_title: str) -> str:
        tokens = [token for token in normalized_title.split(" ") if token]
        return " ".join(sorted(tokens))

    @staticmethod
    def _numeric_tokens(normalized_title: str) -> set[str]:
        return {token for token in normalized_title.split(" ") if token.isdigit()}

    @staticmethod
    def _title_length_gap_too_large(left_title: str, right_title: str) -> bool:
        max_len = max(len(left_title), len(right_title))
        min_len = min(len(left_title), len(right_title))
        if max_len == 0:
            return False
        return (max_len - min_len) / max_len > 0.5

    @staticmethod
    def _years_compatible(left_year: int | None, right_year: int | None) -> bool:
        if left_year is None or right_year is None:
            return True
        return abs(left_year - right_year) <= 1

    @staticmethod
    def _completeness_score(record: RawRecord) -> int:
        score = 0
        if record.authors:
            score += 1
        if record.journal:
            score += 1
        if record.year is not None:
            score += 1
        if record.doi:
            score += 1
        if record.pmid:
            score += 1
        if record.abstract:
            score += 1
        if record.pdf_url:
            score += 1
        if record.oa_status is not OAStatus.UNKNOWN:
            score += 1
        if record.raw_data:
            score += 1
        return score

    @staticmethod
    def _first_non_empty_string(cluster: list[RawRecord], field_name: str) -> str | None:
        for record in cluster:
            value = getattr(record, field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _first_non_empty_list(cluster: list[RawRecord], field_name: str) -> list[str]:
        for record in cluster:
            value = getattr(record, field_name)
            if isinstance(value, list) and value:
                return list(value)
        return []

    @staticmethod
    def _first_non_empty_int(cluster: list[RawRecord], field_name: str) -> int | None:
        for record in cluster:
            value = getattr(record, field_name)
            if isinstance(value, int):
                return value
        return None

    @staticmethod
    def _longest_text(cluster: list[RawRecord], field_name: str) -> str | None:
        candidates: list[str] = []
        for record in cluster:
            value = getattr(record, field_name)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
        if not candidates:
            return None
        return max(candidates, key=len)
