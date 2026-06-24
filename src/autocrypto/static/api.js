(function () {
  "use strict";

  async function api(path, options = {}) {
    const request = {
      method: options.method || "GET",
      headers: { ...(options.headers || {}) },
      credentials: "same-origin",
    };
    if (options.body !== undefined) {
      request.headers["Content-Type"] = "application/json";
      request.body = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
    }
    const response = await fetch(path, request);
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = typeof payload === "object" ? payload.detail || JSON.stringify(payload) : payload;
      throw new Error(detail || `Request failed: ${response.status}`);
    }
    return payload;
  }

  window.AutoCryptoApi = { api };
})();
