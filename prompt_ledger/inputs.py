"""Walk only explicitly selected folders; never follow links or retain image bytes."""

import hashlib
import os

from .config import LedgerError, contained
from .parsers import PARSERS
from .streaming import MetadataInput, read_metadata


def failure(name, code):
    return name, MetadataInput(b"", hashlib.sha256(name.encode()).digest(), code)


def read_inputs(cfg, paths, recursive):
    root = cfg.path("input_dir")

    def walk(name, path):
        try:
            with os.scandir(path) as entries:
                names = sorted(entry.name for entry in entries)
        except OSError:
            yield failure(name, "input_io")
            return
        for entry in names:
            relative = f"{name}/{entry}"
            try:
                child = contained(root, relative)
                if child.is_dir():
                    if recursive:
                        yield from walk(relative, child)
                elif child.suffix.lower() in PARSERS:
                    yield relative, read_metadata(relative, child)
            except LedgerError:
                yield failure(relative, "unsafe_path")
            except OSError:
                yield failure(relative, "input_io")

    for name in paths:
        target = contained(root, name)
        if target.is_dir():
            yield from walk(name, target)
        else:
            yield name, read_metadata(name, target)
