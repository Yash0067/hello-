// ============================================================
// Adarsh Boutique - configuration
// ------------------------------------------------------------
// appointmentEndpoint : where the PUBLIC booking form sends
//   requests (Google Apps Script writing to a private Sheet).
// apiBase : where the ADMIN dashboard (login.html/admin.html)
//   finds the Python backend.
//   * "" (empty)  -> same origin. Use this when the backend
//     serves the site (e.g. https:// YOUR-RENDER-APP /).
//   * Or the full backend URL, e.g.
//     "https://adarsh-boutique-backend.onrender.com"
// ============================================================
window.APP_CONFIG = {
  appointmentEndpoint: "https://script.google.com/macros/s/AKfycbymigoixFCgOsqF826Ym99u4qHdG1bZYCgNdt_WOfsBVcEj02PhQEDGpg7PBT5laY9-kw/exec",
  apiBase: ""
};
