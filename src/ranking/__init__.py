"""Pluggable reranking primitives that sit above the dedup/RRF pipeline.

These modules are deliberately import-light and stateless per call so they
can be wired into DedupService (and the streaming path) without forcing
every caller to hold a long-lived reranker instance.
"""

from src.ranking.bm25_reranker import BM25Reranker
from src.ranking.medcpt_reranker import MedCPTReranker

__all__ = ["BM25Reranker", "MedCPTReranker"]
