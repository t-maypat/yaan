// Light by default; a saved choice (or ?theme=) wins. Loaded before first paint, so no flash.
// A file, not an inline script: the API's Content-Security-Policy allows scripts from the
// app's own origin only (D111).
(function () {
  var t = null;
  try { t = new URLSearchParams(location.search).get("theme") || localStorage.getItem("yaan-theme") || localStorage.getItem("lore-theme"); } catch (e) {}
  document.documentElement.dataset.theme = t === "dark" ? "dark" : "light";
})();
