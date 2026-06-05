from stockmonitor.pdf_parsers import registry


def test_registry_loads_configs():
    parser_types = {p["parser_type"] for p in registry.list_parsers()}
    assert {"metro", "carigel"}.issubset(parser_types)


def test_metro_parser_line_and_date():
    parser = registry.get_parser("metro")
    text = """
Date facture : 02-05-2026
MM EAN Numéro Désignation Régie Vol VAP Poids Prix Qté Montant TVA Promo Extr.
3119780268276 2017291 HEINEKEN PACK 5D 20X25CL VP B 5,0 0,013 0,250 0,553 20 1 11,06 D
3211200347681 2155737 ATLANTIQ IGP OCEADE RSE 75CL T 0,750 2,490 6 1 14,94 D
"""
    parsed = parser.parse(text)
    assert parsed.parser_type == "metro"
    assert parsed.invoice_date.year == 2026
    assert parsed.invoice_date.month == 5
    assert len(parsed.entries) == 2

    heineken = parsed.entries[0]
    assert heineken.article_id == "2017291"
    assert heineken.ean == "3119780268276"
    assert heineken.colisage == 20
    assert heineken.quantity == 1
    assert heineken.unit_price == 0.553
    assert heineken.total_price == 11.06

    atlantiq = parsed.entries[1]
    assert atlantiq.article_id == "2155737"
    assert atlantiq.ean == "3211200347681"
    assert atlantiq.colisage == 6
    assert atlantiq.quantity == 1
    assert atlantiq.unit_price == 2.49
    assert atlantiq.total_price == 14.94


def test_carigel_parser_real_layout():
    parser = registry.get_parser("carigel")
    text = """
26006370 12/05/2026 PZWAYCO 004 RUBIO
ARTICLE VTMONTANTP.U. NETQUANTITECOND'DESIGNATION
TOMATE AROMATISEE/Mutti, 5/1EBLETA 1 43,08 7,18061
ROQUEFORT MINIDES/ Société ,500gFFROFROM 1 9,63 9,62611
MOZZARELLA COSSETTE/ Maestrella, 2.5kgFGMOCOA 1 44,75 5,96732,5
EGRENE BOEUF HALLAL 20%/ U E, 1kgSBBOSHOI 1 113,51 11,351101
COCKTAIL FRUIT DE MER STD/Mousse, 1kgSPCOCKTA 1 26,56 6,64041
FARINE DE BLE T55/ France 1kgx10ESFEFAR 1 5,30 0,530110
CREME FRAICHE  EPAISSE PIZZA/ Flory, 5lFCFREP5 1 60,00 15,00041
EMMENTAL RAPE Francais/S  Grancoeur, 1kgFGRAPP 1 60,00 6,000101
"""
    parsed = parser.parse(text)
    assert parsed.parser_type == "carigel"
    assert len(parsed.entries) == 8

    by_article = {e.article_id: e for e in parsed.entries}

    # qty=6 packs of colisage=1 → total 43.08
    tomate = by_article["EBLETA"]
    assert tomate.ean is None
    assert tomate.quantity == 6
    assert tomate.colisage == 1
    assert tomate.unit_price == 7.180
    assert tomate.total_price == 43.08

    # qty=3 packs of colisage=2.5 (kg) → total 44.75 (decimal colisage preserved)
    mozza = by_article["FGMOCOA"]
    assert mozza.quantity == 3
    assert mozza.colisage == 2.5
    assert mozza.unit_price == 5.967
    assert round(mozza.total_price, 2) == 44.75

    # qty=1 pack of colisage=10 (1kg flour x10) → total 5.30
    farine = by_article["ESFEFAR"]
    assert farine.quantity == 1
    assert farine.colisage == 10
    assert farine.unit_price == 0.530
    assert farine.total_price == 5.30

    # qty=10 packs of colisage=1 → total 113.51
    boeuf = by_article["SBBOSHOI"]
    assert boeuf.quantity == 10
    assert boeuf.colisage == 1
    assert boeuf.unit_price == 11.351


def test_auto_detect_metro():
    text = (
        "MM EAN Numéro Désignation Régie Vol VAP Poids Prix Qté Montant TVA Promo Extr.\n"
        "3119780268276 2017291 HEINEKEN PACK 5D 20X25CL VP B 5,0 0,013 0,250 0,553 20 1 11,06 D\n"
    )
    parser = registry.detect_parser(text)
    assert parser.parser_type == "metro"


def test_auto_detect_carigel():
    text = (
        "ARTICLE VTMONTANTP.U. NETQUANTITECOND'DESIGNATION\n"
        "TOMATE AROMATISEE/Mutti, 5/1EBLETA 1 43,08 7,18061\n"
    )
    parser = registry.detect_parser(text)
    assert parser.parser_type == "carigel"