import re

from .base import BaseParser, ParsedEntry, ParsedInvoice
from .utils import parse_date_from_text, parse_decimal


class CarigelParser(BaseParser):
    parser_type = "carigel"
    required_markers = ["ARTICLE", "DESIGNATION", "P.U. NET", "MONTANT"]

    line_pattern = re.compile(
        r"^(?P<name>.+?)(?P<article>[A-Z0-9]{6,10})\s+"
        r"\d+\s+(?P<total>\d+,\d{2})\s+(?P<unit>\d+,\d{3})(?P<tail>[\d,]*)\s*$",
        re.MULTILINE,
    )

    explicit_pattern = re.compile(
        r"^(?P<article>[A-Z0-9]{4,12})\s+"
        r"(?P<name>.+?)\s+"
        r"(?P<colisage>\d+(?:,\d+)?)\s+(?P<qty>\d+(?:,\d+)?)\s+"
        r"(?P<unit>\d+,\d{3})\s+(?P<total>\d+,\d{2})",
        re.MULTILINE,
    )

    def _infer_quantity(self, total_price: float, unit_price: float, tail: str) -> float:
        if unit_price <= 0:
            return 1.0
        # Some supplier PDFs flatten columns and merge trailing values (e.g. 7,18061),
        # so quantity is more reliable when derived from total/unit.
        return round(total_price / unit_price, 3)

    def parse(self, text: str) -> ParsedInvoice:
        entries: list[ParsedEntry] = []

        def normalize_article_and_name(article_id: str, name: str):
            if len(article_id) > 1 and article_id[0].isdigit() and article_id[1:].isalpha():
                if name.endswith("/"):
                    name = f"{name}{article_id[0]}"
                article_id = article_id[1:]
            return article_id, name

        for match in self.explicit_pattern.finditer(text):
            colisage = max(1, int(parse_decimal(match.group("colisage"))))
            quantity = parse_decimal(match.group("qty"))
            unit_price = parse_decimal(match.group("unit"))
            total_price = parse_decimal(match.group("total"))
            article_id = match.group("article")
            name = " ".join(match.group("name").split())
            article_id, name = normalize_article_and_name(article_id, name)
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
            for match in self.line_pattern.finditer(text):
                unit_price = parse_decimal(match.group("unit"))
                total_price = parse_decimal(match.group("total"))
                quantity = self._infer_quantity(total_price, unit_price, match.group("tail"))
                article_id = match.group("article")
                name = " ".join(match.group("name").split())
                article_id, name = normalize_article_and_name(article_id, name)
                entries.append(
                    ParsedEntry(
                        article_id=article_id,
                        product_name=name,
                        quantity=quantity,
                        colisage=1,
                        unit_price=unit_price,
                        total_price=total_price,
                    )
                )

        if not entries:
            raise ValueError("No entries parsed for Carigel invoice")

        total = round(sum(entry.total_price for entry in entries), 2)
        return ParsedInvoice(
            parser_type=self.parser_type,
            invoice_date=parse_date_from_text(text),
            total_price=total,
            entries=entries,
        )
