"""Export NCBI MedCPT cross-encoder to quantized ONNX for in-process reranking.

Run once on a build machine; commit the resulting ``./models/medcpt-cross-onnx-qint8``
directory to the Coolify volume OR bundle it into the Docker image via the
``INSTALL_MEDCPT`` build arg.

Usage::

    uv pip install '.[medcpt-onnx]' 'sentence-transformers>=3.0' 'optimum[onnxruntime]>=1.21'
    uv run python scripts/export_medcpt_onnx.py

Why this script exists:

* The ``ncbi/MedCPT-Cross-Encoder`` checkpoint on Hugging Face ships as a
  standard PyTorch BERT. At runtime we don't want to pull in torch just to
  score 100 (query, title+abstract) pairs — it blows up the image by ~1.5 GB
  and adds ~1 GB of RSS per worker.
* ONNX Runtime with INT8 dynamic quantization is ~2x faster on CPU, uses
  ~450 MB RAM for the same model, and runs without torch at serving time.

The script is deliberately small and self-contained so operators can audit
what's being exported without reading a recipe framework.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _resolve_output_path() -> Path:
    """Default to ``repo-root/models/medcpt-cross-onnx-qint8``."""
    script_path = Path(__file__).resolve()
    repo_root = script_path.parent.parent
    return repo_root / "models" / "medcpt-cross-onnx-qint8"


def main() -> None:
    try:
        from optimum.onnxruntime import ORTModelForSequenceClassification
        from optimum.onnxruntime import ORTQuantizer
        from optimum.onnxruntime.configuration import AutoQuantizationConfig
        from transformers import AutoTokenizer
    except ImportError as exc:
        sys.stderr.write(
            "ERROR: missing optional MedCPT export dependencies.\n"
            f"       {exc}\n"
            "       Install them with:\n"
            "       uv pip install 'optimum[onnxruntime]>=1.21' "
            "'transformers>=4.40' 'onnxruntime>=1.18'\n",
        )
        sys.exit(1)

    model_id = "ncbi/MedCPT-Cross-Encoder"
    output_dir = _resolve_output_path()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir.with_name(output_dir.name + "-fp32")

    print(f"[medcpt-export] Loading {model_id!r} and exporting to FP32 ONNX...", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    tokenizer.save_pretrained(work_dir)

    model = ORTModelForSequenceClassification.from_pretrained(model_id, export=True)
    model.save_pretrained(work_dir)

    print("[medcpt-export] Quantizing to INT8 (dynamic, avx512_vnni ops)...", flush=True)
    quantizer = ORTQuantizer.from_pretrained(work_dir)
    dqconfig = AutoQuantizationConfig.avx512_vnni(is_static=False, per_channel=False)
    quantizer.quantize(
        save_dir=output_dir,
        quantization_config=dqconfig,
    )

    tokenizer.save_pretrained(output_dir)
    print(f"[medcpt-export] Done. Artifacts written to: {output_dir}", flush=True)


if __name__ == "__main__":
    main()
