// -- THEME --
const html   = document.documentElement;
const toggle = document.getElementById("themeToggle");

function applyTheme(theme) {
  html.setAttribute("data-theme", theme);
  if (toggle) toggle.innerHTML = theme === "dark" ? "&#9728;" : "&#9790;";
  localStorage.setItem("plain-theme", theme);
}

// Default to light — user can toggle to dark
applyTheme(localStorage.getItem("plain-theme") || "light");

if (toggle) {
  toggle.addEventListener("click", () => {
    applyTheme(html.getAttribute("data-theme") === "dark" ? "light" : "dark");
  });
}

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
    expand.classList.remove("open");
    if (summary) summary.style.display = "";
    if (foot)    foot.style.display    = "";
    if (btn)     btn.innerHTML = "Continue reading &darr;";
  } else {
    expand.classList.add("open");
    if (summary) summary.style.display = "none";
    if (foot)    foot.style.display    = "none";
    if (btn)     btn.innerHTML = "Close &uarr;";
    setTimeout(() => expand.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
  }
}

function collapseThis(collapseBtn) {
  const container = collapseBtn.closest(".hero, .article-card");
  const expand    = container.querySelector(".article-expand");
  const summary   = container.querySelector(".hero-summary, .card-summary");
  const foot      = container.querySelector(".hero-foot, .card-foot");
  const btn       = container.querySelector(".expand-btn");

  expand.classList.remove("open");
  if (summary) summary.style.display = "";
  if (foot)    foot.style.display    = "";
  if (btn)     btn.innerHTML = "Continue reading &darr;";
}

// Make entire card or hero clickable to toggle expand/collapse
document.addEventListener("click", e => {
  const container = e.target.closest(".article-card, .hero");
  if (!container) return;
  if (container.classList.contains("support-card")) return;
  if (e.target.closest(".collapse-btn")) return;
  if (e.target.closest(".expand-btn")) return;
  expandContainer(container);
});

// -- CATEGORY FILTER --
document.querySelectorAll(".cat-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    try {
      document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const cat = btn.dataset.cat;

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
  document.querySelectorAll(".article-card").forEach(card => {
    if (card.classList.contains("support-card")) return;
    card.style.display = card.dataset.topnews === "true" ? "block" : "none";
  });
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
