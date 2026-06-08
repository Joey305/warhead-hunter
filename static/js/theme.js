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

  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    const isActive = button.getAttribute("data-theme-choice") === normalizedTheme;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
    button.dataset.active = isActive ? "true" : "false";
  });
}

function initWarheadHunterThemeToggle() {
  applyWarheadHunterTheme(getStoredWarheadHunterTheme(), false);

  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      applyWarheadHunterTheme(button.getAttribute("data-theme-choice"), true);
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initWarheadHunterThemeToggle, { once: true });
} else {
  initWarheadHunterThemeToggle();
}
