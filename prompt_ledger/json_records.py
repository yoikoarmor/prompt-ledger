"""Bounded reader for the metadata-extraction envelope, never a JSON-file materializer."""

import codecs
import json

from .config import MAX_METADATA, LedgerError
from .parsers import strict_json


class TextGuard:
    """Reject renamed binary/image inputs before writing their bytes to staging."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.started, self.leading = False, 0

    def feed(self, chunk, *, final=False):
        try:
            text = self.decoder.decode(chunk, final=final)
        except UnicodeError:
            raise LedgerError("stage_json", "Expected UTF-8 JSON text, not image or binary bytes.") from None
        if "\x00" in text:
            raise LedgerError("stage_json", "Invalid raw control byte in JSON.")
        if not self.started:
            trimmed = text.lstrip(" \t\r\n")
            self.leading += len(text) - len(trimmed)
            if self.leading > MAX_METADATA or (trimmed and not trimmed.startswith("{")):
                raise LedgerError("stage_json", "Expected a JSON object envelope.")
            self.started = bool(trimmed)
        if final and not self.started:
            raise LedgerError("stage_json", "Empty extraction JSON.")


class Reader:
    def __init__(self, stream):
        self.stream, self.buffer, self.eof = stream, "", False
        self.decoder = json.JSONDecoder()

    def fill(self):
        chunk = self.stream.read(32768)
        self.buffer += chunk
        self.eof = not chunk

    def peek(self):
        self.buffer = self.buffer.lstrip(" \t\r\n")
        while not self.buffer and not self.eof:
            self.fill()
            self.buffer = self.buffer.lstrip(" \t\r\n")
        return self.buffer[:1]

    def take(self, token):
        if self.peek() != token:
            raise LedgerError("stage_json", "Invalid extraction JSON structure.")
        self.buffer = self.buffer[1:]

    def value(self):
        self.peek()
        while True:
            try:
                _, end = self.decoder.raw_decode(self.buffer)
                if end == len(self.buffer) and not self.eof:
                    self.fill()
                    continue
                raw = self.buffer[:end]
                if len(raw.encode("utf-8")) > MAX_METADATA:
                    raise LedgerError("metadata_limit", "One JSON value exceeds the metadata budget.")
                value = strict_json(raw)
                self.buffer = self.buffer[end:]
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise LedgerError("stage_json", "Incomplete extraction JSON.") from None
                if len(self.buffer) > MAX_METADATA:
                    raise LedgerError(
                        "metadata_limit", "One JSON value exceeds the metadata budget."
                    ) from None
                self.fill()


def records(stream):
    reader = Reader(stream)
    reader.take("{")
    headers, seen, found = {}, set(), False
    while reader.peek() != "}":
        key = reader.value()
        if not isinstance(key, str) or key in seen:
            raise LedgerError("stage_json", "Duplicate or invalid extraction header.")
        seen.add(key)
        reader.take(":")
        if key == "records":
            if (
                type(headers.get("schema_version")) is not int
                or headers.get("schema_version") != 1
                or headers.get("kind") != "metadata-extraction"
            ):
                raise LedgerError(
                    "stage_json", "Expected schema_version=1 and kind=metadata-extraction before records."
                )
            found = True
            reader.take("[")
            while reader.peek() != "]":
                value = reader.value()
                if not isinstance(value, dict):
                    raise LedgerError("stage_json", "Each extraction record must be an object.")
                yield value
                if reader.peek() == "]":
                    break
                reader.take(",")
                if reader.peek() == "]":
                    raise LedgerError("stage_json", "Trailing comma in records.")
            reader.take("]")
        elif key in ("schema_version", "kind", "rule_version"):
            headers[key] = reader.value()
        else:
            raise LedgerError("stage_json", "Unknown extraction header.")
        if reader.peek() == "}":
            break
        reader.take(",")
        if reader.peek() == "}":
            raise LedgerError("stage_json", "Trailing comma in extraction JSON.")
    reader.take("}")
    if not found or reader.peek():
        raise LedgerError("stage_json", "Missing records or trailing extraction data.")
