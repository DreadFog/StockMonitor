# Parser configurations

Each JSON file in this directory defines one supplier-specific PDF parser.
The directory is bind-mounted into the Docker container (see `docker-compose.yml`),
so adding or editing a file here takes effect on the next app restart : no rebuild
required. The location can also be overridden with the `PARSERS_CONFIG_DIR`
environment variable.

## Pipeline overview

1. PDF bytes are extracted to text with `pypdf` (default mode : note that this
   often re-orders columns and concatenates adjacent cells).
2. The user-selected parser (or the first that matches `detection.required_markers`)
   is loaded from this directory.
3. The parser runs its regex strategy (`line` or `anchored`) and produces one
   raw dict per line item.
4. Each dict is passed through the configured `entry_transforms` (in order).
5. Required fields are type-coerced into a `ParsedEntry` dataclass.
6. Optional `invoice_date` / `total_price` patterns extract header fields.

## JSON schema

```jsonc
{
  // Required: unique slug stored on each invoice row (also the dropdown value).
  "parser_type": "<slug>",

  // Optional: friendly label shown in the upload dropdown.
  "display_name": "<human readable>",

  // Optional: free-form notes about the supplier and PDF layout.
  "description": "<...>",

  // Optional but recommended: enables auto-detection when the user does not
  // pick a parser manually. ALL listed substrings must be present in the
  // (whitespace-normalised) extracted text.
  "detection": {
    "required_markers": ["MM EAN", "Désignation"]
  },

  // Optional: locate the invoice date. If omitted, a generic date search runs
  // over the whole text.
  //   - "pattern" must contain ONE capturing group (or match the date directly).
  //   - "formats" is a list of strptime formats tried in order.
  "invoice_date": {
    "pattern": "Date\\s+facture\\s*:?\\s*(\\d{2}[-/]\\d{2}[-/]\\d{2,4})",
    "formats": ["%d-%m-%Y", "%d/%m/%Y"]
  },

  // Optional: override the invoice grand-total. If omitted, the total is the
  // sum of per-line totals from the parsed entries.
  "total_price": {
    "pattern": "Total\\s+TTC[^0-9\\-]*(-?\\d+,\\d{2})"
  },

  // Required: how to extract line items.
  "entries": {
    // "line":     one regex captures all fields per match.
    // "anchored": two-stage: anchor_pattern locates each item, section_pattern
    //             extracts fields from the text between adjacent anchors.
    "strategy": "anchored",

    // Optional regex flags: MULTILINE, IGNORECASE, DOTALL, VERBOSE, UNICODE.
    "flags": ["MULTILINE"],

    // strategy = "line":
    //   "pattern" must contain ALL required named groups (see table below),
    //   unless a transform supplies them via "transform_provides".
    "pattern": "...",

    // strategy = "anchored":
    //   "anchor_pattern" must contain (?P<article_id>...) and locates each
    //                    item's starting position.
    //   "section_pattern" runs over the slice from one anchor's end to the
    //                    next anchor's start and supplies the remaining fields.
    "anchor_pattern": "(?P<ean>\\d{13})\\s+(?P<article_id>\\d{6,8})\\s+",
    "section_pattern": "(?P<product_name>.+?)\\s+...",

    // Optional: list of required fields that the regex does NOT capture
    // because an entry transform will compute them. Validation will skip
    // these when checking that all required named groups are present.
    "transform_provides": ["quantity", "colisage"],

    // Optional: ordered list of named transforms applied to each raw dict
    // before ParsedEntry coercion. See stockmonitor/pdf_parsers/transforms.py
    // for the available functions and how to add new ones.
    "entry_transforms": ["carigel_split_qty_colisage"]
  }
}
```

## Required fields

The parser engine builds a `ParsedEntry` per match. The following fields must
be present in the raw dict (collected across regex captures + transform output)
before coercion:

| Field          | Type    | Notes                                                            |
| -------------- | ------- | ---------------------------------------------------------------- |
| `article_id`   | string  | Unique supplier reference for the product.                       |
| `product_name` | string  | Whitespace is collapsed automatically.                           |
| `unit_price`   | decimal | Per-unit price. Comma decimal accepted (e.g. `0,553`).           |
| `colisage`     | decimal | Units (or kg, l, …) per pack. Use `1` for single-item products.  |
| `quantity`     | decimal | Number of packs ordered (not individual units).                  |
| `total_price`  | decimal | Line total. Should satisfy `unit_price × colisage × quantity`.   |

Coercion rules applied by the engine:

- `unit_price`, `quantity`, `total_price` → comma→dot float conversion.
- `colisage` → float when fractional (e.g. `2.5` kg packs), int otherwise; floored to `1`.
- `product_name` → stripped and inner whitespace collapsed.

## Extraction strategies

### `line`

The whole extracted text is searched with a single regex; every match becomes
one entry. All required named groups must live in this pattern, unless declared
in `transform_provides`. Add `"flags": ["MULTILINE"]` if your pattern relies on
`^`/`$` per line.

### `anchored`

Used when each item is split across multiple "logical lines" in the extracted
text, but every item is reliably preceded by some anchor (an EAN, an article id,
a date, …). The engine:

1. Finds every match of `anchor_pattern` (which must capture `article_id`).
2. For each anchor `i`, slices `text[anchor_i.end() : anchor_{i+1}.start()]` (or
   to end-of-text for the last one).
3. Runs `section_pattern` against that slice and merges the captured groups with
   `article_id` from the anchor.

If the section pattern fails to match for an anchor, that item is silently
skipped : handy for ignoring header rows or summary lines that look like product
anchors.

## Entry transforms

Transforms are named Python functions registered in
`stockmonitor/pdf_parsers/transforms.py`. Each one receives the raw dict and
must return a (possibly modified) dict.

Use a transform when the regex alone cannot disambiguate the layout, e.g. when
`pypdf` glues two columns together with no separator. The Carigel parser uses
this: the QUANTITE and COND' columns end up concatenated (`"110"` actually
means qty=1, colisage=10), so the regex captures the merged string into a
helper field `_tail` and the `carigel_split_qty_colisage` transform splits it
using the arithmetic identity `total = qty × colisage × unit_price`.

**Adding a new transform:**

1. Implement a function in `stockmonitor/pdf_parsers/transforms.py`:

   ```python
   def my_supplier_fix_name(raw: dict) -> dict:
       raw["product_name"] = raw["product_name"].title()
       return raw
   ```

2. Register it in the `ENTRY_TRANSFORMS` dict at the bottom of that file.
3. Reference it from your JSON config:

   ```jsonc
   "entry_transforms": ["my_supplier_fix_name"]
   ```

4. If the transform supplies a required field that the regex does not capture,
   list that field name in `transform_provides` so validation passes:

   ```jsonc
   "transform_provides": ["product_name"]
   ```

Transforms run in the order they are listed and may use any helper from
`stockmonitor/pdf_parsers/utils.py` (e.g. `parse_decimal`).

## Workflow tips

- **Inspect the raw text first.** PDFs rarely extract the way they look. Run
  `pypdf.PdfReader(path).pages[0].extract_text()` in a Python REPL to see what
  your regex has to work with. Try `extract_text(extraction_mode="layout")` to
  see the visible column layout side-by-side.
- **Develop the regex on the raw default-mode text**, since that is what the
  app passes to the parser at runtime.
- **Use `\\` for backslashes** inside JSON strings (`\\d`, `\\s`, …).
- **Test with the real PDFs in `test/`** before deploying:

  ```bash
  .venv/bin/python -c "
  from pypdf import PdfReader
  from stockmonitor.pdf_parsers import registry
  text = '\n'.join(p.extract_text() or '' for p in PdfReader('test/my_invoice.pdf').pages)
  parsed = registry.get_parser('<slug>').parse(text)
  for e in parsed.entries: print(e)
  "
  ```

## Adding a new supplier : checklist

1. Inspect the PDF in both default and layout extraction modes (see above).
2. Create `parsers/<slug>.json` with `parser_type`, `detection`, and `entries`.
3. (Optional) Add a transform in `stockmonitor/pdf_parsers/transforms.py` for
   any column-merging or normalisation the regex cannot handle.
4. `docker compose restart` (or full `up --build` if you added a transform).
5. The new parser appears in the upload form dropdown and becomes available
   for auto-detection (if `detection.required_markers` is set).
