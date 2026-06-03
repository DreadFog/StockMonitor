import re

from .base import BaseParser, ParsedEntry, ParsedInvoice
from .utils import parse_date_from_text, parse_decimal


class MetroParser(BaseParser):
    parser_type = "metro"
    required_markers = ["MM EAN", "Numéro", "Désignation"]

    anchor_pattern = re.compile(r"(?P<ean>\d{13})\s+(?P<article>\d{6,8})\s+")
    section_pattern = re.compile(
        r"(?P<name>.+?)\s+[A-Z]\s+"
        r"(?:\d+,\d{1,3}\s+){0,6}"
        r"(?P<unit>\d+,\d{3})\s+"
        r"(?P<colisage>\d+)\s+(?P<qty>\d+)\s+"
        r"(?P<total>-?\d+,\d{2})\s+[A-Z](?:\s+[A-Z])?"
    )

    def parse(self, text: str) -> ParsedInvoice:
        entries: list[ParsedEntry] = []
        anchors = list(self.anchor_pattern.finditer(text))

        for index, anchor in enumerate(anchors):
            section_start = anchor.end()
            section_end = anchors[index + 1].start() if index + 1 < len(anchors) else len(text)
            section = text[section_start:section_end]
            match = self.section_pattern.search(section)
            if not match:
                continue

            colisage = int(parse_decimal(match.group("colisage")))
            quantity = parse_decimal(match.group("qty"))
            unit_price = parse_decimal(match.group("unit"))
            total_price = parse_decimal(match.group("total"))
            article_id = anchor.group("article")
            name = " ".join(match.group("name").split())

            entries.append(
                ParsedEntry(
                    article_id=article_id,
                    product_name=name,
                    quantity=quantity,
                    colisage=colisage,
                    unit_price=unit_price,
                    total_price=total_price,
                )
            )

        if not entries:
            raise ValueError("No entries parsed for Metro invoice")

        total = round(sum(entry.total_price for entry in entries), 2)
        return ParsedInvoice(
            parser_type=self.parser_type,
            invoice_date=parse_date_from_text(text),
            total_price=total,
            entries=entries,
        )
