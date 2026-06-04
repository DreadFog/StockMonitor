"""Parser registry that auto-discovers JSON configurations on disk.

The directory is resolved from (in order of precedence):

1. ``PARSERS_CONFIG_DIR`` environment variable
2. Explicit ``config_dir`` argument to :class:`ParserRegistry`
3. Default: ``<repo_root>/parsers``

Parsers are loaded lazily on first use and cached. Call :meth:`reload` to
re-scan the directory without restarting the process.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .base import BaseParser
from .json_parser import JsonConfigParser


_DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "parsers"


class ParserRegistry:
    def __init__(self, config_dir: str | os.PathLike | None = None):
        self._explicit_dir = Path(config_dir) if config_dir else None
        self._parsers: list[BaseParser] | None = None
        self._lock = threading.Lock()

    @property
    def config_dir(self) -> Path:
        env_value = os.environ.get("PARSERS_CONFIG_DIR")
        if env_value:
            return Path(env_value)
        if self._explicit_dir is not None:
            return self._explicit_dir
        return _DEFAULT_DIR

    def _load(self) -> list[BaseParser]:
        config_dir = self.config_dir
        if not config_dir.is_dir():
            raise FileNotFoundError(
                f"Parser config directory not found: {config_dir}. "
                "Set PARSERS_CONFIG_DIR or create the directory."
            )

        parsers: list[BaseParser] = []
        seen_types: set[str] = set()
        for path in sorted(config_dir.glob("*.json")):
            with path.open(encoding="utf-8") as fp:
                try:
                    config = json.load(fp)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in parser config {path}: {exc}") from exc

            parser = JsonConfigParser(config, source_path=path)
            if parser.parser_type in seen_types:
                raise ValueError(
                    f"Duplicate parser_type '{parser.parser_type}' detected in {path}"
                )
            seen_types.add(parser.parser_type)
            parsers.append(parser)

        if not parsers:
            raise RuntimeError(f"No parser config files (*.json) found in {config_dir}")
        return parsers

    @property
    def parsers(self) -> list[BaseParser]:
        if self._parsers is None:
            with self._lock:
                if self._parsers is None:
                    self._parsers = self._load()
        return self._parsers

    def reload(self) -> None:
        with self._lock:
            self._parsers = self._load()

    def get_parser(self, parser_type: str) -> BaseParser:
        for parser in self.parsers:
            if parser.parser_type == parser_type:
                return parser
        raise ValueError(f"No parser registered with type: {parser_type!r}")

    def detect_parser(self, text: str) -> BaseParser:
        for parser in self.parsers:
            if parser.can_parse(text):
                return parser
        raise ValueError("No parser matched this invoice format")

    def list_parsers(self) -> list[dict]:
        return [
            {
                "parser_type": parser.parser_type,
                "display_name": getattr(parser, "display_name", parser.parser_type),
                "description": getattr(parser, "description", ""),
            }
            for parser in self.parsers
        ]
