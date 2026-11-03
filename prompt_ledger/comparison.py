"""Bounded, exact nearest-neighbour comparison; no ANN or scalar projection."""

import json
import math
from functools import lru_cache

from .config import LedgerError
from .embedding import cosine
from .exclusions import matches

BLOCK_SIZE = 128
ROUNDING_MARGIN = 1e-10


def numpy_backend():
    try:
        import numpy

        return numpy
    except ImportError:
        return None


def gpu_scores(p, n, positive, negative, nonempty):
    import torch

    # float64 avoids TF32/float32 threshold drift. Only this bounded block is on GPU.
    with torch.inference_mode():
        pp = torch.as_tensor(p, dtype=torch.float64, device="cuda")
        ps = (pp @ torch.as_tensor(positive, dtype=torch.float64, device="cuda")).clamp(-1.0, 1.0)
        ns = (
            torch.as_tensor(~nonempty, dtype=torch.float64, device="cuda")
            if negative is None
            else (
                torch.as_tensor(n, dtype=torch.float64, device="cuda")
                @ torch.as_tensor(negative, dtype=torch.float64, device="cuda")
            ).clamp(-1.0, 1.0)
            * torch.as_tensor(nonempty, dtype=torch.float64, device="cuda")
        )
        return ps.cpu().numpy(), ns.cpu().numpy()


def nearest(conn, positive, negative, terms, *, accelerated=True, device="cpu", diagnostics=None):
    np = numpy_backend() if accelerated else None
    if diagnostics is not None:
        diagnostics.update(
            {
                "backend": "numpy-cpu" if np is not None else "python-cpu",
                "fallback_reason": None,
                "block_size": BLOCK_SIZE,
            }
        )
    gpu_enabled = device == "cuda"

    # Per-query cache only: raw serialized vectors are immutable keys, never prompt text.
    @lru_cache(maxsize=64)
    def decode(raw):
        return json.loads(raw)

    dimension = len(positive)
    best, nearest_id, ps, ns = -2.0, None, None, None
    rows = conn.execute(
        "SELECT id,positive,negative,positive_vector,negative_vector FROM records WHERE status='accepted' ORDER BY id"
    )
    while True:
        block = rows.fetchmany(BLOCK_SIZE)
        if not block:
            break
        block = [r for r in block if not matches(r["positive"], r["negative"], terms)]
        if not block:
            continue
        try:
            positives = [decode(r["positive_vector"]) for r in block]
            negatives = [decode(r["negative_vector"]) if r["negative"] else [0.0] * dimension for r in block]
            if np is not None:
                p = np.asarray(positives)
                n = np.asarray(negatives)
                if p.dtype.kind not in "fiu" or n.dtype.kind not in "fiu":
                    raise ValueError("numeric values required")
                p = p.astype(np.float64, copy=False)
                n = n.astype(np.float64, copy=False)
                nonempty = np.asarray([bool(r["negative"]) for r in block])
                if p.shape != (len(block), dimension) or n.shape != p.shape:
                    raise ValueError("shape")
                if not np.isfinite(p).all() or not np.isfinite(n).all():
                    raise ValueError("nonfinite")
                if np.any(np.abs(np.sum(p * p, axis=1) - 1) > 1e-6) or np.any(
                    np.abs(np.sum(n[nonempty] * n[nonempty], axis=1) - 1) > 1e-6
                ):
                    raise ValueError("norm")
                computed_on_gpu = False
                if gpu_enabled:
                    try:
                        positive_scores, negative_scores = gpu_scores(p, n, positive, negative, nonempty)
                        if not np.isfinite(positive_scores).all() or not np.isfinite(negative_scores).all():
                            raise RuntimeError("Invalid CUDA comparison scores")
                        computed_on_gpu = True
                        if diagnostics is not None:
                            diagnostics["backend"] = "torch-cuda-float64"
                    except (RuntimeError, ImportError):
                        # Same vectors and exact CPU rerank: comparison fallback changes no model space.
                        gpu_enabled = False
                        if diagnostics is not None:
                            diagnostics.update(
                                {"backend": "numpy-cpu", "fallback_reason": "cuda_comparison_failed"}
                            )
                if not computed_on_gpu:
                    positive_scores = np.clip(
                        np.einsum("ij,j->i", p, np.asarray(positive, dtype=np.float64)), -1.0, 1.0
                    )
                    negative_scores = (
                        np.where(nonempty, 0.0, 1.0)
                        if negative is None
                        else np.where(
                            nonempty,
                            np.clip(
                                np.einsum("ij,j->i", n, np.asarray(negative, dtype=np.float64)), -1.0, 1.0
                            ),
                            0.0,
                        )
                    )
                scores = np.minimum(positive_scores, negative_scores)
                cutoff = max(best, float(scores.max())) - ROUNDING_MARGIN
                candidates = np.flatnonzero(scores >= cutoff)
            else:
                for vector in positives + [v for r, v in zip(block, negatives) if r["negative"]]:
                    if (
                        len(vector) != dimension
                        or not all(type(x) in (int, float) and math.isfinite(x) for x in vector)
                        or abs(math.fsum(x * x for x in vector) - 1) > 1e-6
                    ):
                        raise ValueError("vector")
                candidates = range(len(block))
        except (ValueError, TypeError, OverflowError):
            raise LedgerError(
                "stored_embedding", "Stored vectors are invalid; comparison stopped without changing records."
            ) from None
        # Recompute potential winners with the original fsum cosine. This preserves
        # the exact threshold and ID tie rule despite BLAS rounding differences.
        for i in candidates:
            row = block[i]
            pos = cosine(positive, positives[i])
            neg = (
                1.0
                if negative is None and not row["negative"]
                else (0.0 if negative is None or not row["negative"] else cosine(negative, negatives[i]))
            )
            score = min(pos, neg)
            if score > best:
                best, nearest_id, ps, ns = score, row["id"], pos, neg
    return nearest_id, ps, ns
