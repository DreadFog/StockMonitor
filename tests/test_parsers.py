from stockmonitor.pdf_parsers.carigel import CarigelParser
from stockmonitor.pdf_parsers.metro import MetroParser


def test_metro_parser_line_and_date():
    text = """
Date facture : 02-05-2026
MM EAN Numéro Désignation Régie Vol VAP Poids Prix Qté Montant TVA Promo Extr.
3211200347681 2155737 ATLANTIQ IGP OCEADE RSE 75CL T 0,750 2,490 6 1 14,94 D
"""
    parsed = MetroParser().parse(text)
    assert parsed.parser_type == "metro"
    assert parsed.invoice_date.year == 2026
    assert len(parsed.entries) == 1
    assert parsed.entries[0].article_id == "2155737"
    assert parsed.entries[0].colisage == 6
    assert parsed.entries[0].quantity == 1
    assert parsed.entries[0].unit_price == 2.49
    assert parsed.entries[0].total_price == 14.94


def test_carigel_parser_explicit_format():
    text = """
ARTICLE DESIGNATION COND' QUANTITE P.U. NET MONTANT T V
12/05/2026
EBLETA TOMATE AROMATISEE/Mutti, 5/1 1 6 7,180 43,08 1
"""
    parsed = CarigelParser().parse(text)
    assert parsed.parser_type == "carigel"
    assert len(parsed.entries) == 1
    assert parsed.entries[0].article_id == "EBLETA"
    assert parsed.entries[0].colisage == 1
    assert parsed.entries[0].quantity == 6
    assert parsed.entries[0].unit_price == 7.18
    assert parsed.entries[0].total_price == 43.08
