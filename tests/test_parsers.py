import json
import struct
import zlib

import pytest

from prompt_ledger.config import MAX_METADATA, LedgerError
from prompt_ledger.parsers import extract, infotext


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_metadata(text, kind=b"tEXt"):
    return b"\x89PNG\r\n\x1a\n" + chunk(kind, text) + chunk(b"IEND", b"")


def test_png_infotext():
    raw = png_metadata(b"parameters\0a yellow flower\nNegative prompt: blur\nSteps: 20, Sampler: Euler")
    result = extract("flower.png", raw)
    assert (result.positive, result.negative) == ("a yellow flower", "blur")


def test_compressed_utf8_png():
    text = "花\nNegative prompt: ぼけ\nSteps: 20, Sampler: Euler".encode()
    raw = png_metadata(b"parameters\0\x01\x00\0\0" + zlib.compress(text), b"iTXt")
    assert extract("flower.png", raw).positive == "花"


def test_png_bomb_and_no_metadata():
    raw = png_metadata(b"parameters\0\0" + zlib.compress(b"x" * (MAX_METADATA + 1)), b"zTXt")
    with pytest.raises(LedgerError, match="large"):
        extract("bomb.png", raw)
    with pytest.raises(LedgerError):
        extract("none.png", png_metadata(b"Software\0Synthetic test"))


def graph():
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a flower"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "blur"}},
        "3": {"class_type": "KSampler", "inputs": {"positive": ["1", 0], "negative": ["2", 0]}},
    }


def test_comfy_direct_and_ambiguous():
    value = graph()
    assert extract("graph.json", json.dumps(value).encode()).negative == "blur"
    value["4"] = value["3"]
    with pytest.raises(LedgerError, match="one supported sampler"):
        extract("graph.json", json.dumps(value).encode())
    value = graph()
    value["1"]["class_type"] = "ConditioningCombine"
    with pytest.raises(LedgerError, match="require review"):
        extract("graph.json", json.dumps(value).encode())
    value = graph()
    value["4"] = {"class_type": "SamplerCustom", "inputs": {}}
    with pytest.raises(LedgerError, match="require review"):
        extract("graph.json", json.dumps(value).encode())


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b'{"positive":"x","positive":"y","negative":""}',
        b'{"positive":"x"}',
        b"[]",
        b'{"positive":NaN,"negative":""}',
    ],
)
def test_bad_json(raw):
    with pytest.raises(LedgerError):
        extract("bad.json", raw)


def test_jpeg_usercomment():
    content = b"ASCII\0\0\0a flower\nNegative prompt: blur\nSteps: 20, Sampler: Euler"
    # TIFF IFD0 -> ExifIFD -> UserComment. No pixel decoding needed.
    tiff = b"II" + struct.pack("<HI", 42, 8)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + b"\0" * 4
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x9286, 7, len(content), 44) + b"\0" * 4 + content
    segment = b"Exif\0\0" + tiff
    raw = b"\xff\xd8\xff\xe1" + struct.pack(">H", len(segment) + 2) + segment + b"\xff\xd9"
    assert extract("synthetic.jpg", raw).positive == "a flower"


def test_infotext_empty_negative():
    assert infotext("a flower\nSteps: 20, Sampler: Euler").negative == ""
    assert infotext("a flower\nNegative prompt:\nSteps: 20, Sampler: Euler").negative == ""


@pytest.mark.parametrize(
    "label",
    ["Negative prompt:blur", "Negative prompt:\nblur", " Negative prompt: blur", "Negative prompt:\tblur"],
)
def test_nonstandard_negative_labels_require_review(label):
    with pytest.raises(LedgerError) as error:
        infotext(f"a flower\n{label}\nSteps: 20, Sampler: Euler")
    assert error.value.code == "review"


def test_equal_json_aliases_and_graph_are_consistent():
    value = {
        "positive": "a flower",
        "negative": "blur",
        "positive_prompt": "a flower",
        "negative_prompt": "blur",
        "prompt": graph(),
    }
    result = extract("synthetic.json", json.dumps(value).encode())
    assert (result.positive, result.negative) == ("a flower", "blur")


@pytest.mark.parametrize(
    "extra",
    [
        {"positive_prompt": "a mountain", "negative_prompt": "noise"},
        {"positive_prompt": "a flower"},
        {"prompt": {}},
        {"prompt": "unsupported graph string"},
    ],
)
def test_conflicting_or_malformed_json_representations_require_review(extra):
    value = {"positive": "a flower", "negative": "blur", **extra}
    with pytest.raises(LedgerError) as error:
        extract("synthetic.json", json.dumps(value).encode())
    assert error.value.code == "review"


def test_pair_and_conflicting_graph_require_review():
    value = {"positive": "a mountain", "negative": "noise", "prompt": graph()}
    with pytest.raises(LedgerError, match="Conflicting"):
        extract("synthetic.json", json.dumps(value).encode())


@pytest.mark.parametrize(
    "extra_node",
    [
        {"class_type": "FaceDetailer", "inputs": {"positive": ["4", 0], "negative": ["2", 0]}},
        {"class_type": "BasicGuider", "inputs": {"conditioning": ["4", 0]}},
        {"class_type": "CustomDetailer", "inputs": {"cond": ["4", 0]}},
    ],
)
def test_additional_conditioning_paths_require_review(extra_node):
    value = graph()
    value["4"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "another synthetic prompt"}}
    value["5"] = extra_node
    with pytest.raises(LedgerError, match="Additional conditioning"):
        extract("graph.json", json.dumps(value).encode())


def test_shared_positive_negative_encoder_is_unambiguous():
    value = graph()
    value["3"]["inputs"]["negative"] = ["1", 0]
    result = extract("graph.json", json.dumps(value).encode())
    assert result.positive == result.negative == "a flower"
