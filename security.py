"""Security primitives shared by authentication, rate limiting, and auditing."""

import hashlib
import hmac
import re
from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import current_app, request
from werkzeug.security import check_password_hash

_password_hasher = PasswordHasher()


def hash_password(password):
    """Hash a new password with Argon2id."""
    return _password_hasher.hash(password)


def password_strength_error(password):
    """Return a safe validation message unless password length/variety is adequate."""
    if not isinstance(password, str) or len(password) < 12:
        return 'Use a password with at least 12 characters.'
    if len(password) > 128:
        return 'Use a password with no more than 128 characters.'
    categories = sum((
        bool(re.search(r'[a-z]', password)),
        bool(re.search(r'[A-Z]', password)),
        bool(re.search(r'\d', password)),
        bool(re.search(r'[^A-Za-z0-9]', password)),
    ))
    if categories < 3:
        return 'Use at least three of lowercase, uppercase, number, and symbol characters.'
    return None


def verify_password_and_update(stored_hash, password):
    """Verify Argon2id or legacy Werkzeug hashes and return an optional upgrade."""
    if not stored_hash or not isinstance(password, str):
        return False, None
    if stored_hash.startswith('$argon2id$'):
        try:
            verified = _password_hasher.verify(stored_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError, TypeError):
            return False, None
        if verified and _password_hasher.check_needs_rehash(stored_hash):
            return True, hash_password(password)
        return bool(verified), None

    try:
        verified = check_password_hash(stored_hash, password)
    except (TypeError, ValueError):
        return False, None
    return (True, hash_password(password)) if verified else (False, None)


def _hmac_key():
    key = current_app.config.get('SECURITY_HMAC_KEY') or current_app.config.get('SECRET_KEY')
    if not key:
        raise RuntimeError('A secret key is required for security identifiers.')
    return key.encode('utf-8') if isinstance(key, str) else key


def keyed_digest(purpose, value):
    """Return a purpose-separated HMAC-SHA256 digest for a bounded identifier."""
    message = f'{purpose}:{value}'.encode('utf-8', errors='replace')
    return hmac.new(_hmac_key(), message, hashlib.sha256).hexdigest()


def rate_limit_ip_key():
    """HMAC the direct client address; forwarded headers are ignored by default."""
    return keyed_digest('rate-ip', request.remote_addr or 'unknown')


def login_rate_limit_key():
    """Bind login throttling to both normalized account name and client address."""
    username = request.form.get('username', '') or request.form.get('student_id', '')
    if not username and request.is_json:
        payload = request.get_json(silent=True) or {}
        username = payload.get('username', '')
    account = str(username).strip().casefold()[:256]
    address = request.remote_addr or 'unknown'
    return keyed_digest('rate-login', f'{account}|{address}')


def revoke_user_sessions(user_id, *, now=None):
    """Revoke every session for a user without storing or exposing session IDs."""
    from models.user_session import UserSession

    revoked_at = now or datetime.utcnow()
    return UserSession.query.filter_by(user_id=user_id, revoked_at=None).update(
        {UserSession.revoked_at: revoked_at}, synchronize_session=False,
    )


def invalidate_password_reset_tokens(user_id, *, now=None):
    """Make all outstanding password-reset tokens for a user unusable."""
    from models.user_session import PasswordResetToken

    used_at = now or datetime.utcnow()
    return PasswordResetToken.query.filter_by(user_id=user_id, used_at=None).update(
        {PasswordResetToken.used_at: used_at}, synchronize_session=False,
    )
