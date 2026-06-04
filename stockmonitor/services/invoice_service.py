from io import BytesIO
from hashlib import sha256

from pypdf import PdfReader
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Invoice, InvoiceEntry, Product
from ..pdf_parsers import registry


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_and_store_invoice(
    filename: str,
    pdf_bytes: bytes,
    parser_type: str | None = None,
) -> tuple[Invoice, bool]:
    content_hash = sha256(pdf_bytes).hexdigest()
    existing_invoice = Invoice.query.filter_by(pdf_sha256=content_hash).first()
    if existing_invoice is not None:
        return existing_invoice, False

    text = extract_text_from_pdf_bytes(pdf_bytes)
    if parser_type:
        parser = registry.get_parser(parser_type)
    else:
        parser = registry.detect_parser(text)
    parsed = parser.parse(text)

    invoice = Invoice(
        parser_type=parsed.parser_type,
        invoice_date=parsed.invoice_date,
        total_price=parsed.total_price,
        filename=filename,
        pdf_sha256=content_hash,
        pdf_data=pdf_bytes,
        pdf_mime="application/pdf",
    )
    db.session.add(invoice)
    db.session.flush()

    for parsed_entry in parsed.entries:
        product = Product.query.filter_by(article_id=parsed_entry.article_id).first()
        if product is None:
            product = Product(
                article_id=parsed_entry.article_id,
                shop=parsed.parser_type,
                source_name=parsed_entry.product_name,
                pack_size=parsed_entry.colisage,
                latest_unit_price=round(parsed_entry.unit_price * parsed_entry.colisage, 3),
            )
            db.session.add(product)
            db.session.flush()
        else:
            product.shop = parsed.parser_type
            product.source_name = parsed_entry.product_name
            product.pack_size = parsed_entry.colisage
            product.latest_unit_price = round(parsed_entry.unit_price * parsed_entry.colisage, 3)

        entry = InvoiceEntry(
            invoice_id=invoice.id,
            product_id=product.id,
            article_id=parsed_entry.article_id,
            quantity=parsed_entry.quantity,
            colisage=parsed_entry.colisage,
            unit_price=parsed_entry.unit_price,
            total_price=parsed_entry.total_price,
        )
        db.session.add(entry)

    try:
        db.session.commit()
        return invoice, True
    except IntegrityError:
        # Another request inserted the same hash concurrently.
        db.session.rollback()
        existing_invoice = Invoice.query.filter_by(pdf_sha256=content_hash).first()
        if existing_invoice is not None:
            return existing_invoice, False
        raise


def delete_invoice_and_recalculate(invoice: Invoice) -> None:
    affected_product_ids = {entry.product_id for entry in invoice.entries}
    db.session.delete(invoice)
    db.session.flush()

    for product_id in affected_product_ids:
        product = Product.query.get(product_id)
        if product is None:
            continue

        latest_entry = (
            InvoiceEntry.query.join(Invoice, Invoice.id == InvoiceEntry.invoice_id)
            .filter(InvoiceEntry.product_id == product_id)
            .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
            .first()
        )

        if latest_entry is None:
            product.latest_unit_price = None
            product.pack_size = 1
            product.shop = None
        else:
            product.latest_unit_price = round(latest_entry.unit_price * latest_entry.colisage, 3)
            product.pack_size = latest_entry.colisage
            product.shop = latest_entry.invoice.parser_type

    db.session.commit()
