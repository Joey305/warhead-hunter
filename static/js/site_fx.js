document.documentElement.classList.add("fx-enabled");

function markVisible(el) {
  el.classList.add("is-visible");
}

function shouldRevealImmediately(el) {
  const rect = el.getBoundingClientRect();
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
  return rect.top <= viewportHeight * 0.96 || rect.height <= viewportHeight * 0.9;
}

function initRevealFx() {
  const revealEls = Array.from(document.querySelectorAll(".reveal"));

  if (revealEls.length === 0) {
    return;
  }

  if (!("IntersectionObserver" in window)) {
    revealEls.forEach(markVisible);
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting || entry.intersectionRatio > 0) {
          markVisible(entry.target);
          observer.unobserve(entry.target);
        }
      });
    },
    {
      rootMargin: "0px 0px -6% 0px",
      threshold: 0.01,
    }
  );

  revealEls.forEach((el) => {
    if (shouldRevealImmediately(el)) {
      markVisible(el);
      return;
    }
    observer.observe(el);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initRevealFx, { once: true });
} else {
  initRevealFx();
}
