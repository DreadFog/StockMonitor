from functools import wraps

from flask import current_app, g, jsonify, redirect, request, session, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

from .models import User


def register_auth_helpers(app):
    app.auth_serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def issue_token(user: User) -> str:
    serializer: URLSafeTimedSerializer = current_app.auth_serializer
    return serializer.dumps({"user_id": user.id}, salt=current_app.config["TOKEN_SALT"])


def verify_token(token: str):
    serializer: URLSafeTimedSerializer = current_app.auth_serializer
    try:
        payload = serializer.loads(
            token,
            salt=current_app.config["TOKEN_SALT"],
            max_age=current_app.config["TOKEN_MAX_AGE_SECONDS"],
        )
    except (BadSignature, SignatureExpired):
        return None
    user_id = payload.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def require_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing bearer token"}), 401

        token = auth_header.split(" ", 1)[1].strip()
        user = verify_token(token)
        if not user:
            return jsonify({"error": "Invalid or expired token"}), 401

        g.current_user = user
        return func(*args, **kwargs)

    return wrapper


def require_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("web.login_page"))
        user = User.query.get(user_id)
        if not user:
            session.clear()
            return redirect(url_for("web.login_page"))
        g.current_user = user
        return func(*args, **kwargs)

    return wrapper
