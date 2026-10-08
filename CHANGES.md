# Bousala — fixes and new AI Assistant (October 2026)

## Setup
1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in: SECRET_KEY, DATABASE_URL, ADMIN_PASSWORD, LLM_API_KEY
3. `python run.py`, then open http://localhost:5000

## Database fixes
- The app crashed at startup when DATABASE_URL was missing. It now falls back to SQLite (instance/bousala.db).
- `postgres://` URLs (Render/Heroku) are converted to `postgresql://`, which SQLAlchemy 2 requires.
- Added pool_pre_ping/pool_recycle so hosted Postgres doesn't fail with "SSL connection closed".
- Uploads were saved relative to the working folder, and files with the same name overwrote each other. They now use an absolute folder and unique names, and only image/audio/video files are accepted.
- GuideBot's "See others' stories" was always empty: it searched for 'safe_routes'/'hospitals'/... but posts are stored with the Arabic category names. Added a mapping.
- Editing a story to empty text crashed (NOT NULL). It is now validated.
- Deleting a user left notifications behind. Added the cascade.
- New table `chat_message` stores questions asked to the AI assistant.
- `import faiss` (unused) crashed the app wherever faiss-cpu didn't install. Removed it.
- Flask 2.3.2 breaks with Werkzeug 3.1. Pinned Werkzeug==2.3.8.

## Chat fixes
- The free-text chat (static/js/chat.js) posted to /api/chat, but that route didn't exist and no page loaded the script. It has been rebuilt as the new AI Assistant (/assistant).
- GuideBot: stories written by users were inserted as raw HTML (stored XSS). They are now escaped.
- GuideBot: hospital numbers were placeholders (+249 912 345 678...). They are replaced with verification guidance.
- GuideBot has a new menu button, "Ask the AI Assistant".
- Admin username/password were hard-coded in routes.py. They are now read from .env.
- No API key existed anywhere in the project. The assistant reads LLM_API_KEY from .env only.

## AI Assistant (/assistant, link in navbar)
- Knowledge: knowledge_base/*.md|txt + the site's pages + decision_tree.json + community stories from the DB (refreshed automatically).
- New war data: 6_sudan_war_timeline_2023_2026, 7_humanitarian_figures_2026, 8_civilian_safety_and_health, 9_verifying_war_rumours, 10_bousala_site_guide (Arabic + English). Update these files to keep the bot current.
- Providers: anthropic | openai | gemini | groq | openrouter (auto-detected from the key).
- With no key, or when the API fails, it answers in offline mode from the knowledge base.
- knowledge.index / metadata.jsonl (old FAISS files) are no longer used and can be deleted.
