"""
Bousala AI Assistant
====================
Free-text chat that answers from:
  1. knowledge_base/*.txt|md            (war context, humanitarian figures, safety, verification)
  2. the website's own pages            (templates: advices, safe routes, food supply, ...)
  3. app/data/decision_tree.json        (GuideBot content)
  4. community stories from the DB       (posts table, refreshed every few minutes)

Retrieval is a small pure-python BM25 index that understands Arabic and English
(no FAISS / embeddings model needed).

Answer generation:
  * If LLM_API_KEY is set in .env -> the retrieved passages are sent to an LLM
    (Anthropic Claude, OpenAI, Google Gemini, Groq or OpenRouter — see LLM_PROVIDER).
  * If no key is set, or the API fails -> an offline answer is built directly from
    the best matching passages, so the chat never breaks.
"""
import os
import re
import json
import math
import time
import glob
import threading
from collections import Counter, defaultdict

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(BASE_DIR, "app")
KB_DIR = os.path.join(BASE_DIR, "knowledge_base")
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
TREE_PATH = os.path.join(APP_DIR, "data", "decision_tree.json")

# Website pages that hold real content (title_en, title_ar, url)
SITE_PAGES = {
    "home_fully.html": ("Bousala home – what is misinformation", "الصفحة الرئيسية – ما هي المعلومة المضللة", "/home_fully"),
    "about.html": ("About Bousala", "حول بوصلة", "/about"),
    "advices.html": ("Safety & awareness tips", "نصائح للسلامة والوعي", "/advices"),
    "safe_routes.html": ("Safe routes", "الطرق الآمنة", "/safe_routes"),
    "food_supply.html": ("Food / supply distribution", "مواقع توزيع الغذاء والإمدادات", "/food_supply"),
    "decision.html": ("Decision-making during the war", "اتخاذ القرار أثناء الحرب", "/decision"),
    "mental_aspect.html": ("Psychological aspect", "الجانب النفسي", "/mental_aspect"),
    "old.html": ("Children and the elderly", "الأطفال وكبار السن", "/old"),
    "misinfo_type.html": ("Types of misinformation", "أنواع المعلومات المضللة", "/misinfo_type"),
    "statistics.html": ("Survey statistics", "إحصائيات الاستبيان", "/statistics"),
}

# ---------------------------------------------------------------------------
# Text normalisation (Arabic + English)
# ---------------------------------------------------------------------------
_AR_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_TOKEN = re.compile(r"[a-z0-9]+|[ء-ي]+")
_STOP = set("""
the a an and or of to in on for is are was were be been it this that with as at by from
what how who where when which why do does did can i you we they he she my your our me
about there their have has had not no yes will would should could please tell
في من على الى إلى عن مع هل ما ماذا كيف اين أين متى هذا هذه ذلك التي الذي هو هي انا أنا
نحن انت أنت كان كانت او أو ثم لا نعم قد لم لن يا كل بعد قبل عند لي لك
""".split())


def normalize(text):
    text = (text or "").lower()
    text = _AR_DIACRITICS.sub("", text)
    text = re.sub("[إأآا]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return text


def _stem_ar(tok):
    for p in ("وال", "بال", "كال", "فال", "لل", "ال"):
        if tok.startswith(p) and len(tok) - len(p) >= 2:
            tok = tok[len(p):]
            break
    for s in ("ات", "ون", "ين", "ها", "ه"):
        if tok.endswith(s) and len(tok) - len(s) >= 3:
            tok = tok[: -len(s)]
            break
    return tok


def _stem_en(tok):
    for s in ("ing", "ed", "es", "s"):
        if tok.endswith(s) and len(tok) - len(s) >= 3:
            return tok[: -len(s)]
    return tok


def tokenize(text):
    out = []
    for t in _TOKEN.findall(normalize(text)):
        if t in _STOP or len(t) < 2:
            continue
        out.append(_stem_ar(t) if "ء" <= t[0] <= "ي" else _stem_en(t))
    return out


def is_arabic(text):
    return len(re.findall(r"[؀-ۿ]", text or "")) > len(re.findall(r"[A-Za-z]", text or ""))


# ---------------------------------------------------------------------------
# Document loading
# ---------------------------------------------------------------------------
def _html_to_text(html):
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I)
    html = re.sub(r"{#[\s\S]*?#}|{%[\s\S]*?%}|{{[\s\S]*?}}", " ", html)
    html = re.sub(r"<br\s*/?>|</(p|li|h[1-6]|div|tr)>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in html.splitlines()]
    return "\n".join(l for l in lines if l)


def _chunk(text, size=700, overlap=120):
    """Split on paragraphs/lines, pack into ~size-char chunks."""
    parts = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#)|\n(?=- )", text) if p.strip()]
    chunks, cur = [], ""
    for p in parts:
        if len(cur) + len(p) + 1 <= size:
            cur = (cur + "\n" + p).strip()
        else:
            if cur:
                chunks.append(cur)
            while len(p) > size:
                chunks.append(p[:size])
                p = p[size - overlap:]
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def _load_static_docs():
    docs = []
    # 1) knowledge base files
    for path in sorted(glob.glob(os.path.join(KB_DIR, "*"))):
        if not path.endswith((".txt", ".md")):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        title = os.path.splitext(os.path.basename(path))[0].split("_", 1)[-1].replace("_", " ").title()
        # keep section headings with each chunk for context
        heading = title
        for chunk in _chunk(text):
            m = re.match(r"#+\s*(.+)", chunk)
            if m:
                heading = m.group(1).strip()
            docs.append({"title": heading, "source": "Knowledge base: " + title, "url": None, "text": chunk})

    # 2) website pages
    for fname, (t_en, t_ar, url) in SITE_PAGES.items():
        path = os.path.join(TEMPLATES_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = _html_to_text(f.read())
        for chunk in _chunk(text):
            docs.append({"title": f"{t_en} | {t_ar}", "source": "Bousala page", "url": url, "text": chunk})

    # 3) GuideBot decision tree
    if os.path.exists(TREE_PATH):
        try:
            with open(TREE_PATH, encoding="utf-8") as f:
                tree = json.load(f)
            for node_id, node in tree.items():
                strings = []

                def walk(x):
                    if isinstance(x, str):
                        strings.append(x)
                    elif isinstance(x, dict):
                        for v in x.values():
                            walk(v)
                    elif isinstance(x, list):
                        for v in x:
                            walk(v)
                walk(node)
                text = "\n".join(s for s in strings if len(s) > 3 and not s.startswith(("/", "http")))
                if text:
                    docs.append({"title": "GuideBot: " + node_id, "source": "GuideBot", "url": "/guidebot", "text": text[:1500]})
        except Exception:
            pass
    return docs


# ---------------------------------------------------------------------------
# BM25 index
# ---------------------------------------------------------------------------
class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self.tf = []
        self.df = defaultdict(int)
        for d in docs:
            toks = tokenize(d["title"]) * 2 + tokenize(d["text"])   # title words count double
            c = Counter(toks)
            self.tf.append((c, len(toks)))
            for t in c:
                self.df[t] += 1
        self.N = len(docs) or 1
        self.avgdl = (sum(l for _, l in self.tf) / self.N) if self.tf else 1

    def search(self, query, k=5):
        q = tokenize(query)
        if not q:
            return []
        scores = []
        for i, (c, dl) in enumerate(self.tf):
            s = 0.0
            for t in q:
                f = c.get(t)
                if not f:
                    continue
                idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [(s, self.docs[i]) for s, i in scores[:k]]


_lock = threading.Lock()
_state = {"index": None, "built": 0, "static": None}
REFRESH_SECONDS = 300


def _story_docs():
    """Community stories from the database (latest 300)."""
    try:
        from .database import posts
        rows = posts.query.order_by(posts.created_at.desc()).limit(300).all()
    except Exception:
        return []
    docs = []
    for p in rows:
        meta = " | ".join(x for x in [p.misinfo_type, p.followup, p.state, p.locality, p.time,
                                       f"danger: {p.danger_level}"] if x)
        docs.append({
            "title": f"Community story #{p.id} ({p.state or ''})",
            "source": "Community story",
            "url": f"/posts?type={p.misinfo_type}" if p.misinfo_type else "/posts",
            "text": f"{meta}\n{(p.content or '')[:1200]}",
        })
    return docs


def get_index(force=False):
    with _lock:
        now = time.time()
        if force or _state["index"] is None or now - _state["built"] > REFRESH_SECONDS:
            if _state["static"] is None or force:
                _state["static"] = _load_static_docs()
            _state["index"] = BM25(_state["static"] + _story_docs())
            _state["built"] = now
        return _state["index"]


def refresh_index():
    """Call after a story is posted / edited / deleted."""
    _state["built"] = 0


# ---------------------------------------------------------------------------
# LLM providers (key comes ONLY from environment / .env)
# ---------------------------------------------------------------------------
PROVIDERS = {
    #  name        endpoint                                                            default model
    "anthropic": ("https://api.anthropic.com/v1/messages", "claude-haiku-4-5"),
    "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-flash"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "meta-llama/llama-3.3-70b-instruct"),
}

SYSTEM_PROMPT = """You are "Bousala Assistant" (مساعد بوصلة), the AI helper of Bousala — a website that helps civilians in Sudan deal with misinformation during the war between the Sudanese Armed Forces (SAF) and the Rapid Support Forces (RSF), share their stories anonymously, and make safer decisions.

Rules:
- Answer in the SAME language as the user's question (Sudanese-friendly Modern Standard Arabic for Arabic). Be warm, calm, short and practical (max ~200 words unless asked for more). Use simple bullet points when listing steps.
- Base your answer on the CONTEXT passages. You may add widely known general knowledge, but never invent phone numbers, places, dates, figures or claims about who controls an area. If the context doesn't contain it, say you don't know and point to trusted sources (UN OCHA, WHO, UNICEF, Sudanese Red Crescent, ICRC, Reuters/BBC fact-check).
- War news changes daily. When you mention figures or the military situation, say the date of the information and advise checking a trusted source before acting.
- Always encourage verifying rumours (ceasefires, "safe routes", aid distributions, a party "entering" a town) with at least two trusted sources before moving.
- Be strictly neutral between the warring parties. Do not give military, tactical or weapons information, and do not help locate or target people.
- If someone is in immediate danger or mentions self-harm, give calm safety steps first and encourage contacting local emergency help or the support numbers in the context.
- When useful, point to the relevant Bousala page (e.g. /safe_routes, /advices, /food_supply, /mental_aspect, /post to share a story, /guidebot).
- Community stories are personal experiences from users, not verified facts — present them as "people reported…".
"""


def _llm_config():
    key = (os.getenv("LLM_API_KEY") or "").strip()
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not provider and key:
        # guess from key prefix
        if key.startswith("sk-ant-"):
            provider = "anthropic"
        elif key.startswith("gsk_"):
            provider = "groq"
        elif key.startswith("AIza"):
            provider = "gemini"
        elif key.startswith("sk-or-"):
            provider = "openrouter"
        else:
            provider = "openai"
    if not key or provider not in PROVIDERS:
        return None
    url, default_model = PROVIDERS[provider]
    return {"provider": provider, "key": key, "url": url,
            "model": (os.getenv("LLM_MODEL") or default_model).strip()}


def llm_enabled():
    return _llm_config() is not None


def _call_llm(cfg, system, messages, timeout=40):
    if cfg["provider"] == "anthropic":
        r = requests.post(cfg["url"], timeout=timeout, headers={
            "x-api-key": cfg["key"], "anthropic-version": "2023-06-01", "content-type": "application/json",
        }, json={"model": cfg["model"], "max_tokens": 800, "system": system, "messages": messages})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()

    r = requests.post(cfg["url"], timeout=timeout, headers={
        "Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json",
    }, json={"model": cfg["model"], "max_tokens": 800, "temperature": 0.3,
             "messages": [{"role": "system", "content": system}] + messages})
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


# ---------------------------------------------------------------------------
# Offline answers (no API key / API down)
# ---------------------------------------------------------------------------
_EMERGENCY = re.compile(r"(انتحار|اقتل نفسي|اموت|أموت|ايذاء نفسي|إيذاء نفسي|suicide|kill myself|self.?harm|"
                        r"نزيف|bleeding|injured|قصف الان|قصف الآن|under attack|trapped|محاصر)", re.I)

_GREETING = re.compile(r"^\s*(hi|hello|hey|salam|السلام عليكم|سلام|مرحبا|اهلا|أهلا|هلا)\b", re.I)


def _offline_answer(question, hits, ar):
    if _GREETING.search(question) and len(question) < 30:
        return ("أهلًا بك 👋 أنا مساعد بوصلة. اسألني عن التحقق من الأخبار، الطرق الآمنة، الوضع الإنساني، "
                "الصحة والسلامة، أو قصص المجتمع." if ar else
                "Hello 👋 I'm the Bousala assistant. Ask me about verifying news, safe routes, the humanitarian "
                "situation, health and safety, or community stories.")
    if not hits:
        return ("لم أجد معلومة مؤكدة عن هذا في قاعدة معرفة بوصلة. ننصحك بمراجعة مصادر موثوقة مثل أوتشا السودان "
                "(reports.unocha.org/sudan) أو منظمة الصحة العالمية أو الهلال الأحمر السوداني، وعدم اتخاذ قرار "
                "خطير قبل التأكد من مصدرين." if ar else
                "I couldn't find verified information about this in Bousala's knowledge base. Please check trusted "
                "sources such as UN OCHA Sudan (reports.unocha.org/sudan), WHO or the Sudanese Red Crescent, and "
                "don't take risky decisions before confirming with two sources.")
    intro = "إليك ما وجدته في قاعدة معرفة بوصلة:" if ar else "Here is what I found in Bousala's knowledge base:"
    # pick the most relevant LINES (not whole chunks) from the best passages, in the user's language
    q = set(tokenize(question))
    scored, seen = [], set()
    for rank, (_, d) in enumerate(hits[:5]):
        for line in d["text"].splitlines():
            line = line.strip().lstrip("-•* ").strip()
            if len(line) < 25 or line.startswith("#") or is_arabic(line) != ar:
                continue
            key = normalize(line)[:80]
            if key in seen:
                continue
            seen.add(key)
            overlap = len(q & set(tokenize(line)))
            if overlap:
                scored.append((overlap - rank * 0.3, rank, line))
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked = [l for *_, l in scored[:5]]
    if not picked:  # fall back to the start of the best passage
        picked = [l.strip("-•* ") for l in hits[0][1]["text"].splitlines() if len(l.strip()) > 25][:4]
    parts = [intro, "\n".join("• " + (l if len(l) < 400 else l[:400] + "…") for l in picked)]
    parts.append("⚠️ تحقق دائمًا من مصدرين موثوقين قبل اتخاذ أي قرار." if ar
                 else "⚠️ Always confirm with two trusted sources before acting.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def answer(question, history=None, lang=None):
    question = (question or "").strip()[:1500]
    ar = is_arabic(question) if re.search(r"[A-Za-z؀-ۿ]", question) else (lang == "ar")

    # search with the question + last user turn for follow-ups ("and in Darfur?")
    search_q = question
    for h in reversed(history or []):
        if h.get("role") == "user":
            search_q = h.get("content", "")[:300] + " " + question
            break
    hits = get_index().search(search_q, k=6)
    good = [h for h in hits if h[0] >= 1.0]

    sources, seen = [], set()
    for _, d in good[:4]:
        key = (d["source"], d["title"])
        if key in seen:
            continue
        seen.add(key)
        sources.append({"title": d["title"], "source": d["source"], "url": d["url"]})

    cfg = _llm_config()
    if cfg:
        context = "\n\n".join(
            f"[{i+1}] ({d['source']} — {d['title']}{' — ' + d['url'] if d['url'] else ''})\n{d['text']}"
            for i, (_, d) in enumerate(good[:6])
        ) or "(no matching passages)"
        system = SYSTEM_PROMPT + f"\nToday's date: {time.strftime('%Y-%m-%d')}.\n\nCONTEXT:\n{context}"
        msgs = []
        for h in (history or [])[-6:]:
            role = "assistant" if h.get("role") == "assistant" else "user"
            content = str(h.get("content", ""))[:1500]
            if content:
                if msgs and msgs[-1]["role"] == role:
                    msgs[-1]["content"] += "\n" + content
                else:
                    msgs.append({"role": role, "content": content})
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] += "\n" + question
        else:
            msgs.append({"role": "user", "content": question})
        try:
            reply = _call_llm(cfg, system, msgs)
            if reply:
                return {"reply": reply, "sources": sources, "mode": "llm"}
        except Exception as e:  # fall back to offline answer
            print(f"[assistant] LLM error ({cfg['provider']}): {e}")

    reply = _offline_answer(question, good, ar)
    if _EMERGENCY.search(question):
        reply = (("🚨 إذا كنت في خطر الآن: ابتعد عن مكان الخطر، اطلب المساعدة من أقرب شخص أو مرفق صحي، "
                  "وللدعم النفسي عن بعد: طمأنينة ‎+249 90 660 7664.\n\n") if ar else
                 ("🚨 If you are in danger right now: move away from the danger, ask the nearest person or health "
                  "facility for help. Remote psychological support: Tamaneenah +249 90 660 7664.\n\n")) + reply
    return {"reply": reply, "sources": sources, "mode": "offline"}
