"""PRISMA metrics computation service."""

from __future__ import annotations

from src.schemas.enums import OAStatus
from src.schemas.prisma import PrismaCounts, PrismaFilters
from src.schemas.records import UnifiedRecord


class PrismaService:
    """Compute PRISMA counts for deduplicated records and active filters."""

    def compute_counts(
        self,
        identified: int,
        records: list[UnifiedRecord],
        filters: PrismaFilters | None = None,
    ) -> PrismaCounts:
        """Return PRISMA counts derived from records and optional filters."""
        after_deduplication = len(records)
        screened = after_deduplication

        filtered_records = list(records)
        if filters is not None:
            filtered_records = self._apply_filters(records=filtered_records, filters=filters)

        excluded = screened - len(filtered_records)
        oa_retrieved = sum(1 for record in filtered_records if record.oa_status is OAStatus.OPEN)

        return PrismaCounts(
            identified=identified,
            after_deduplication=after_deduplication,
            screened=screened,
            excluded=excluded,
            oa_retrieved=oa_retrieved,
        )

    def _apply_filters(self, records: list[UnifiedRecord], filters: PrismaFilters) -> list[UnifiedRecord]:
        filtered = records

        if filters.year_from is not None:
            filtered = [
                record
                for record in filtered
                if record.year is not None and record.year >= filters.year_from
            ]

        if filters.year_to is not None:
            filtered = [
                record
                for record in filtered
                if record.year is not None and record.year <= filters.year_to
            ]

        if filters.sources:
            allowed_sources = set(filters.sources)
            filtered = [record for record in filtered if record.source in allowed_sources]

        if filters.open_access_only:
            filtered = [record for record in filtered if record.oa_status is OAStatus.OPEN]

        if filters.age_groups:
            allowed_groups = set(filters.age_groups)
            filtered = [
                record for record in filtered
                if set(record.age_groups) & allowed_groups
            ]

        if filters.age_min is not None:
            filtered = [
                record for record in filtered
                if record.age_max is None or record.age_max >= filters.age_min
            ]

        if filters.age_max is not None:
            filtered = [
                record for record in filtered
                if record.age_min is None or record.age_min <= filters.age_max
            ]

        if filters.study_types:
            allowed_types = set(filters.study_types)
            filtered = [
                record for record in filtered
                if record.study_type is not None and record.study_type in allowed_types
            ]

        return filtered
