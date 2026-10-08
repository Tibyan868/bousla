from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import CheckConstraint

db = SQLAlchemy()

# -----------------------
# Database Tables


class users(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    email = db.Column(db.String(120), unique=True)
    password = db.Column(db.String(255), nullable=False)
    is_guest = db.Column(db.Boolean, nullable=False, default=False)
    age_group = db.Column(db.String(20))
    gender = db.Column(db.String(15))

    
    posts = db.relationship('posts', back_populates='user', cascade="all, delete-orphan")
    # FIX: without this, deleting a user left orphan notifications (FK violation on Postgres)
    notifications = db.relationship('notification', cascade="all, delete-orphan", passive_deletes=True)


class posts(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    time = db.Column(db.String(100), nullable=False)
    
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)

    
    user = db.relationship('users', back_populates='posts')

    state = db.Column(db.String(80), nullable=False)
    locality = db.Column(db.String(80))
    misinfo_type = db.Column(db.String(120), nullable=False, index=True)
    followup = db.Column(db.String(255))
    decision = db.Column(db.Boolean, default=False, nullable=False)
    danger_level = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)

    __table_args__ = (
        CheckConstraint("danger_level IN ('High','Medium','Low')", name='check_danger_level'),
    )

 
    media_items = db.relationship('media', back_populates='post', cascade="all, delete-orphan")


class media(db.Model):
    __tablename__ = 'media'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)

   
    post = db.relationship('posts', back_populates='media_items')


class notification(db.Model):
    __tablename__ = 'notification'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)



class chat_message(db.Model):
    """Questions asked to the AI assistant (lets the admin see what people need)."""
    __tablename__ = 'chat_message'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    lang = db.Column(db.String(5), default='ar')
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text)
    source = db.Column(db.String(20))   # 'llm' or 'offline'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def init_db(app):
    """Initialize the database (PostgreSQL or SQLite fallback) with the Flask app."""
    db.init_app(app)
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:  # make connection problems obvious in the logs
            app.logger.error("Database connection failed for %s : %s",
                             app.config.get('SQLALCHEMY_DATABASE_URI', '').split('@')[-1], e)
            raise
