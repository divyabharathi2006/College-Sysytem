import os
from datetime import timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database', 'attendance.db')
load_dotenv(os.path.join(BASE_DIR, '.env'))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or f'sqlite:///{DB_PATH}'
    SQLALCHEMY_BINDS = {
        'university_students': os.environ.get('UNIVERSITY_STUDENTS_DATABASE_URL')
        or f"sqlite:///{os.path.join(BASE_DIR, 'database', 'university_students.db')}",
        'affiliated_students': os.environ.get('AFFILIATED_STUDENTS_DATABASE_URL')
        or f"sqlite:///{os.path.join(BASE_DIR, 'database', 'affiliated_students.db')}",
        'affiliated_colleges': os.environ.get('AFFILIATED_COLLEGES_DATABASE_URL')
        or f"sqlite:///{os.path.join(BASE_DIR, 'database', 'affiliated_colleges.db')}",
    }
    DEBUG = os.environ.get('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes'}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    MAX_ATTENDANCE_IMPORT_BYTES = max(1024, int(os.environ.get('MAX_ATTENDANCE_IMPORT_BYTES', str(2 * 1024 * 1024))))
    MAX_ATTENDANCE_IMPORT_ROWS = max(1, int(os.environ.get('MAX_ATTENDANCE_IMPORT_ROWS', '2000')))
    ATTENDANCE_IMPORT_STAGE_TTL_MINUTES = max(1, int(os.environ.get('ATTENDANCE_IMPORT_STAGE_TTL_MINUTES', '15')))
    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    PERMANENT_SESSION_LIFETIME = timedelta(hours=max(1, int(os.environ.get('SESSION_LIFETIME_HOURS', '8'))))
    SESSION_IDLE_TIMEOUT_MINUTES = max(1, int(os.environ.get('SESSION_IDLE_TIMEOUT_MINUTES', '30')))
    PASSWORD_RESET_LIFETIME_MINUTES = max(5, int(os.environ.get('PASSWORD_RESET_LIFETIME_MINUTES', '30')))
    SESSION_REFRESH_EACH_REQUEST = True
    SECURITY_HMAC_KEY = os.environ.get('SECURITY_HMAC_KEY') or os.environ.get('SECRET_KEY')
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI') or 'memory://'
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_ENABLED = True
    SECURITY_HSTS_MAX_AGE = max(0, int(os.environ.get('SECURITY_HSTS_MAX_AGE', '31536000')))
    TRUSTED_PROXY_HOPS = max(0, int(os.environ.get('TRUSTED_PROXY_HOPS', '0')))
    SMTP_HOST = os.environ.get('SMTP_HOST', '').strip()
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '').strip()
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
    SMTP_FROM = os.environ.get('SMTP_FROM', '').strip()
    PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')
    STUDENT_EMAIL_VERIFICATION_REQUIRED = True
    STUDENT_EMAIL_VERIFICATION_LIFETIME_HOURS = 24
    SECURITY_HEADERS_ENABLED = True
    CONTENT_SECURITY_POLICY = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' https://cdn.jsdelivr.net data:; img-src 'self' data: https://images.pexels.com; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    ATTENDANCE_CORRECTION_WINDOW_HOURS = int(os.environ.get('ATTENDANCE_CORRECTION_WINDOW_HOURS', '24'))
