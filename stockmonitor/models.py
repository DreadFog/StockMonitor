from datetime import datetime

from .extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    mobile_view = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.String(120), unique=True, nullable=False, index=True)
    ean = db.Column(db.String(13), nullable=True, index=True)
    shop = db.Column(db.String(80), nullable=True, index=True)
    source_name = db.Column(db.String(255), nullable=False)
    natural_name = db.Column(db.String(255), nullable=True)
    pack_size = db.Column(db.Float, nullable=False, default=1)
    image_url = db.Column(db.String(500), nullable=True)
    custom_image_filename = db.Column(db.String(255), nullable=True)
    starred = db.Column(db.Boolean, nullable=False, default=False, index=True)
    latest_unit_price = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entries = db.relationship("InvoiceEntry", back_populates="product", lazy=True)


class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    parser_type = db.Column(db.String(80), nullable=False)
    invoice_date = db.Column(db.Date, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    pdf_sha256 = db.Column(db.String(64), nullable=True, index=True)
    pdf_data = db.Column(db.LargeBinary, nullable=False)
    pdf_mime = db.Column(db.String(64), nullable=False, default="application/pdf")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    entries = db.relationship("InvoiceEntry", back_populates="invoice", cascade="all, delete-orphan", lazy=True)


class InvoiceEntry(db.Model):
    __tablename__ = "invoice_entries"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    article_id = db.Column(db.String(120), nullable=False)
    ean = db.Column(db.String(13), nullable=True, index=True)
    quantity = db.Column(db.Float, nullable=False)
    colisage = db.Column(db.Float, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)

    invoice = db.relationship("Invoice", back_populates="entries")
    product = db.relationship("Product", back_populates="entries")


class Cart(db.Model):
    __tablename__ = "carts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="active")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    items = db.relationship("CartItem", back_populates="cart", cascade="all, delete-orphan", lazy=True)


class CartItem(db.Model):
    __tablename__ = "cart_items"

    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.Integer, db.ForeignKey("carts.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit_price_snapshot = db.Column(db.Float, nullable=False)

    cart = db.relationship("Cart", back_populates="items")
    product = db.relationship("Product")
