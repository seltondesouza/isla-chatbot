// Apply a saved light/dark choice before the page paints (no flash).
(function () {
  try {
    var t = localStorage.getItem("isla-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage blocked: follow the system theme */ }
})();
