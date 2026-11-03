"""Incremental metadata sieve: never decode pixels or make image disk copies."""

import hashlib
import struct
from dataclasses import dataclass

from .config import MAX_METADATA, LedgerError, relative_path


@dataclass
class MetadataInput:
    raw: bytes
    fingerprint: bytes
    error: str | None = None


class MetadataStream:
    def __init__(self, source):
        self.suffix = relative_path(source).suffix.lower()
        self.hash = hashlib.sha256()
        self.output, self.part = bytearray(), bytearray()
        self.error, self.done = None, False
        self.reader = self.sieve()
        self.need, self.keep = next(self.reader)

    def emit(self, data):
        if len(self.output) + len(data) > MAX_METADATA + 65536:
            raise LedgerError("metadata_limit", "Metadata exceeds the processing budget.")
        self.output.extend(data)

    def sieve(self):
        if self.suffix == ".png":
            signature = yield 8, True
            if signature != b"\x89PNG\r\n\x1a\n":
                raise LedgerError("format", "Invalid PNG signature.")
            self.emit(signature)
            while True:
                header = yield 8, True
                length, kind = struct.unpack(">I4s", header)
                keep = kind in (b"tEXt", b"zTXt", b"iTXt", b"IEND")
                if keep and length > MAX_METADATA:
                    raise LedgerError("metadata_limit", "Metadata exceeds the processing budget.")
                data = yield length + 4, keep
                if keep:
                    self.emit(header + data)
                if kind == b"IEND":
                    return
        elif self.suffix in (".jpg", ".jpeg"):
            signature = yield 2, True
            if signature != b"\xff\xd8":
                raise LedgerError("format", "Invalid JPEG signature.")
            self.emit(signature)
            while True:
                marker = yield 1, True
                if marker != b"\xff":
                    raise LedgerError("format", "Invalid JPEG marker.")
                kind = yield 1, True
                while kind == b"\xff":
                    kind = yield 1, True
                if kind in (b"\xda", b"\xd9"):
                    self.emit(b"\xff" + kind)
                    return
                if kind[0] in range(0xD0, 0xD9) or kind == b"\x01":
                    continue
                size = yield 2, True
                length = int.from_bytes(size, "big") - 2
                if length < 0:
                    raise LedgerError("format", "Invalid JPEG segment size.")
                keep = 0xE0 <= kind[0] <= 0xEF or kind == b"\xfe"
                data = yield length, keep
                if keep:
                    self.emit(b"\xff" + kind + size + data)
        else:
            yield 1, True
            raise LedgerError("format", "Supported formats: PNG, JPEG and JSON.")

    def feed(self, chunk):
        self.hash.update(chunk)
        if self.done or self.error:
            return
        if self.suffix == ".json":
            if len(self.output) + len(chunk) > MAX_METADATA:
                self.error = "metadata_limit"
                self.output.clear()
            else:
                self.output.extend(chunk)
            return
        view = memoryview(chunk)
        while (view or self.need == 0) and not self.done and not self.error:
            size = min(len(view), self.need)
            if self.keep:
                self.part.extend(view[:size])
            view = view[size:]
            self.need -= size
            if self.need == 0:
                data = bytes(self.part)
                self.part.clear()
                try:
                    self.need, self.keep = self.reader.send(data)
                except StopIteration:
                    self.done = True
                except LedgerError as exc:
                    self.error = exc.code
                    self.output.clear()

    def finish(self):
        error = self.error or ("format" if not self.done and self.suffix != ".json" else None)
        return MetadataInput(bytes(self.output), self.hash.digest(), error)


def read_metadata(source, path):
    stream = MetadataStream(source)
    try:
        if not path.is_file():
            return MetadataInput(b"", hashlib.sha256(source.encode()).digest(), "input_io")
        with path.open("rb") as file:
            while chunk := file.read(64 * 1024):
                stream.feed(chunk)
    except OSError:
        return MetadataInput(b"", hashlib.sha256(source.encode()).digest(), "input_io")
    return stream.finish()
