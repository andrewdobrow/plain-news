// -- EXPAND / COLLAPSE --
function toggleExpand(btn) {
  const container = btn.closest(".hero, .article-card");
  expandContainer(container);
}

function expandContainer(container) {
  const expand  = container.querySelector(".article-expand");
  if (!expand) return;
  const summary = container.querySelector(".hero-summary, .card-summary");
  const foot    = container.querySelector(".hero-foot, .card-foot");
  const btn     = container.querySelector(".expand-btn");
  const isOpen  = expand.classList.contains("open");

  if (isOpen) {
    collapseContainer(container);
  } else {
    // Store exactly where the top of this container is right now, before any
    // content opens. We'll scroll back here on close.
    container.dataset.scrollTarget = window.pageYOffset + container.getBoundingClientRect().top - 70;

    expand.classList.add("open");
    if (summary) summary.style.display = "none";
    if (foot)    foot.style.display    = "none";
    if (btn)     btn.innerHTML = "Close &uarr;";
    setTimeout(() => expand.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
  }
}

function collapseContainer(container) {
  const expand  = container.querySelector(".article-expand");
  const summary = container.querySelector(".hero-summary, .card-summary");
  const foot    = container.querySelector(".hero-foot, .card-foot");
  const btn     = container.querySelector(".expand-btn");

  expand.classList.remove("open");
  if (summary) summary.style.display = "";
  if (foot)    foot.style.display    = "";
  if (btn)     btn.innerHTML = "Continue reading &darr;";

  // Scroll back to exactly where the user was when they opened this article
  const target = parseFloat(container.dataset.scrollTarget);
  if (!isNaN(target)) {
    requestAnimationFrame(() => {
      window.scrollTo({ top: Math.max(0, target), behavior: "auto" });
    });
  }
}

function collapseThis(collapseBtn) {
  collapseContainer(collapseBtn.closest(".hero, .article-card"));
}

// Make entire card or hero clickable to toggle — but ignore clicks on any button or link
document.addEventListener("click", e => {
  // If the click landed on (or inside) any interactive control, let that control
  // handle it and do NOT also toggle the container. This prevents the open-then-
  // instantly-close double fire.
  if (e.target.closest("button, a")) return;

  const container = e.target.closest(".article-card, .hero");
  if (!container) return;
  if (container.classList.contains("support-card")) return;
  expandContainer(container);
});

// -- CATEGORY FILTER --
document.querySelectorAll(".cat-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    try {
      document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const cat = btn.dataset.cat;

      // Update browser tab title when switching categories
      const titleMap = {
        "all":           "Plain | News without the noise",
        "world":         "World News — Plain",
        "us":            "U.S. News — Plain",
        "politics":      "Politics — Plain",
        "business":      "Business — Plain",
        "tech":          "Tech & Science — Plain",
        "sports":        "Sports — Plain",
        "entertainment": "Entertainment — Plain",
      };
      if (titleMap[cat]) document.title = titleMap[cat];

      // Persist the active category in the URL so a refresh stays on this section
      // instead of resetting to Top News.
      try {
        const newUrl = cat && cat !== "all"
          ? `${window.location.pathname}?cat=${cat}`
          : window.location.pathname;
        history.replaceState(null, "", newUrl);
      } catch (e) {}

      // Switch hero sections
      document.querySelectorAll("[data-cat-hero]").forEach(hero => {
        hero.style.display = hero.dataset.catHero === cat ? "block" : "none";
      });

      // Scroll to top
      window.scrollTo({ top: 0, behavior: "smooth" });

      // Filter article cards (skip support card)
      document.querySelectorAll(".article-card").forEach(card => {
        if (card.classList.contains("support-card")) return;
        let show;
        if (cat === "all") {
          // Top News: only show the deduped front page set
          show = card.dataset.topnews === "true";
        } else {
          // Category view: show all cards for this category, minus the category hero
          // (the hero is already displayed in the hero section above)
          const matchesCat  = card.dataset.cat === cat;
          const isHeroInCat = card.dataset.isHero === "true" && card.dataset.cat === cat;
          show = matchesCat && !isHeroInCat;
        }
        card.style.display = show ? "block" : "none";
      });

      // Reposition support card to 3rd visible slot
      const grid        = document.getElementById("articlesGrid");
      const supportCard = grid ? grid.querySelector(".support-card") : null;
      if (supportCard && grid) {
        const visible = Array.from(grid.querySelectorAll(".article-card:not(.support-card)"))
          .filter(c => c.style.display !== "none");
        const insertAfter = visible.length >= 2 ? visible[1] : visible[visible.length - 1];
        if (insertAfter) insertAfter.insertAdjacentElement("afterend", supportCard);
        supportCard.style.display = "block";
      }
    } catch(e) {
      console.error("Category filter error:", e);
    }
  });
});

// -- INITIAL STATE: show only Top News (deduped) cards on load --
document.addEventListener("DOMContentLoaded", () => {
  // Default: show only top news cards
  document.querySelectorAll(".article-card").forEach(card => {
    if (card.classList.contains("support-card")) return;
    card.style.display = card.dataset.topnews === "true" ? "block" : "none";
  });

  // If arriving from another page with ?cat= query param, auto-activate that category
  const params = new URLSearchParams(window.location.search);
  const catParam = params.get("cat");
  if (catParam) {
    const btn = document.querySelector(`.cat-btn[data-cat="${catParam}"]`);
    if (btn) btn.click();
  }
});

// -- COUNTDOWN --
function updateCountdown() {
  const now = new Date(), next = new Date(now);
  next.setHours(now.getHours() + 1, 0, 0, 0);
  const el = document.getElementById("countdown");
  if (el) el.textContent = Math.floor((next - now) / 60000) + " min";
}
updateCountdown();
setInterval(updateCountdown, 60000);

// -- SHARE --
function shareArticle(btn) {
  const headline  = btn.getAttribute("data-headline") || document.title;
  const url       = btn.getAttribute("data-url") || window.location.origin + "/";
  const siteName  = document.querySelector(".wordmark")?.textContent?.trim() || "Plain";
  const shareText = headline + " — read more on " + siteName;

  if (navigator.share) {
    navigator.share({ title: headline, text: shareText, url: url }).catch(() => {});
    return;
  }

  const clipText = shareText + "\n" + url;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(clipText).then(() => {
      const original = btn.innerHTML;
      btn.innerHTML = "Copied &#10003;";
      setTimeout(() => { btn.innerHTML = original; }, 1800);
    }).catch(() => {});
  }
}

// -- THEME TOGGLE --
(function() {
  const root    = document.documentElement;
  const btn     = document.getElementById("themeToggle");
  const STORAGE = "plain-theme";

  function applyTheme(theme) {
    if (theme === "dark") {
      root.setAttribute("data-theme", "dark");
      if (btn) btn.innerHTML = "&#9788;";
    } else if (theme === "light") {
      root.setAttribute("data-theme", "light");
      if (btn) btn.innerHTML = "&#9790;";
    } else {
      root.removeAttribute("data-theme");
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      if (btn) btn.innerHTML = prefersDark ? "&#9788;" : "&#9790;";
    }
  }

  // Apply saved preference on load
  const saved = localStorage.getItem(STORAGE);
  applyTheme(saved || "auto");

  if (btn) {
    btn.addEventListener("click", () => {
      const current = localStorage.getItem(STORAGE) || "auto";
      const next = current === "dark" ? "light" : "dark";
      localStorage.setItem(STORAGE, next);
      applyTheme(next);
    });
  }
})();
