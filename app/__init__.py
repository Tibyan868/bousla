import os
from flask import Flask
from dotenv import load_dotenv
from .database import db, init_db

load_dotenv()

ALLOWED_IMAGE = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_AUDIO = {'mp3', 'wav', 'm4a', 'ogg'}
ALLOWED_VIDEO = {'mp4', 'mov', 'webm', 'mkv'}


# -----------------------
# Media classification helper
# -----------------------
def classify_media(file):
    """Return ('image'|'audio'|'video'|None, ext) based on mimetype or extension."""
    if not file or not file.filename:
        return None, None
    filename = file.filename
    ext = (filename.rsplit('.', 1)[-1] or '').lower()
    mt = (file.mimetype or '').lower()
    if mt.startswith('image/') or ext in ALLOWED_IMAGE:
        return 'image', ext
    if mt.startswith('audio/') or ext in ALLOWED_AUDIO:
        return 'audio', ext
    if mt.startswith('video/') or ext in ALLOWED_VIDEO:
        return 'video', ext
    return None, ext


def _database_uri(app):
    """
    Build a working SQLAlchemy URI.

    FIX 1: if DATABASE_URL is missing the app used to crash at startup
           ("Either SQLALCHEMY_DATABASE_URI or SQLALCHEMY_BINDS must be set").
           We now fall back to a local SQLite file so the site always starts.
    FIX 2: Render / Heroku / Railway give URLs that start with "postgres://".
           SQLAlchemy 1.4+ only accepts "postgresql://", so it is rewritten.
    """
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        os.makedirs(app.instance_path, exist_ok=True)
        return "sqlite:///" + os.path.join(app.instance_path, "bousala.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


# -----------------------
# Flask App Factory
# -----------------------
def create_app():
    app = Flask(__name__)

    # Secret key (set SECRET_KEY in .env for production)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-me-in-env')

    # -----------------------
    # Database configuration
    # -----------------------
    uri = _database_uri(app)
    app.config['SQLALCHEMY_DATABASE_URI'] = uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    if uri.startswith("postgresql"):
        # FIX 3: hosted Postgres drops idle connections -> "SSL connection has been closed
        # unexpectedly". pre_ping + recycle reconnects transparently.
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True, "pool_recycle": 280}

    # -----------------------
    # Media upload settings (absolute path, independent of working directory)
    # -----------------------
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB per request

    # -----------------------
    # Initialize database
    # -----------------------
    init_db(app)

    # -----------------------
    # Register blueprints
    # -----------------------
    from .routes import main
    app.register_blueprint(main)

    return app
