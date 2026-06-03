from .base import BaseParser
from .carigel import CarigelParser
from .metro import MetroParser


class ParserRegistry:
    def __init__(self):
        self.parsers: list[BaseParser] = [MetroParser(), CarigelParser()]

    def detect_parser(self, text: str) -> BaseParser:
        for parser in self.parsers:
            if parser.can_parse(text):
                return parser
        raise ValueError("No parser matched this invoice format")
