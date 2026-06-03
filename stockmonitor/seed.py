from sqlalchemy.exc import IntegrityError

from .auth import hash_password
from .extensions import db
from .models import User


def seed_default_user():
    from flask import current_app

    username = current_app.config["DEFAULT_USERNAME"]
    password = current_app.config["DEFAULT_PASSWORD"]

    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username, password_hash=hash_password(password))
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # Another worker inserted the same default user concurrently.
            db.session.rollback()
