import os
from hashlib import sha256

from flask import Flask
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from .auth import register_auth_helpers
from .config import Config
from .extensions import db
from .models import Invoice, InvoiceEntry, Product
from .routes.api import api_bp
from .routes.web import web_bp
from .seed import seed_default_user


def _ensure_runtime_schema():
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()
    if "products" not in table_names:
        return

    product_columns = {column["name"] for column in inspector.get_columns("products")}
    if "shop" not in product_columns:
        try:
            db.session.execute(text("ALTER TABLE products ADD COLUMN shop VARCHAR(80)"))
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
    if "pack_size" not in product_columns:
        try:
            db.session.execute(text("ALTER TABLE products ADD COLUMN pack_size INTEGER NOT NULL DEFAULT 1"))
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()

    products_without_shop = Product.query.filter(Product.shop.is_(None)).all()
    for product in products_without_shop:
        latest_entry = (
            InvoiceEntry.query.join(Invoice, Invoice.id == InvoiceEntry.invoice_id)
            .filter(InvoiceEntry.product_id == product.id)
            .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
            .first()
        )
        if latest_entry:
            product.shop = latest_entry.invoice.parser_type

    if "invoices" in table_names:
        invoice_columns = {column["name"] for column in inspector.get_columns("invoices")}
        if "pdf_sha256" not in invoice_columns:
            try:
                db.session.execute(text("ALTER TABLE invoices ADD COLUMN pdf_sha256 VARCHAR(64)"))
                db.session.commit()
            except SQLAlchemyError:
                # Another worker may have added the column concurrently.
                db.session.rollback()

        invoices_without_hash = Invoice.query.filter(Invoice.pdf_sha256.is_(None)).all()
        for invoice in invoices_without_hash:
            invoice.pdf_sha256 = sha256(invoice.pdf_data).hexdigest()

        if "invoice_entries" in table_names:
            entry_columns = {column["name"] for column in inspector.get_columns("invoice_entries")}
            if "colisage" not in entry_columns:
                try:
                    db.session.execute(text("ALTER TABLE invoice_entries ADD COLUMN colisage INTEGER NOT NULL DEFAULT 1"))
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()

        # Keep only the oldest invoice row for a given hash before enabling uniqueness.
        duplicate_hash_rows = db.session.execute(
            text(
                """
                SELECT pdf_sha256, GROUP_CONCAT(id)
                FROM invoices
                WHERE pdf_sha256 IS NOT NULL
                GROUP BY pdf_sha256
                HAVING COUNT(*) > 1
                """
            )
        ).all()
        duplicate_ids_to_delete: list[int] = []
        for _, id_list in duplicate_hash_rows:
            ids = sorted(int(value) for value in str(id_list).split(",") if value)
            duplicate_ids_to_delete.extend(ids[1:])

        for invoice_id in duplicate_ids_to_delete:
            invoice = Invoice.query.get(invoice_id)
            if invoice is not None:
                db.session.delete(invoice)

        # Persist duplicate cleanup before adding the unique index.
        db.session.commit()

        try:
            db.session.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_invoices_pdf_sha256 "
                    "ON invoices(pdf_sha256) WHERE pdf_sha256 IS NOT NULL"
                )
            )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
    db.session.commit()


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    os.makedirs(app.instance_path, exist_ok=True)

    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    prefix = "sqlite:///instance/"
    if uri.startswith(prefix):
        db_name = uri[len(prefix) :]
        absolute_path = os.path.join(app.instance_path, db_name)
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{absolute_path}"

    db.init_app(app)
    register_auth_helpers(app)

    with app.app_context():
        db.create_all()
        _ensure_runtime_schema()
        seed_default_user()

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)

    return app
