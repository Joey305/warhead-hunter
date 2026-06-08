const WARHEAD_HUNTER_THEME_KEY = "warhead-hunter-theme";
const WARHEAD_HUNTER_THEMES = new Set(["light", "dark"]);

function normalizeWarheadHunterTheme(theme) {
  return WARHEAD_HUNTER_THEMES.has(theme) ? theme : "light";
}

function getStoredWarheadHunterTheme() {
  try {
    return normalizeWarheadHunterTheme(localStorage.getItem(WARHEAD_HUNTER_THEME_KEY));
  } catch (error) {
    return "light";
  }
}

function applyWarheadHunterTheme(theme, persist) {
  const normalizedTheme = normalizeWarheadHunterTheme(theme);
  document.documentElement.dataset.theme = normalizedTheme;

  if (persist) {
    try {
      localStorage.setItem(WARHEAD_HUNTER_THEME_KEY, normalizedTheme);
    } catch (error) {
      // Ignore storage failures and keep the current theme applied in memory.
    }
  }

  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    const nextTheme = normalizedTheme === "dark" ? "light" : "dark";
    const nextEmoji = nextTheme === "dark" ? "🌙" : "☀️";
    const nextLabel = nextTheme === "dark" ? "Switch to dark mode" : "Switch to light mode";
    button.textContent = nextEmoji;
    button.setAttribute("aria-label", nextLabel);
    button.setAttribute("aria-pressed", normalizedTheme === "dark" ? "true" : "false");
    button.dataset.theme = normalizedTheme;
  });
}

function initWarheadHunterThemeToggle() {
  applyWarheadHunterTheme(getStoredWarheadHunterTheme(), false);

  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const currentTheme = normalizeWarheadHunterTheme(document.documentElement.dataset.theme);
      const nextTheme = currentTheme === "dark" ? "light" : "dark";
      applyWarheadHunterTheme(nextTheme, true);
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initWarheadHunterThemeToggle, { once: true });
} else {
  initWarheadHunterThemeToggle();
}
