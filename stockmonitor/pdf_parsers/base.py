from dataclasses import dataclass
from datetime import date
from typing import List


@dataclass
class ParsedEntry:
    article_id: str
    product_name: str
    quantity: float
    colisage: float
    unit_price: float
    total_price: float
    ean: str | None = None


@dataclass
class ParsedInvoice:
    parser_type: str
    invoice_date: date
    total_price: float
    entries: List[ParsedEntry]


class BaseParser:
    parser_type = "base"
    required_markers: list[str] = []

    def can_parse(self, text: str) -> bool:
        normalized = " ".join(text.split())
        return all(marker in normalized for marker in self.required_markers)

    def parse(self, text: str) -> ParsedInvoice:
        raise NotImplementedError
