from flask import Blueprint, jsonify, request
from sqlalchemy import or_

from ..auth import issue_token, require_auth, verify_password
from ..extensions import db
from ..models import Cart, CartItem, Invoice, InvoiceEntry, Product, User
from ..pdf_parsers import registry as parser_registry
from ..services.invoice_service import delete_invoice_and_recalculate, parse_and_store_invoice

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _invoice_to_dict(invoice: Invoice):
    return {
        "id": invoice.id,
        "type": invoice.parser_type,
        "date": invoice.invoice_date.isoformat(),
        "total_price": invoice.total_price,
        "filename": invoice.filename,
        "entries": [
            {
                "id": e.id,
                "article_id": e.article_id,
                "ean": e.ean,
                "product_id": e.product_id,
                "shop": e.product.shop,
                "product_name": e.product.natural_name or e.product.source_name,
                "quantity": e.quantity,
                "colisage": e.colisage,
                "unit_price": e.unit_price,
                "pack_price": round(e.unit_price * e.colisage, 3),
                "total_price": e.total_price,
            }
            for e in invoice.entries
        ],
    }


def _active_cart_for_user(user_id: int) -> Cart:
    cart = Cart.query.filter_by(user_id=user_id, status="active").first()
    if cart is None:
        cart = Cart(user_id=user_id, status="active")
        db.session.add(cart)
        db.session.commit()
    return cart


@api_bp.post("/auth/login")
def api_login():
    payload = request.get_json(silent=True) or {}
    username = payload.get("username", "")
    password = payload.get("password", "")

    user = User.query.filter_by(username=username).first()
    if not user or not verify_password(user.password_hash, password):
        return jsonify({"error": "Invalid credentials"}), 401

    return jsonify({"token": issue_token(user)})


@api_bp.post("/invoices/upload")
@require_auth
def upload_invoice():
    if "file" not in request.files:
        return jsonify({"error": "Missing file"}), 400

    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF is supported"}), 400

    raw_parser_type = (request.form.get("parser_type") or "").strip()
    parser_type = raw_parser_type or None

    try:
        invoice, created = parse_and_store_invoice(
            file.filename, file.read(), parser_type=parser_type
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    payload = _invoice_to_dict(invoice)
    payload["already_exists"] = not created
    return jsonify(payload), 201 if created else 200


@api_bp.get("/parsers")
@require_auth
def list_parsers():
    try:
        return jsonify(parser_registry.list_parsers())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.get("/invoices")
@require_auth
def list_invoices():
    invoices = Invoice.query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).all()
    return jsonify([_invoice_to_dict(invoice) for invoice in invoices])


@api_bp.get("/invoices/<int:invoice_id>")
@require_auth
def invoice_detail(invoice_id: int):
    invoice = Invoice.query.get_or_404(invoice_id)
    return jsonify(_invoice_to_dict(invoice))


@api_bp.delete("/invoices/<int:invoice_id>")
@require_auth
def delete_invoice(invoice_id: int):
    invoice = Invoice.query.get_or_404(invoice_id)
    delete_invoice_and_recalculate(invoice)
    return jsonify({"status": "deleted"})


@api_bp.get("/products")
@require_auth
def list_products():
    query = request.args.get("q", "").strip()
    q = Product.query
    if query:
        like = f"%{query}%"
        q = q.filter(
            or_(
                Product.article_id.ilike(like),
                Product.source_name.ilike(like),
                Product.natural_name.ilike(like),
            )
        )
    products = q.order_by(Product.article_id.asc()).all()
    return jsonify(
        [
            {
                "id": p.id,
                "article_id": p.article_id,
                "ean": p.ean,
                "shop": p.shop,
                "source_name": p.source_name,
                "natural_name": p.natural_name,
                "image_url": p.image_url,
                "latest_price": p.latest_unit_price,
            }
            for p in products
        ]
    )


@api_bp.patch("/products/<int:product_id>")
@require_auth
def update_product(product_id: int):
    payload = request.get_json(silent=True) or {}
    natural_name = (payload.get("natural_name") or "").strip()
    product = Product.query.get_or_404(product_id)
    product.natural_name = natural_name or None
    db.session.commit()
    return jsonify({"id": product.id, "natural_name": product.natural_name})


@api_bp.get("/products/<int:product_id>/history")
@require_auth
def product_history(product_id: int):
    product = Product.query.get_or_404(product_id)
    entries = (
        InvoiceEntry.query.join(Invoice, Invoice.id == InvoiceEntry.invoice_id)
        .filter(InvoiceEntry.product_id == product_id)
        .order_by(Invoice.invoice_date.asc(), Invoice.id.asc())
        .all()
    )
    return jsonify(
        {
            "product": {
                "id": product.id,
                "article_id": product.article_id,
                "ean": product.ean,
                "name": product.natural_name or product.source_name,
                "pack_size": product.pack_size,
                "image_url": product.image_url,
                "latest_price": product.latest_unit_price,
            },
            "history": [
                {
                    "invoice_id": e.invoice_id,
                    "date": e.invoice.invoice_date.isoformat(),
                    "quantity": e.quantity,
                    "colisage": e.colisage,
                    "unit_price": e.unit_price,
                    "pack_price": round(e.unit_price * e.colisage, 3),
                    "total_price": e.total_price,
                }
                for e in entries
            ],
        }
    )


@api_bp.get("/cart")
@require_auth
def get_cart():
    from flask import g

    cart = _active_cart_for_user(g.current_user.id)
    items = []
    estimated_total = 0.0

    for item in cart.items:
        line_total = item.quantity * item.unit_price_snapshot
        estimated_total += line_total
        items.append(
            {
                "id": item.id,
                "product_id": item.product_id,
                "article_id": item.product.article_id,
                "shop": item.product.shop,
                "name": item.product.natural_name or item.product.source_name,
                "quantity": item.quantity,
                "estimated_unit_price": item.unit_price_snapshot,
                "estimated_total": round(line_total, 2),
            }
        )

    items.sort(key=lambda item: ((item.get("shop") or "").lower(), item["name"].lower()))
    return jsonify({"id": cart.id, "items": items, "estimated_total": round(estimated_total, 2)})


@api_bp.post("/cart/items")
@require_auth
def add_cart_item():
    from flask import g

    payload = request.get_json(silent=True) or {}
    product_id = payload.get("product_id")
    quantity = float(payload.get("quantity", 0))
    if not product_id or quantity <= 0:
        return jsonify({"error": "product_id and positive quantity are required"}), 400

    product = Product.query.get_or_404(product_id)
    cart = _active_cart_for_user(g.current_user.id)

    item = CartItem.query.filter_by(cart_id=cart.id, product_id=product.id).first()
    if item is None:
        item = CartItem(
            cart_id=cart.id,
            product_id=product.id,
            quantity=quantity,
            unit_price_snapshot=product.latest_unit_price or 0.0,
        )
        db.session.add(item)
    else:
        item.quantity = quantity
        item.unit_price_snapshot = product.latest_unit_price or 0.0

    db.session.commit()
    return jsonify({"id": item.id, "quantity": item.quantity}), 201


@api_bp.delete("/cart/items/<int:item_id>")
@require_auth
def remove_cart_item(item_id: int):
    item = CartItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({"status": "deleted"})


@api_bp.delete("/cart/empty")
@require_auth
def empty_cart():
    from flask import g

    cart = _active_cart_for_user(g.current_user.id)
    CartItem.query.filter_by(cart_id=cart.id).delete()
    db.session.commit()
    return jsonify({"status": "emptied"})
