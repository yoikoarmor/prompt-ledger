"""Bounded metadata parsers. Never decode, render, or transmit image pixels."""

import hashlib
import json
import re
import struct
import zlib
from dataclasses import dataclass

from .config import MAX_METADATA, MAX_TEXT, LedgerError, relative_path


@dataclass(frozen=True)
class Pair:
    positive: str
    negative: str

    @property
    def digest(self):
        return hashlib.sha256(
            json.dumps([self.positive, self.negative], ensure_ascii=False).encode()
        ).hexdigest()


def pair(positive, negative):
    if not isinstance(positive, str) or not isinstance(negative, str) or not positive.strip():
        raise LedgerError("review", "Explicit positive and negative string fields are required.")
    if max(len(positive.encode()), len(negative.encode())) > MAX_TEXT:
        raise LedgerError("text_limit", "Prompt exceeds the text byte limit; no truncation was performed.")
    # Preserve the original prompt exactly. Empty negative means exactly the empty string.
    return Pair(positive, negative)


def strict_json(text):
    def unique(items):
        result = {}
        for k, v in items:
            if k in result:
                raise LedgerError("invalid_json", "Duplicate JSON keys are ambiguous.")
            result[k] = v
        return result

    try:
        return json.loads(
            text, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
        )
    except (ValueError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, LedgerError):
            raise
        raise LedgerError("invalid_json", "Invalid JSON metadata.") from None


def infotext(text):
    if not isinstance(text, str) or len(text.encode()) > MAX_METADATA:
        raise LedgerError("metadata_limit", "Metadata exceeds the byte limit.")
    # A settings line is required: arbitrary captions must not become training data.
    settings = list(re.finditer(r"(?m)^Steps: \d+,", text))
    if len(settings) != 1:
        raise LedgerError("review", "A unique A1111/Forge settings line was not found.")
    body = text[: settings[0].start()].rstrip("\r\n")
    marker = "\nNegative prompt: "
    body = body.replace("\r\n", "\n")
    labels = list(re.finditer(r"(?m)^[ \t]*Negative prompt:", body))
    if len(labels) > 1:
        raise LedgerError("review", "Multiple negative-prompt sections require confirmation.")
    if marker in body:
        positive, negative = body.split(marker)
    elif body.endswith("\nNegative prompt:"):
        positive, negative = body[: -len("\nNegative prompt:")], ""
    elif labels:
        raise LedgerError("review", "Nonstandard negative-prompt label requires confirmation.")
    else:
        # A1111 intentionally omits this field when negative is empty.
        positive, negative = body, ""
    return pair(positive, negative)


def comfy(graph):
    if not isinstance(graph, dict) or len(graph) > 5000:
        raise LedgerError("review", "Unsupported ComfyUI graph.")
    for node in graph.values():
        if isinstance(node, dict):
            kind = node.get("class_type", "")
            if (
                isinstance(kind, str)
                and "sampler" in kind.lower()
                and kind not in ("KSampler", "KSamplerAdvanced")
            ):
                raise LedgerError("review", "Custom sampler graphs require review.")
    samplers = [
        n
        for n in graph.values()
        if isinstance(n, dict) and n.get("class_type") in ("KSampler", "KSamplerAdvanced")
    ]
    if len(samplers) != 1:
        raise LedgerError(
            "review", "Exactly one supported sampler is required; multi-stage graphs need review."
        )
    text_node_ids = {
        str(key)
        for key, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode"
    }
    for node in graph.values():
        if not isinstance(node, dict) or node is samplers[0]:
            continue
        node_inputs = node.get("inputs", {})
        if isinstance(node_inputs, dict) and (
            any(key in node_inputs for key in ("positive", "negative", "conditioning"))
            or any(
                isinstance(link, list) and len(link) == 2 and str(link[0]) in text_node_ids
                for link in node_inputs.values()
            )
        ):
            raise LedgerError("review", "Additional conditioning consumers require review.")
    inputs = samplers[0].get("inputs", {})
    texts = []
    for role in ("positive", "negative"):
        link = inputs.get(role) if isinstance(inputs, dict) else None
        if not isinstance(link, list) or len(link) != 2 or link[1] != 0:
            raise LedgerError("review", "Conditioning must link directly to a CLIPTextEncode output.")
        node = graph.get(str(link[0]))
        if not isinstance(node, dict) or node.get("class_type") != "CLIPTextEncode":
            raise LedgerError("review", "Conditioning transforms or custom nodes require review.")
        text_inputs = node.get("inputs", {})
        text = text_inputs.get("text") if isinstance(text_inputs, dict) else None
        if not isinstance(text, str):
            raise LedgerError("review", "Dynamic text cannot be safely inferred.")
        texts.append(text)
    return pair(*texts)


def parse_json(raw):
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise LedgerError("review", "Expected a JSON object, not a list.")
    candidates = []
    if "positive" in value or "negative" in value:
        candidates.append(pair(value.get("positive"), value.get("negative")))
    if "positive_prompt" in value or "negative_prompt" in value:
        candidates.append(pair(value.get("positive_prompt"), value.get("negative_prompt")))
    if "prompt" in value:
        # A present but malformed format must not silently fall back to another one.
        candidates.append(comfy(value["prompt"]))
    if any(isinstance(node, dict) and "class_type" in node for node in value.values()):
        candidates.append(comfy(value))
    if not candidates:
        return comfy(value)
    if any(candidate != candidates[0] for candidate in candidates):
        raise LedgerError("review", "Conflicting JSON prompt representations require confirmation.")
    return candidates[0]


def inflate(raw):
    inflater = zlib.decompressobj()
    value = inflater.decompress(raw, MAX_METADATA + 1)
    if len(value) > MAX_METADATA or not inflater.eof or inflater.unconsumed_tail:
        raise LedgerError("metadata_limit", "Compressed metadata is too large or invalid.")
    return value


def png(raw):
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise LedgerError("format", "Invalid PNG signature.")
    position, total, fields = 8, 0, {}
    ended = False
    while position + 12 <= len(raw):
        length = struct.unpack_from(">I", raw, position)[0]
        kind = raw[position + 4 : position + 8]
        end = position + 8 + length
        if end + 4 > len(raw):
            raise LedgerError("format", "Truncated PNG chunk.")
        if kind in (b"tEXt", b"iTXt", b"zTXt"):
            if length > MAX_METADATA:
                raise LedgerError("metadata_limit", "PNG metadata is too large.")
            chunk = raw[position + 8 : end]
            if zlib.crc32(kind + chunk) != struct.unpack_from(">I", raw, end)[0]:
                raise LedgerError("format", "Invalid PNG metadata CRC.")
            key, content = chunk.split(b"\0", 1)
            if kind == b"zTXt":
                if not content or content[0] != 0:
                    raise LedgerError("format", "Unsupported PNG compression.")
                content = inflate(content[1:])
            if kind == b"iTXt":
                flag, method = content[:2]
                if flag not in (0, 1) or method != 0:
                    raise LedgerError("format", "Unsupported PNG text encoding.")
                content = content[2:].split(b"\0", 2)[2]
                if flag:
                    content = inflate(content)
            total += len(content)
            if total > MAX_METADATA:
                raise LedgerError("metadata_limit", "Total PNG text is too large.")
            if key in (b"parameters", b"prompt"):
                if key in fields:
                    raise LedgerError("review", "Duplicate PNG prompt fields require review.")
                fields[key] = content.decode("utf-8" if kind == b"iTXt" else "latin-1")
        position = end + 4
        if kind == b"IEND":
            ended = True
            break
    if not ended:
        raise LedgerError("format", "PNG is missing IEND.")
    candidates = []
    if b"parameters" in fields:
        candidates.append(infotext(fields[b"parameters"]))
    if b"prompt" in fields:
        candidates.append(comfy(strict_json(fields[b"prompt"])))
    if not candidates:
        raise LedgerError("review", "No supported prompt metadata found.")
    if any(p != candidates[0] for p in candidates):
        raise LedgerError("review", "Conflicting metadata sources require confirmation.")
    return candidates[0]


def exif_comment(tiff):
    if tiff[:2] not in (b"II", b"MM"):
        raise LedgerError("format", "Invalid TIFF byte order.")
    endian = "<" if tiff[:2] == b"II" else ">"

    def read(fmt, offset):
        return struct.unpack_from(endian + fmt, tiff, offset)[0]

    if read("H", 2) != 42:
        raise LedgerError("format", "Invalid TIFF header.")
    seen, comments = set(), []

    def directory(offset, depth=0):
        if depth > 2 or offset in seen:
            raise LedgerError("review", "Ambiguous EXIF directories.")
        seen.add(offset)
        count = read("H", offset)
        if count > 2048:
            raise LedgerError("metadata_limit", "Too many EXIF tags.")
        for i in range(count):
            entry = offset + 2 + i * 12
            tag, typ, size = read("H", entry), read("H", entry + 2), read("I", entry + 4)
            if tag == 0x8769 and typ == 4 and size == 1:
                directory(read("I", entry + 8), depth + 1)
            if tag == 0x9286:
                if typ != 7 or size < 8 or size > MAX_METADATA:
                    raise LedgerError("review", "Unsupported EXIF UserComment type.")
                start = read("I", entry + 8)
                data = tiff[start : start + size]
                if len(data) != size:
                    raise LedgerError("format", "Truncated EXIF UserComment.")
                prefix, content = data[:8], data[8:]
                if prefix == b"ASCII\0\0\0":
                    comments.append(content.rstrip(b"\0").decode("ascii"))
                elif prefix == b"UNICODE\0":
                    codec = (
                        "utf-16"
                        if content[:2] in (b"\xff\xfe", b"\xfe\xff")
                        else ("utf-16-le" if endian == "<" else "utf-16-be")
                    )
                    comments.append(content.decode(codec).rstrip("\0"))
                else:
                    raise LedgerError("review", "Unsupported EXIF character encoding.")

    directory(read("I", 4))
    return comments


def jpeg(raw):
    if not raw.startswith(b"\xff\xd8"):
        raise LedgerError("format", "Invalid JPEG signature.")
    pos, comments, metadata_bytes = 2, [], 0
    while pos + 1 < len(raw):
        if raw[pos] != 0xFF:
            raise LedgerError("format", "Invalid JPEG marker.")
        while pos < len(raw) and raw[pos] == 0xFF:
            pos += 1
        kind = raw[pos]
        pos += 1
        if kind in (0xDA, 0xD9):  # Stop before compressed image data.
            break
        if kind in range(0xD0, 0xD9) or kind == 1:
            continue
        length = struct.unpack_from(">H", raw, pos)[0]
        if length < 2 or pos + length > len(raw):
            raise LedgerError("format", "Truncated JPEG segment.")
        segment = raw[pos + 2 : pos + length]
        if 0xE0 <= kind <= 0xEF or kind == 0xFE:
            metadata_bytes += len(segment)
            if metadata_bytes > MAX_METADATA:
                raise LedgerError("metadata_limit", "Total JPEG metadata is too large.")
        if kind == 0xE1 and segment.startswith(b"Exif\0\0"):
            comments.extend(exif_comment(segment[6:]))
        pos += length
    if len(comments) != 1:
        raise LedgerError("review", "A unique EXIF UserComment was not found.")
    return infotext(comments[0])


PARSERS = {".png": png, ".jpg": jpeg, ".jpeg": jpeg, ".json": parse_json}


def extract(source, raw):
    from .streaming import MetadataInput

    path = relative_path(source)
    if isinstance(raw, MetadataInput):
        if raw.error:
            raise LedgerError(raw.error, "Input requires review; no content was truncated or accepted.")
        raw = raw.raw
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise LedgerError("format", "Supported formats: PNG, JPEG and JSON. Archives are not accepted.")
    if path.suffix.lower() == ".json" and len(raw) > MAX_METADATA:
        raise LedgerError("metadata_limit", "JSON exceeds the metadata limit.")
    try:
        return parser(raw)
    except LedgerError:
        raise
    except (
        ValueError,
        IndexError,
        KeyError,
        TypeError,
        struct.error,
        zlib.error,
        RecursionError,
        UnicodeError,
    ):
        raise LedgerError("format", "Malformed or unsupported metadata; please review manually.") from None
