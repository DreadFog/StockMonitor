from collections import defaultdict

from flask import Blueprint, Response, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_

from ..auth import issue_token, require_login, verify_password
from ..extensions import db
from ..models import Cart, CartItem, Invoice, InvoiceEntry, Product, User
from ..pdf_parsers import registry as parser_registry
from ..services.invoice_service import delete_invoice_and_recalculate, parse_and_store_invoice

web_bp = Blueprint("web", __name__)


def _active_cart_for_user(user_id: int) -> Cart:
    cart = Cart.query.filter_by(user_id=user_id, status="active").first()
    if cart is None:
        cart = Cart(user_id=user_id, status="active")
        db.session.add(cart)
        db.session.commit()
    return cart


@web_bp.get("/login")
def login_page():
    if session.get("user_id"):
        return redirect(url_for("web.dashboard"))
    return render_template("login.html")


@web_bp.post("/login")
def login_submit():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    user = User.query.filter_by(username=username).first()
    if not user or not verify_password(user.password_hash, password):
        flash("Invalid credentials", "error")
        return redirect(url_for("web.login_page"))

    session["user_id"] = user.id
    session["api_token"] = issue_token(user)
    return redirect(url_for("web.dashboard"))


@web_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.login_page"))


@web_bp.get("/")
@require_login
def dashboard():
    stats = {
        "products": Product.query.count(),
        "invoices": Invoice.query.count(),
        "entries": InvoiceEntry.query.count(),
        "latest_total": db.session.query(func.sum(Invoice.total_price)).scalar() or 0,
    }
    return render_template("dashboard.html", stats=stats)


@web_bp.route("/invoices", methods=["GET", "POST"])
@require_login
def invoices():
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            flash("Please upload a PDF file", "error")
            return redirect(url_for("web.invoices"))

        raw_parser_type = (request.form.get("parser_type") or "").strip()
        parser_type = raw_parser_type or None

        try:
            _, created = parse_and_store_invoice(file.filename, file.read(), parser_type=parser_type)
            if created:
                flash("Invoice parsed and saved", "success")
            else:
                flash("This invoice is already imported", "success")
        except Exception as exc:
            flash(f"Failed to parse invoice: {exc}", "error")
        return redirect(url_for("web.invoices"))

    invoices_data = Invoice.query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).all()
    try:
        available_parsers = parser_registry.list_parsers()
    except Exception as exc:
        available_parsers = []
        flash(f"Parser configurations could not be loaded: {exc}", "error")
    return render_template(
        "invoices.html",
        invoices=invoices_data,
        available_parsers=available_parsers,
    )


@web_bp.get("/invoices/<int:invoice_id>/pdf")
@require_login
def invoice_pdf(invoice_id: int):
    invoice = Invoice.query.get_or_404(invoice_id)
    return Response(
        invoice.pdf_data,
        mimetype=invoice.pdf_mime,
        headers={"Content-Disposition": f"inline; filename={invoice.filename}"},
    )


@web_bp.get("/invoices/<int:invoice_id>")
@require_login
def invoice_detail_page(invoice_id: int):
    invoice = Invoice.query.get_or_404(invoice_id)
    return render_template("invoice_detail.html", invoice=invoice)


@web_bp.post("/invoices/<int:invoice_id>/delete")
@require_login
def delete_invoice_page(invoice_id: int):
    invoice = Invoice.query.get_or_404(invoice_id)
    delete_invoice_and_recalculate(invoice)
    flash("Invoice deleted", "success")
    return redirect(url_for("web.invoices"))


@web_bp.get("/products")
@require_login
def products():
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
    products_data = q.order_by(Product.article_id.asc()).all()
    return render_template("products.html", products=products_data, query=query)


@web_bp.post("/products/<int:product_id>/rename")
@require_login
def rename_product(product_id: int):
    product = Product.query.get_or_404(product_id)
    value = (request.form.get("natural_name") or "").strip()
    product.natural_name = value or None
    db.session.commit()
    flash("Product name updated", "success")
    return redirect(url_for("web.product_detail", product_id=product.id))


@web_bp.get("/products/<int:product_id>")
@require_login
def product_detail(product_id: int):
    product = Product.query.get_or_404(product_id)
    entries = (
        InvoiceEntry.query.join(Invoice, Invoice.id == InvoiceEntry.invoice_id)
        .filter(InvoiceEntry.product_id == product.id)
        .order_by(Invoice.invoice_date.asc(), Invoice.id.asc())
        .all()
    )
    timeline = [
        {
            "date": entry.invoice.invoice_date.isoformat(),
            "unit_price": entry.unit_price,
            "pack_price": round(entry.unit_price * entry.colisage, 3),
            "colisage": entry.colisage,
            "quantity": entry.quantity,
        }
        for entry in entries
    ]
    return render_template("product_detail.html", product=product, entries=entries, timeline=timeline)


@web_bp.get("/cart")
@require_login
def cart_page():
    cart = _active_cart_for_user(g.current_user.id)
    search = request.args.get("q", "").strip()
    product_results = []
    if search:
        like = f"%{search}%"
        product_results = (
            Product.query.filter(
                or_(
                    Product.article_id.ilike(like),
                    Product.source_name.ilike(like),
                    Product.natural_name.ilike(like),
                )
            )
            .order_by(Product.shop.asc().nullslast(), Product.article_id.asc())
            .limit(20)
            .all()
        )

    estimated_total = sum(item.quantity * item.unit_price_snapshot for item in cart.items)
    grouped_items: dict[str, list[CartItem]] = defaultdict(list)
    for item in cart.items:
        grouped_items[item.product.shop or "Unknown"].append(item)

    shop_groups = []
    for shop_name in sorted(grouped_items.keys(), key=lambda value: value.lower()):
        items = sorted(
            grouped_items[shop_name],
            key=lambda item: (item.product.natural_name or item.product.source_name).lower(),
        )
        subtotal = sum(item.quantity * item.unit_price_snapshot for item in items)
        shop_groups.append({"shop": shop_name, "items": items, "subtotal": subtotal})

    return render_template(
        "cart.html",
        cart=cart,
        shop_groups=shop_groups,
        estimated_total=estimated_total,
        search=search,
        product_results=product_results,
    )


@web_bp.post("/cart/add")
@require_login
def cart_add():
    cart = _active_cart_for_user(g.current_user.id)
    product_id = int(request.form.get("product_id", 0))
    quantity = float(request.form.get("quantity", 0))
    if product_id <= 0 or quantity <= 0:
        flash("Please provide a valid product and quantity", "error")
        return redirect(url_for("web.cart_page"))

    product = Product.query.get_or_404(product_id)
    item = CartItem.query.filter_by(cart_id=cart.id, product_id=product.id).first()
    if item is None:
        item = CartItem(
            cart_id=cart.id,
            product_id=product.id,
            quantity=quantity,
            unit_price_snapshot=product.latest_unit_price or 0,
        )
        db.session.add(item)
    else:
        item.quantity = quantity
        item.unit_price_snapshot = product.latest_unit_price or 0

    db.session.commit()
    flash("Cart updated", "success")
    return redirect(url_for("web.cart_page"))


@web_bp.post("/cart/remove/<int:item_id>")
@require_login
def cart_remove(item_id: int):
    item = CartItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash("Item removed", "success")
    return redirect(url_for("web.cart_page"))


@web_bp.post("/cart/empty")
@require_login
def cart_empty():
    cart = _active_cart_for_user(g.current_user.id)
    CartItem.query.filter_by(cart_id=cart.id).delete()
    db.session.commit()
    flash("Cart emptied", "success")
    return redirect(url_for("web.cart_page"))
