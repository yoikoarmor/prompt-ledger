import json
import math
import random
from unittest.mock import patch

import pytest
from conftest import payload

from prompt_ledger.comparison import nearest
from prompt_ledger.config import LedgerError


def seed(ledger, count=300, dimension=16):
    rng = random.Random(42)
    with ledger.connect() as conn:
        for i in range(count):

            def vector():
                v = [rng.uniform(-1, 1) for _ in range(dimension)]
                norm = math.sqrt(math.fsum(x * x for x in v))
                return [x / norm for x in v]

            p, n = vector(), vector()
            conn.execute(
                "INSERT INTO records(input_hash,source,status,reason,positive,negative,positive_vector,negative_vector,created_at) VALUES (?,?,'accepted','synthetic',?,?,?,?, 'synthetic')",
                (
                    str(i),
                    "synthetic.json",
                    "synthetic",
                    "negative" if i % 3 else "",
                    json.dumps(p),
                    json.dumps(n) if i % 3 else None,
                ),
            )


def test_blocks_match_reference_including_scores(ledger):
    seed(ledger)
    with ledger.connect() as conn:
        row = conn.execute("SELECT positive_vector,negative_vector FROM records WHERE id=2").fetchone()
        p, n = json.loads(row[0]), json.loads(row[1])
        for negative in (None, n):
            assert nearest(conn, p, negative, []) == nearest(conn, p, negative, [], accelerated=False)
        assert nearest(conn, p, n, ["synthetic"]) == (None, None, None)


@pytest.mark.parametrize("backend", [True, False])
@pytest.mark.parametrize("value", ["null", "[]", "[0,0,0]", "[NaN,0,0]", '["1","0","0"]', "broken"])
def test_corrupt_stored_vector_fails_closed(ledger, backend, value):
    ledger.ingest([("a.json", payload("base"))])
    with ledger.connect() as conn:
        conn.execute("UPDATE records SET positive_vector=?", (value,))
        with pytest.raises(LedgerError) as exc:
            nearest(conn, [1.0, 0.0, 0.0], None, [], accelerated=backend)
        assert exc.value.code == "stored_embedding"


def test_cancel_matrix_comparison_rolls_back_new_record(ledger):
    ledger.ingest([("a.json", payload("base"))])
    before = ledger.export()
    with (
        patch("prompt_ledger.core.find_nearest", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        ledger.ingest([("b.json", payload("opposite"))])
    assert ledger.export() == before


def test_ties_and_threshold_across_blocks(ledger):
    with ledger.connect() as conn:
        for i in range(270):
            conn.execute(
                "INSERT INTO records(input_hash,source,status,reason,positive,negative,positive_vector,created_at) VALUES (?,?,'accepted','synthetic','synthetic','','[1,0,0]','synthetic')",
                (str(i), "synthetic.json"),
            )
        p = [0.95, math.sqrt(1 - 0.95**2), 0.0]
        assert nearest(conn, p, None, []) == (1, 0.95, 1.0)
        assert nearest(conn, p, None, []) == nearest(conn, p, None, [], accelerated=False)


def test_block_buffers_bounded_and_no_numpy_fallback(ledger):
    import tracemalloc

    seed(ledger, count=1500, dimension=128)
    with ledger.connect() as conn:
        p = [1.0] + [0.0] * 127
        tracemalloc.start()
        result = nearest(conn, p, None, [])
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 20 * 1024 * 1024
        with patch("prompt_ledger.comparison.numpy_backend", return_value=None):
            assert nearest(conn, p, None, []) == result


def test_empty_candidates(ledger):
    with ledger.connect() as conn:
        assert nearest(conn, [1.0, 0.0, 0.0], None, []) == (None, None, None)


@pytest.mark.parametrize("with_negative", [False, True])
def test_gpu_score_path_and_runtime_fallback_without_gpu(ledger, with_negative):
    np = pytest.importorskip("numpy")
    seed(ledger, dimension=16)
    diagnostics = {}

    def gpu(p, n, pv, nv, nonempty):
        return np.clip(np.einsum("ij,j->i", p, pv), -1.0, 1.0), (
            np.where(nonempty, 0.0, 1.0)
            if nv is None
            else np.where(nonempty, np.clip(np.einsum("ij,j->i", n, nv), -1.0, 1.0), 0.0)
        )

    with ledger.connect() as conn:
        p = [1.0] + [0.0] * 15
        negative = p if with_negative else None
        expected = nearest(conn, p, negative, [], accelerated=False)
        with patch("prompt_ledger.comparison.gpu_scores", side_effect=gpu) as called:
            assert nearest(conn, p, negative, [], device="cuda", diagnostics=diagnostics) == expected
        assert called.call_count == 3 and diagnostics["backend"] == "torch-cuda-float64"
        with patch(
            "prompt_ledger.comparison.gpu_scores", side_effect=RuntimeError("synthetic OOM")
        ) as called:
            assert nearest(conn, p, negative, [], device="cuda", diagnostics=diagnostics) == expected
        assert called.call_count == 1
        assert diagnostics["backend"] == "numpy-cpu"
        assert diagnostics["fallback_reason"] == "cuda_comparison_failed"


def test_gpu_rounding_boundary_and_cancellation(ledger):
    np = pytest.importorskip("numpy")
    ledger.ingest([("a.json", payload("base"))])
    p = [0.95, math.sqrt(1 - 0.95**2), 0.0]
    with ledger.connect() as conn:
        with patch(
            "prompt_ledger.comparison.gpu_scores",
            return_value=(np.asarray([0.95 - 1e-14]), np.asarray([1.0])),
        ):
            assert nearest(conn, p, None, [], device="cuda") == (1, 0.95, 1.0)
        with (
            patch("prompt_ledger.comparison.gpu_scores", side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            nearest(conn, p, None, [], device="cuda")
