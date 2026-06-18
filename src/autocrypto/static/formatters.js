(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function money(value, options = {}) {
    const parsed = Number(value || 0);
    const digits = options.digits ?? 2;
    return `$${parsed.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }

  function percent(value) {
    const parsed = Number(value || 0);
    return `${parsed >= 0 ? "+" : ""}${parsed.toFixed(2)}%`;
  }

  function metricNumber(value) {
    return Number(String(value || "").replace(/[^0-9.-]/g, "")) || 0;
  }

  function positiveNumber(value) {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  }

  function trimQuantity(value, digits = 8) {
    const parsed = Number(value || 0);
    if (!Number.isFinite(parsed)) return String(value || "0");
    const fixed = parsed.toFixed(digits);
    return fixed.replace(/\.?0+$/, "") || "0";
  }

  function compactSymbol(symbol) {
    return String(symbol || "").replace("/", "").toUpperCase();
  }

  function prettySymbol(symbol) {
    const raw = String(symbol || "").toUpperCase();
    if (raw.includes("/")) return raw;
    if (raw.endsWith("USDT")) return `${raw.slice(0, -4)}/USDT`;
    if (raw.endsWith("USDC")) return `${raw.slice(0, -4)}/USDC`;
    if (raw.endsWith("USD")) return `${raw.slice(0, -3)}/USD`;
    return raw;
  }

  function baseAsset(symbol) {
    return prettySymbol(symbol).split("/")[0] || "base";
  }

  function coinClass(symbol) {
    const compact = compactSymbol(symbol);
    if (compact.startsWith("BTC")) return "btc";
    if (compact.startsWith("ETH")) return "eth";
    return "sol";
  }

  function formatBacktestTime(value) {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "not run";
    return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function formatAuditTime(value) {
    if (!value) return "-";
    const parsed = new Date(`${String(value).replace(" ", "T")}Z`);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString();
  }

  function csvCell(value) {
    const text = String(value ?? "");
    return `"${text.replaceAll('"', '""')}"`;
  }

  function formatDraftTime(value) {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "saved locally";
    return `saved ${parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  }

  window.AutoCryptoFormatters = {
    escapeHtml,
    money,
    percent,
    metricNumber,
    positiveNumber,
    trimQuantity,
    compactSymbol,
    baseAsset,
    prettySymbol,
    coinClass,
    formatBacktestTime,
    formatAuditTime,
    csvCell,
    formatDraftTime,
  };
})();
