"""NCBI MedCPT cross-encoder reranker with pluggable backends.

Gated entirely behind ``settings.RANKING_MEDCPT``. Off by default because
activation requires either (a) ONNX weights exported via
``scripts/export_medcpt_onnx.py`` and bundled into the Docker image, or
(b) an external inference endpoint URL / HF token. In both cases the
feature flag + explicit config act as the safety net.

Four backends are supported, selectable via ``settings.RANKING_MEDCPT_BACKEND``:

* ``onnx`` (DEFAULT) — quantized ONNX file loaded in-process via
  onnxruntime. ~60 ms for 100 candidates on 2 vCPU; no network; no torch.
* ``sidecar`` — HTTP POST to a Coolify sidecar container exposing the
  same rerank interface. Useful when you want backend isolation.
* ``hf_endpoints`` — Hugging Face Inference Endpoints URL. Managed, but
  adds ~200-400 ms network latency and ~$24/month always-on cost.
* ``hf_api`` — Hugging Face serverless pay-per-call API. Cheapest for
  low volume; subject to free-tier rate limits.

The reranker returns the *same* candidate list reordered — no records
are added or dropped. Any error inside the backend (model missing,
network glitch, tokenizer failure) yields the original order, so a
misconfigured flag cannot break search.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.core.config import Settings
    from src.schemas.records import RawRecord


logger = structlog.get_logger(__name__).bind(component="medcpt_reranker")


@dataclass(slots=True)
class _ScoredPair:
    index: int
    score: float


class MedCPTReranker:
    """Cross-encoder reranker for the top-K fused candidates.

    Process-wide singleton so the heavy ONNX session + tokenizer load once
    at first use. The lock is coarse-grained because loading happens at
    most twice per worker (one per thread race) and scoring itself is
    already GIL-bound, so finer locking would add noise without benefit.
    """

    _instance: "MedCPTReranker | None" = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logger
        self._backend_ready: bool = False
        self._onnx_session = None  # type: ignore[assignment]
        self._tokenizer = None  # type: ignore[assignment]
        self._max_length: int = 512

    @classmethod
    def get(cls, settings: Settings) -> "MedCPTReranker":
        """Return the process-wide singleton, creating it on first call."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(settings=settings)
        return cls._instance

    def rerank(
        self,
        *,
        query: str,
        records: list[RawRecord],
    ) -> list[RawRecord]:
        """Return ``records`` reordered by MedCPT relevance; original on error."""
        if not query or not query.strip() or len(records) < 2:
            return records

        backend = self.settings.RANKING_MEDCPT_BACKEND
        try:
            if backend == "onnx":
                scores = self._score_onnx(query=query, records=records)
            elif backend == "sidecar":
                scores = self._score_sidecar(query=query, records=records)
            elif backend == "hf_endpoints":
                scores = self._score_hf_endpoints(query=query, records=records)
            elif backend == "hf_api":
                scores = self._score_hf_api(query=query, records=records)
            else:
                self.logger.warning("medcpt_unknown_backend", backend=backend)
                return records
        except Exception as exc:  # noqa: BLE001 - never break search on rerank
            self.logger.warning(
                "medcpt_rerank_failed",
                backend=backend,
                candidate_count=len(records),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return records

        if not scores or len(scores) != len(records):
            return records

        scored_pairs = [
            _ScoredPair(index=index, score=float(score))
            for index, score in enumerate(scores)
        ]
        scored_pairs.sort(key=lambda pair: (-pair.score, pair.index))
        return [records[pair.index] for pair in scored_pairs]

    # ----- ONNX backend ---------------------------------------------------

    def _ensure_onnx_ready(self) -> None:
        """Lazily load ONNX session + tokenizer. Raises on dependency errors."""
        if self._backend_ready:
            return
        import onnxruntime as ort  # local: only required for onnx backend
        from pathlib import Path
        from transformers import AutoTokenizer

        model_path = Path(self.settings.RANKING_MEDCPT_MODEL_PATH)
        if not model_path.exists():
            raise FileNotFoundError(
                f"MedCPT ONNX model path does not exist: {model_path}. "
                "Run scripts/export_medcpt_onnx.py first.",
            )

        onnx_file = self._locate_onnx_file(model_path)
        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = 0  # let ORT decide
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._onnx_session = ort.InferenceSession(
            str(onnx_file), sess_options=session_options, providers=["CPUExecutionProvider"],
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(model_path))
        self._backend_ready = True

    @staticmethod
    def _locate_onnx_file(model_path):  # type: ignore[no-untyped-def]
        """Pick the quantized ONNX file from the export directory."""
        from pathlib import Path

        model_path = Path(model_path)
        preferred_names = (
            "model_quantized.onnx",
            "model-quantized.onnx",
            "model.onnx",
        )
        for name in preferred_names:
            candidate = model_path / name
            if candidate.exists():
                return candidate
        matches = list(model_path.glob("*.onnx"))
        if matches:
            return matches[0]
        raise FileNotFoundError(f"No ONNX file found in {model_path}")

    def _score_onnx(
        self, *, query: str, records: list[RawRecord],
    ) -> list[float]:
        import numpy as np

        self._ensure_onnx_ready()
        assert self._tokenizer is not None
        assert self._onnx_session is not None

        pairs = [(query, self._record_text(record)) for record in records]
        encoded = self._tokenizer(
            [q for q, _ in pairs],
            [d for _, d in pairs],
            padding=True,
            truncation=True,
            max_length=self._max_length,
            return_tensors="np",
        )

        inputs: dict[str, object] = {}
        for onnx_input in self._onnx_session.get_inputs():
            if onnx_input.name in encoded:
                inputs[onnx_input.name] = encoded[onnx_input.name].astype(np.int64)

        outputs = self._onnx_session.run(None, inputs)
        logits = outputs[0]
        if logits.ndim == 2 and logits.shape[1] == 1:
            return [float(value) for value in logits[:, 0].tolist()]
        if logits.ndim == 2 and logits.shape[1] >= 2:
            # Binary classification head: score is the "relevant" class logit.
            return [float(value) for value in logits[:, -1].tolist()]
        return [float(value) for value in np.asarray(logits).flatten().tolist()]

    # ----- Sidecar backend ------------------------------------------------

    def _score_sidecar(
        self, *, query: str, records: list[RawRecord],
    ) -> list[float]:
        import httpx

        endpoint = "http://medcpt:8000/rerank"
        payload = {
            "query": query,
            "documents": [self._record_text(record) for record in records],
        }
        with httpx.Client(timeout=10.0) as client:
            response = client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()
        return [float(score) for score in data.get("scores", [])]

    # ----- HF Inference Endpoints backend --------------------------------

    def _score_hf_endpoints(
        self, *, query: str, records: list[RawRecord],
    ) -> list[float]:
        import httpx

        endpoint = self.settings.HF_MEDCPT_ENDPOINT_URL
        token = self.settings.HF_API_TOKEN
        if not endpoint or not token:
            raise RuntimeError(
                "hf_endpoints backend requires HF_MEDCPT_ENDPOINT_URL and HF_API_TOKEN",
            )
        payload = {
            "inputs": {
                "source_sentence": query,
                "sentences": [self._record_text(record) for record in records],
            },
        }
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=20.0) as client:
            response = client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        if isinstance(data, list):
            return [float(v) for v in data]
        return [float(v) for v in data.get("scores", [])]

    # ----- HF pay-per-call API backend -----------------------------------

    def _score_hf_api(
        self, *, query: str, records: list[RawRecord],
    ) -> list[float]:
        from huggingface_hub import InferenceClient  # local import

        token = self.settings.HF_API_TOKEN
        if not token:
            raise RuntimeError("hf_api backend requires HF_API_TOKEN")
        client = InferenceClient(token=token, provider="hf-inference")
        outputs = client.sentence_similarity(
            sentences=[self._record_text(record) for record in records],
            source_sentence=query,
            model="ncbi/MedCPT-Cross-Encoder",
        )
        return [float(value) for value in outputs]

    # ----- Helpers --------------------------------------------------------

    @staticmethod
    def _record_text(record: RawRecord) -> str:
        title = (record.title or "").strip()
        abstract = (record.abstract or "").strip()
        if title and abstract:
            return f"{title}. {abstract}"
        return title or abstract or ""
