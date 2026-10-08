// static/js/chat.js — Bousala AI Assistant
// (The old version posted to /api/chat, which did not exist on the server, and it was not
//  loaded by any page, so the free-text chat never worked.)
(function () {
  const cfg = window.BOUSALA_CHAT || { lang: "ar", endpoint: "/api/chat" };
  const T = {
    en: {
      hello: "Hello 👋 I'm the Bousala Assistant. Ask me about verifying news, safe movement, the humanitarian situation, health and safety, or what others have shared.",
      error: "⚠️ Could not reach the server. Please try again.",
      chips: [
        "How do I verify news about a ceasefire?",
        "Is it safe to return to Khartoum?",
        "What is the humanitarian situation in Sudan now?",
        "How do I protect my family from cholera?",
        "What did people report about unsafe routes?",
        "I feel anxious because of the news"
      ]
    },
    ar: {
      hello: "أهلًا بك 👋 أنا مساعد بوصلة. اسألني عن التحقق من الأخبار، التنقل الآمن، الوضع الإنساني، الصحة والسلامة، أو ما شاركه الآخرون.",
      error: "⚠️ تعذر الاتصال بالخادم. حاول مرة أخرى.",
      chips: [
        "كيف أتحقق من خبر وقف إطلاق النار؟",
        "هل العودة إلى الخرطوم آمنة؟",
        "ما هو الوضع الإنساني في السودان الآن؟",
        "كيف أحمي أسرتي من الكوليرا؟",
        "ماذا قال الناس عن الطرق غير الآمنة؟",
        "أشعر بالقلق بسبب الأخبار"
      ]
    }
  };
  const L = T[cfg.lang] || T.ar;

  const win = document.getElementById("ai-window");
  const form = document.getElementById("ai-form");
  const input = document.getElementById("ai-text");
  const send = document.getElementById("ai-send");
  const chips = document.getElementById("ai-chips");
  const clear = document.getElementById("ai-clear");
  if (!win || !form) return;

  let history = [];
  let busy = false;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // tiny, safe markdown: **bold**, links, bullet lists, paragraphs
  function render(text) {
    const lines = esc(text).split(/\n/);
    let html = "", inList = false;
    for (let raw of lines) {
      let line = raw
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]*)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
        .replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>')
        .replace(/(^|\s)(\/(?:safe_routes|advices|food_supply|decision|mental_aspect|old|misinfo_type|statistics|post|posts|guidebot|about))\b/g,
                 '$1<a href="$2?lang=' + cfg.lang + '">$2</a>');
      const m = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (m) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += "<li>" + m[1] + "</li>";
      } else {
        if (inList) { html += "</ul>"; inList = false; }
        if (line.trim()) html += "<p>" + line.replace(/^#+\s*/, "") + "</p>";
      }
    }
    if (inList) html += "</ul>";
    return html;
  }

  function add(text, who, sources) {
    const wrap = document.createElement("div");
    wrap.className = "msg " + who;
    const b = document.createElement("div");
    b.className = "bubble";
    if (who === "user") b.textContent = text; else b.innerHTML = render(text);
    wrap.appendChild(b);
    if (sources && sources.length) {
      const s = document.createElement("div");
      s.className = "sources";
      sources.forEach((src) => {
        const label = (src.title || src.source || "").split("|")[cfg.lang === "ar" ? 1 : 0] || src.title;
        const el = document.createElement(src.url ? "a" : "span");
        el.className = "src";
        el.textContent = "📎 " + (label || "").trim().slice(0, 60);
        if (src.url) { el.href = src.url + (src.url.includes("?") ? "&" : "?") + "lang=" + cfg.lang; el.target = "_blank"; }
        s.appendChild(el);
      });
      wrap.appendChild(s);
    }
    win.appendChild(wrap);
    win.scrollTop = win.scrollHeight;
  }

  function typing() {
    const w = document.createElement("div");
    w.className = "msg bot";
    w.innerHTML = '<div class="bubble typing"><span></span><span></span><span></span></div>';
    win.appendChild(w);
    win.scrollTop = win.scrollHeight;
    return w;
  }

  function showChips() {
    chips.innerHTML = "";
    L.chips.forEach((q) => {
      const c = document.createElement("button");
      c.type = "button";
      c.className = "chip";
      c.textContent = q;
      c.onclick = () => ask(q);
      chips.appendChild(c);
    });
  }

  async function ask(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; send.disabled = true;
    chips.innerHTML = "";
    add(text, "user");
    input.value = ""; autosize();
    const t = typing();
    try {
      const res = await fetch(cfg.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, lang: cfg.lang, history: history.slice(-8) })
      });
      const data = await res.json().catch(() => ({}));
      t.remove();
      if (data.reply) {
        add(data.reply, "bot", data.sources);
        history.push({ role: "user", content: text }, { role: "assistant", content: data.reply });
      } else {
        add("⚠️ " + (data.error || L.error), "bot");
      }
    } catch (e) {
      t.remove();
      add(L.error, "bot");
      console.error(e);
    } finally {
      busy = false; send.disabled = false; input.focus();
    }
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }

  function reset() {
    history = [];
    win.innerHTML = "";
    add(L.hello, "bot");
    showChips();
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); }
  });
  input.addEventListener("input", autosize);
  clear.addEventListener("click", reset);

  reset();
})();
