from collections import defaultdict
import os
from uuid import uuid4

from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, send_file, session, url_for
from sqlalchemy import func, or_
from werkzeug.utils import secure_filename

from ..auth import issue_token, require_login, verify_password
from ..extensions import db
from ..models import Cart, CartItem, Invoice, InvoiceEntry, Product, User
from ..pdf_parsers import registry as parser_registry
from ..services.invoice_service import delete_invoice_and_recalculate, parse_and_store_invoice
from ..services.metro_image_service import (
    cached_image_path,
    ensure_cached_image,
    fetch_metro_image_url,
)

web_bp = Blueprint("web", __name__)

_ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def _active_cart_for_user(user_id: int) -> Cart:
    cart = Cart.query.filter_by(user_id=user_id, status="active").first()
    if cart is None:
        cart = Cart(user_id=user_id, status="active")
        db.session.add(cart)
        db.session.commit()
    return cart


def _safe_float(value: str | None, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback
    value = value.strip()
    if not value:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _custom_image_dir() -> str:
    path = os.path.join(current_app.instance_path, "product_images", "custom")
    os.makedirs(path, exist_ok=True)
    return path


def _delete_custom_image_file(filename: str | None) -> None:
    if not filename:
        return
    path = os.path.join(_custom_image_dir(), filename)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _is_allowed_image(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in _ALLOWED_IMAGE_EXTENSIONS


def _save_custom_product_image(product: Product, uploaded_file) -> bool:
    if not uploaded_file or not uploaded_file.filename:
        return False
    cleaned = secure_filename(uploaded_file.filename)
    if not cleaned or not _is_allowed_image(cleaned):
        return False
    ext = cleaned.rsplit(".", 1)[1].lower()
    new_filename = f"product_{product.id}_{uuid4().hex[:10]}.{ext}"
    target_path = os.path.join(_custom_image_dir(), new_filename)
    uploaded_file.save(target_path)

    old_filename = product.custom_image_filename
    product.custom_image_filename = new_filename
    product.image_url = None
    _delete_custom_image_file(old_filename)
    return True


def _build_custom_article_id(name: str) -> str:
    slug = "".join(ch for ch in name.lower().strip() if ch.isalnum())[:12] or "item"
    return f"custom-{slug}-{uuid4().hex[:6]}"


def _default_image_path() -> str | None:
    static_root = current_app.static_folder or ""
    candidates = (
        os.path.join(static_root, "images", "default_image.png"),
        os.path.join(static_root, "images", "default_image.jpg"),
        os.path.join(static_root, "images", "default_image.jpeg"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


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
    per_page_raw = (request.args.get("per_page") or "10").strip()
    try:
        per_page = int(per_page_raw)
    except ValueError:
        per_page = 10
    if per_page not in (10, 20, 50):
        per_page = 10

    page_raw = (request.args.get("page") or "1").strip()
    try:
        page = int(page_raw)
    except ValueError:
        page = 1
    if page < 1:
        page = 1

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
    pagination = q.order_by(Product.starred.desc(), Product.article_id.asc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    return render_template(
        "products.html",
        products=pagination.items,
        query=query,
        per_page=per_page,
        page=page,
        total_pages=pagination.pages,
        has_prev=pagination.has_prev,
        has_next=pagination.has_next,
        prev_num=pagination.prev_num,
        next_num=pagination.next_num,
        total_items=pagination.total,
    )


@web_bp.get("/products/new")
@require_login
def new_custom_product():
    return render_template("product_new.html")


@web_bp.post("/products/create")
@require_login
def create_custom_product():
    next_url = request.form.get("next") or url_for("web.products")
    source_name = (request.form.get("source_name") or "").strip()
    shop = (request.form.get("shop") or "").strip()
    price = _safe_float(request.form.get("latest_unit_price"), None)
    if not source_name or not shop or price is None or price <= 0:
        flash("Name, shop, and latest price are required", "error")
        return redirect(next_url)

    article_id = (request.form.get("article_id") or "").strip()
    if not article_id:
        article_id = _build_custom_article_id(source_name)

    if Product.query.filter_by(article_id=article_id).first() is not None:
        flash("Article ID already exists. Please choose another one.", "error")
        return redirect(next_url)

    pack_size = _safe_float(request.form.get("pack_size"), 1.0) or 1.0
    if pack_size <= 0:
        pack_size = 1.0

    product = Product(
        article_id=article_id,
        ean=(request.form.get("ean") or "").strip() or None,
        shop=shop,
        source_name=source_name,
        natural_name=(request.form.get("natural_name") or "").strip() or None,
        pack_size=pack_size,
        image_url=(request.form.get("image_url") or "").strip() or None,
        starred=request.form.get("starred") == "on",
        latest_unit_price=price,
    )
    db.session.add(product)
    db.session.commit()
    flash("Custom item added", "success")
    return redirect(url_for("web.product_detail", product_id=product.id))


@web_bp.post("/products/<int:product_id>/delete")
@require_login
def delete_product(product_id: int):
    product = Product.query.get_or_404(product_id)
    entry_count = InvoiceEntry.query.filter_by(product_id=product.id).count()
    if entry_count > 0:
        flash("Cannot delete this product because it is referenced by invoices", "error")
        next_url = request.form.get("next") or url_for("web.product_detail", product_id=product.id)
        return redirect(next_url)

    CartItem.query.filter_by(product_id=product.id).delete()
    _delete_custom_image_file(product.custom_image_filename)
    if product.image_url:
        cached_path = cached_image_path(product.image_url)
        if os.path.isfile(cached_path):
            try:
                os.remove(cached_path)
            except OSError:
                pass
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted", "success")
    return redirect(url_for("web.products"))


@web_bp.post("/products/<int:product_id>/toggle-star")
@require_login
def toggle_star(product_id: int):
    product = Product.query.get_or_404(product_id)
    product.starred = not bool(product.starred)
    db.session.commit()
    next_url = request.form.get("next") or url_for("web.products")
    return redirect(next_url)


@web_bp.post("/me/toggle-mobile-view")
@require_login
def toggle_mobile_view():
    g.current_user.mobile_view = not bool(g.current_user.mobile_view)
    db.session.commit()
    next_url = request.form.get("next") or url_for("web.dashboard")
    return redirect(next_url)


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


@web_bp.post("/products/<int:product_id>/image/upload")
@require_login
def upload_product_image(product_id: int):
    product = Product.query.get_or_404(product_id)
    uploaded = request.files.get("image_file")
    if not _save_custom_product_image(product, uploaded):
        flash("Invalid image file. Allowed: png, jpg, jpeg, webp, gif", "error")
        return redirect(url_for("web.product_detail", product_id=product.id))
    db.session.commit()
    flash("Image updated", "success")
    return redirect(url_for("web.product_detail", product_id=product.id))


@web_bp.post("/products/<int:product_id>/image/remove")
@require_login
def remove_product_image(product_id: int):
    product = Product.query.get_or_404(product_id)
    _delete_custom_image_file(product.custom_image_filename)
    product.custom_image_filename = None
    if product.image_url:
        cached_path = cached_image_path(product.image_url)
        if os.path.isfile(cached_path):
            try:
                os.remove(cached_path)
            except OSError:
                pass
    product.image_url = None
    db.session.commit()
    flash("Image removed", "success")
    return redirect(url_for("web.product_detail", product_id=product.id))


@web_bp.get("/products/<int:product_id>/image")
@require_login
def product_image(product_id: int):
    product = Product.query.get_or_404(product_id)

    if product.custom_image_filename:
        custom_path = os.path.join(_custom_image_dir(), product.custom_image_filename)
        if os.path.isfile(custom_path):
            return send_file(custom_path)
        product.custom_image_filename = None
        db.session.commit()

    # Lazy backfill: if we don't yet know the URL but we have an EAN, try once.
    if not product.image_url and product.ean:
        resolved_url = fetch_metro_image_url(product.ean)
        if resolved_url:
            product.image_url = resolved_url
            db.session.commit()

    if not product.image_url:
        default_path = _default_image_path()
        if default_path:
            return send_file(default_path)
        return Response(status=404)

    cached_path = ensure_cached_image(product.image_url)
    if cached_path is None:
        default_path = _default_image_path()
        if default_path:
            return send_file(default_path)
        return Response(status=502)

    return send_file(cached_path)


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
            .order_by(Product.starred.desc(), Product.shop.asc().nullslast(), Product.article_id.asc())
            .limit(20)
            .all()
        )
    else:
        product_results = (
            Product.query.filter(Product.starred.is_(True))
            .order_by(Product.shop.asc().nullslast(), Product.article_id.asc())
            .all()
        )
    results_are_suggestions = not search and bool(product_results)

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
        results_are_suggestions=results_are_suggestions,
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
