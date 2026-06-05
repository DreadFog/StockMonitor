"""Generic regex-driven parser configured from a JSON file.

Supports two extraction strategies:

- ``line``: ``pattern`` is matched against the whole text; every match contributes
  one ParsedEntry. All required named groups must live in this single pattern.
- ``anchored``: ``anchor_pattern`` locates each item's starting position (and
  must capture ``article_id``); for each anchor we run ``section_pattern`` over
  the slice from this anchor's end to the next anchor's start. Remaining fields
  come from the section match.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import BaseParser, ParsedEntry, ParsedInvoice
from .transforms import ENTRY_TRANSFORMS
from .utils import parse_date_from_text, parse_decimal


REQUIRED_FIELDS = {
    "article_id",
    "product_name",
    "unit_price",
    "colisage",
    "quantity",
    "total_price",
}

_FLAG_NAMES = {
    "MULTILINE": re.MULTILINE,
    "M": re.MULTILINE,
    "IGNORECASE": re.IGNORECASE,
    "I": re.IGNORECASE,
    "DOTALL": re.DOTALL,
    "S": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "X": re.VERBOSE,
    "UNICODE": re.UNICODE,
    "U": re.UNICODE,
}


def _compile_flags(flag_names) -> int:
    flags = 0
    for name in flag_names or []:
        upper = str(name).upper()
        if upper not in _FLAG_NAMES:
            raise ValueError(f"Unknown regex flag: {name}")
        flags |= _FLAG_NAMES[upper]
    return flags


def _coerce(field: str, raw: Any) -> Any:
    if raw is None:
        if field == "colisage":
            return 1
        raise ValueError(f"Missing value for required field '{field}'")
    if isinstance(raw, str):
        raw = raw.strip()
    if field in ("unit_price", "quantity", "total_price"):
        if isinstance(raw, (int, float)):
            return float(raw)
        return parse_decimal(raw)
    if field == "colisage":
        try:
            if isinstance(raw, (int, float)):
                value = float(raw)
            else:
                value = parse_decimal(raw)
        except (TypeError, ValueError):
            return 1
        if value < 1:
            return 1
        return value if value != int(value) else int(value)
    return str(raw)


class JsonConfigParser(BaseParser):
    """Parser built from a JSON configuration dict."""

    def __init__(self, config: dict, source_path: Path | None = None):
        self.config = config
        self.source_path = source_path

        try:
            self.parser_type = config["parser_type"]
        except KeyError as exc:
            raise ValueError(f"Parser config missing 'parser_type' ({source_path})") from exc

        self.display_name = config.get("display_name", self.parser_type)
        self.description = config.get("description", "")

        detection = config.get("detection") or {}
        self.required_markers = list(detection.get("required_markers", []))

        entries_cfg = config.get("entries")
        if not entries_cfg:
            raise ValueError(f"[{self.parser_type}] config missing 'entries' section")

        strategy = entries_cfg.get("strategy")
        if strategy not in ("line", "anchored"):
            raise ValueError(
                f"[{self.parser_type}] unknown entries.strategy: {strategy!r} "
                "(must be 'line' or 'anchored')"
            )
        self.strategy = strategy

        flags = _compile_flags(entries_cfg.get("flags"))
        transform_provides = set(entries_cfg.get("transform_provides") or [])

        if strategy == "line":
            pattern = entries_cfg.get("pattern")
            if not pattern:
                raise ValueError(f"[{self.parser_type}] line strategy requires 'pattern'")
            self.line_re = re.compile(pattern, flags)
            captured = set(self.line_re.groupindex.keys())
            missing = REQUIRED_FIELDS - captured - transform_provides
            if missing:
                raise ValueError(
                    f"[{self.parser_type}] line pattern missing named groups: "
                    f"{sorted(missing)} (declare them in 'transform_provides' "
                    "if a transform supplies them)"
                )
        else:
            anchor_pattern = entries_cfg.get("anchor_pattern")
            section_pattern = entries_cfg.get("section_pattern")
            if not anchor_pattern or not section_pattern:
                raise ValueError(
                    f"[{self.parser_type}] anchored strategy requires both "
                    "'anchor_pattern' and 'section_pattern'"
                )
            self.anchor_re = re.compile(anchor_pattern, flags)
            self.section_re = re.compile(section_pattern, flags)
            if (
                "article_id" not in self.anchor_re.groupindex
                and "article_id" not in transform_provides
            ):
                raise ValueError(
                    f"[{self.parser_type}] anchor_pattern must contain "
                    "(?P<article_id>...)"
                )
            captured = set(self.section_re.groupindex.keys()) | {"article_id"}
            missing = REQUIRED_FIELDS - captured - transform_provides
            if missing:
                raise ValueError(
                    f"[{self.parser_type}] section_pattern missing named groups: "
                    f"{sorted(missing)} (declare them in 'transform_provides' "
                    "if a transform supplies them)"
                )

        self.entry_transforms = list(entries_cfg.get("entry_transforms") or [])
        for name in self.entry_transforms:
            if name not in ENTRY_TRANSFORMS:
                raise ValueError(
                    f"[{self.parser_type}] unknown entry transform: {name!r} "
                    f"(available: {sorted(ENTRY_TRANSFORMS)})"
                )

        invoice_date_cfg = config.get("invoice_date") or {}
        self.invoice_date_re = (
            re.compile(invoice_date_cfg["pattern"]) if invoice_date_cfg.get("pattern") else None
        )
        self.invoice_date_formats = list(invoice_date_cfg.get("formats", []))

        total_cfg = config.get("total_price") or {}
        self.total_re = re.compile(total_cfg["pattern"]) if total_cfg.get("pattern") else None

    # ------------------------------------------------------------------ helpers

    def _extract_date(self, text: str):
        if self.invoice_date_re is not None:
            match = self.invoice_date_re.search(text)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
                for fmt in self.invoice_date_formats:
                    try:
                        return datetime.strptime(value, fmt).date()
                    except ValueError:
                        continue
        return parse_date_from_text(text)

    def _extract_total(self, text: str, entries: list[ParsedEntry]) -> float:
        if self.total_re is not None:
            match = self.total_re.search(text)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
                try:
                    return parse_decimal(value)
                except (TypeError, ValueError):
                    pass
        return round(sum(entry.total_price for entry in entries), 2)

    def _build_entry(self, raw: dict) -> ParsedEntry:
        for transform_name in self.entry_transforms:
            raw = ENTRY_TRANSFORMS[transform_name](raw)
        product_name = _coerce("product_name", raw.get("product_name"))
        ean_raw = raw.get("ean")
        ean = None
        if ean_raw is not None:
            candidate = str(ean_raw).strip()
            if candidate:
                ean = candidate
        return ParsedEntry(
            article_id=_coerce("article_id", raw.get("article_id")),
            product_name=" ".join(product_name.split()),
            quantity=_coerce("quantity", raw.get("quantity")),
            colisage=_coerce("colisage", raw.get("colisage")),
            unit_price=_coerce("unit_price", raw.get("unit_price")),
            total_price=_coerce("total_price", raw.get("total_price")),
            ean=ean,
        )

    # ----------------------------------------------------------------- strategies

    def _parse_anchored(self, text: str) -> list[ParsedEntry]:
        entries: list[ParsedEntry] = []
        anchors = list(self.anchor_re.finditer(text))
        for index, anchor in enumerate(anchors):
            section_start = anchor.end()
            section_end = (
                anchors[index + 1].start() if index + 1 < len(anchors) else len(text)
            )
            section = text[section_start:section_end]
            match = self.section_re.search(section)
            if not match:
                continue
            raw = dict(match.groupdict())
            raw["article_id"] = anchor.group("article_id")
            for key, value in anchor.groupdict().items():
                if key not in raw and value is not None:
                    raw[key] = value
            entries.append(self._build_entry(raw))
        return entries

    def _parse_line(self, text: str) -> list[ParsedEntry]:
        return [self._build_entry(dict(match.groupdict())) for match in self.line_re.finditer(text)]

    # --------------------------------------------------------------------- API

    def parse(self, text: str) -> ParsedInvoice:
        if self.strategy == "anchored":
            entries = self._parse_anchored(text)
        else:
            entries = self._parse_line(text)

        if not entries:
            raise ValueError(f"No entries parsed for {self.parser_type} invoice")

        return ParsedInvoice(
            parser_type=self.parser_type,
            invoice_date=self._extract_date(text),
            total_price=self._extract_total(text, entries),
            entries=entries,
        )
