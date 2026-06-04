"""Named transformations applied to a raw entry dict before ParsedEntry construction.

A transform receives the dict of captured groups and returns a (possibly modified)
dict. Register new transforms by adding them to ``ENTRY_TRANSFORMS`` below; they
become available to JSON parser configs via the ``entry_transforms`` key.
"""

from .utils import parse_decimal


def carigel_split_qty_colisage(raw: dict) -> dict:
    """Split the merged ``_tail`` (qty + colisage) on a Carigel line.

    pypdf default extraction concatenates the QUANTITE and COND' columns with
    no separator at the end of each line (e.g. tail ``"110"`` means qty=1,
    colisage=10; tail ``"32,5"`` means qty=3, colisage=2,5). We disambiguate
    by checking that ``total = qty * colisage * unit_price`` holds within a
    small tolerance.
    """
    tail = (raw.pop("_tail", "") or "").strip()
    if not tail:
        raw.setdefault("quantity", 1)
        raw.setdefault("colisage", 1)
        return raw

    unit_price = parse_decimal(raw["unit_price"])
    total_price = parse_decimal(raw["total_price"])
    expected = total_price / unit_price if unit_price else 0

    candidates: list[tuple[int, str]] = []
    if "," in tail:
        # The decimal slice must contain the comma. The colisage is the
        # shortest suffix containing the comma (e.g. "2,5"); the qty is
        # whatever digits precede it.
        comma_index = tail.index(",")
        # colisage candidate boundaries: from (comma_index - 1) back to 0
        for split in range(comma_index - 1, -1, -1):
            qty_str = tail[:split]
            cond_str = tail[split:]
            if not qty_str or not cond_str:
                continue
            if cond_str.startswith(",") or cond_str.endswith(","):
                continue
            candidates.append((split, qty_str + "|" + cond_str))
    else:
        # All digits: try every internal split point.
        for split in range(1, len(tail)):
            qty_str = tail[:split]
            cond_str = tail[split:]
            candidates.append((split, qty_str + "|" + cond_str))

    best: tuple[int, int] | None = None
    best_error = float("inf")
    for _, marker in candidates:
        qty_str, cond_str = marker.split("|", 1)
        try:
            qty_val = parse_decimal(qty_str)
            cond_val = parse_decimal(cond_str)
        except ValueError:
            continue
        if qty_val <= 0 or cond_val <= 0:
            continue
        product = qty_val * cond_val
        error = abs(product - expected)
        if error < best_error:
            best_error = error
            best = (qty_val, cond_val)

    if best is None or best_error > 0.05 * max(1.0, expected):
        # Fall back: assume single-unit (qty=tail, colisage=1).
        try:
            raw["quantity"] = parse_decimal(tail)
        except ValueError:
            raw["quantity"] = 1
        raw["colisage"] = 1
    else:
        qty_val, cond_val = best
        raw["quantity"] = qty_val
        raw["colisage"] = cond_val
    return raw


ENTRY_TRANSFORMS = {
    "carigel_split_qty_colisage": carigel_split_qty_colisage,
}
