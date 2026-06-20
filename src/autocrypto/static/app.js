const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const {
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
} = window.AutoCryptoFormatters;
const {
  readPinnedStrategies,
  writePinnedStrategies,
  readStoredBacktests,
  writeStoredBacktests,
  readStoredTicketDraft,
  writeStoredTicketDraft,
  clearStoredTicketDraft,
  readAutoRefreshEnabled,
  writeAutoRefreshEnabled,
  readImportedStrategy,
  writeImportedStrategy,
} = window.AutoCryptoStorage;
const { api } = window.AutoCryptoApi;
const { defaultMarkets, strategies } = window.AutoCryptoCatalog;

const appState = {
  data: null,
  exchanges: [],
  platforms: [],
  activeView: "dashboard",
  selectedPair: "BTCUSDT",
  timeframe: "15m",
  deskTable: "positions",
  deskSearch: "",
  runtimeFilter: "all",
  strategyFilter: "all",
  strategySearch: "",
  strategySort: "featured",
  equityRange: "1d",
  parsedSignal: null,
  riskPreview: null,
  lastPayload: null,
  backtests: readStoredBacktests(),
  selectedExchange: null,
  markPrices: {},
  refreshInFlight: false,
  autoRefreshTimer: null,
  autoRefreshMs: 10000,
};

function backtestSortValue(strategy, key) {
  const value = Number(appState.backtests[strategy.id]?.[key]);
  return Number.isFinite(value) ? value : null;
}

function compareOptional(left, right, direction = "desc") {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return direction === "asc" ? left - right : right - left;
}

function setStatus(message, type = "") {
  const el = $("#statusLine");
  el.textContent = message;
  el.className = `status-line ${type}`.trim();
}

async function loadState(showStatus = true) {
  if (appState.refreshInFlight) return;
  appState.refreshInFlight = true;
  try {
    appState.data = await api("/ui/state");
    await loadExchanges(false);
    await loadPlatforms(false);
    renderAll();
    if (showStatus) setStatus("State refreshed from Auto-Crypto API.", "ok");
  } catch (error) {
    setStatus(`Unable to load API state: ${error.message}`, "error");
  } finally {
    appState.refreshInFlight = false;
  }
}

function setAutoRefresh(enabled, { persist = true, announce = true } = {}) {
  const button = $("#autoRefreshButton");
  if (appState.autoRefreshTimer) {
    clearInterval(appState.autoRefreshTimer);
    appState.autoRefreshTimer = null;
  }
  if (enabled) {
    appState.autoRefreshTimer = window.setInterval(() => loadState(false), appState.autoRefreshMs);
  }
  button.classList.toggle("is-selected", enabled);
  button.setAttribute("aria-pressed", String(enabled));
  button.textContent = enabled ? "Auto On" : "Auto 10s";
  if (persist) writeAutoRefreshEnabled(enabled);
  if (announce) setStatus(enabled ? "Auto refresh enabled every 10 seconds." : "Auto refresh disabled.", enabled ? "ok" : "");
}

function restoreAutoRefresh() {
  setAutoRefresh(readAutoRefreshEnabled(), { persist: false, announce: false });
}

async function loadExchanges(showStatus = true) {
  try {
    const payload = await api("/exchanges");
    appState.exchanges = payload.exchanges || [];
    if (showStatus) setStatus(`Loaded ${appState.exchanges.length} exchanges.`, "ok");
  } catch (error) {
    appState.exchanges = [];
    if (showStatus) setStatus(`Exchange discovery failed: ${error.message}`, "error");
  }
}

async function loadPlatforms(showStatus = true) {
  try {
    const payload = await api("/exchanges/platforms");
    appState.platforms = payload.platforms || [];
    if (showStatus) setStatus(`Loaded ${appState.platforms.length} trading platforms.`, "ok");
  } catch (error) {
    appState.platforms = [];
    if (showStatus) setStatus(`Platform registry failed: ${error.message}`, "error");
  }
}

function renderAll() {
  renderShell();
  renderMarketStrip();
  renderDashboard();
  renderSignals();
  renderTradingDesk();
  renderStrategies();
  renderPortfolio();
  renderExchanges();
  renderAudit();
  drawAllCharts();
}

function approvalModeEnabled() {
  return Boolean(appState.data?.execution?.require_approval);
}

function renderExecutionMode() {
  const approvalCount = (appState.data?.approvals || []).length;
  const requiresApproval = approvalModeEnabled();
  $("#approvalPill").textContent = requiresApproval
    ? `Approval gate on | ${approvalCount} queued`
    : `Approval gate off | ${approvalCount} queued`;
  $("#submitSignalButton").textContent = requiresApproval ? "Queue for Approval" : "Submit Paper Signal";
  $("#submitTicketButton").textContent = requiresApproval ? "Queue Approval" : "Submit";
}

function renderShell() {
  const data = appState.data || {};
  const halted = Boolean(data.control?.halted);
  const reason = String(data.control?.reason || "");
  $("#railMode").textContent = halted ? "Halted" : "Armed";
  $("#railMode").className = halted ? "red" : "green";
  $("#haltReasonLabel").textContent = halted ? reason || "manual halt" : "none";
  $("#haltReasonLabel").className = halted ? "amber" : "";
  $("#lastRefresh").textContent = new Date().toLocaleTimeString();
  $("#haltButton").disabled = halted;
  $("#resumeButton").disabled = !halted;
  $("#webhookPill").textContent = halted ? "Webhook intake halted" : "Webhook intake live";
  renderExecutionMode();
}

function renderMarketStrip() {
  const latestBySymbol = new Map();
  for (const order of appState.data?.orders || []) {
    if (order.price) latestBySymbol.set(order.symbol, Number(order.price));
  }
  $("#marketStrip").innerHTML = defaultMarkets
    .map((market) => {
      const price = appState.markPrices[market.symbol] ?? latestBySymbol.get(market.symbol) ?? market.price;
      const suffix = market.suffix || "";
      const priceText = market.suffix ? `${Number(price).toFixed(2)}${suffix}` : money(price);
      const cls = market.change > 0 ? "up" : market.change < 0 ? "down" : "flat";
      return `<div><span>${market.symbol}</span><strong>${priceText}</strong><em class="${cls}">${market.suffix ? "OK" : percent(market.change)}</em></div>`;
    })
    .join("");
}

function renderDashboard() {
  const data = appState.data || {};
  const approvals = data.approvals || [];
  const signals = data.signals || [];
  const orders = data.orders || [];
  const audit = data.audit || [];

  $("#pendingPreview").innerHTML =
    approvals.length > 0
      ? approvals.slice(0, 3).map(signalRow).join("")
      : signals.length > 0
        ? signals.slice(-3).reverse().map((signal) => signalRow(signal, "seen")).join("")
        : `<div class="empty-state">No queued signals yet. Use Signal Forge to parse and submit a paper signal.</div>`;

  const openNotional = Number(data.account?.open_notional || 0);
  const maxOpen = Number(data.risk?.max_open_notional || 0) || Number(data.account?.equity || 10000);
  const usage = maxOpen > 0 ? Math.min(100, (openNotional / maxOpen) * 100) : 0;
  $("#riskPercent").textContent = `${Math.round(usage)}%`;
  $("#riskStateLabel").textContent = data.control?.halted ? "halted" : usage > 80 ? "watch" : "normal";
  $("#riskStateLabel").className = data.control?.halted || usage > 80 ? "amber" : "green";
  $("#riskMetrics").innerHTML = [
    ["Open notional", `${money(openNotional)} / ${money(maxOpen)}`],
    ["Max order", money(data.risk?.max_order_notional || 0)],
    ["Max equity %", `${data.risk?.max_position_equity_pct || "0"}%`],
    ["Max SL", `${data.risk?.max_stop_loss_pct || "0"}%`],
    ["Max trail", `${data.risk?.max_trailing_stop_pct || "0"}%`],
    ["Min R/R", data.risk?.min_reward_risk_ratio || "0"],
    ["Daily loss cap", money(data.risk?.max_daily_loss || 0)],
    ["Allowed venues", (data.risk?.allowed_exchanges || []).join(", ") || "none"],
  ]
    .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");

  $("#exchangeFabric").innerHTML =
    appState.exchanges.slice(0, 4).map((exchange) => {
      const status = exchange.exchange_id === "paper" ? "paper ready" : exchange.driver_available ? exchange.driver : "offline";
      return `<div><strong>${escapeHtml(exchange.exchange_id)}</strong><span>${escapeHtml(status)}</span><em class="${exchange.driver_available ? "up" : "down"}">${exchange.live_execution_enabled ? "live" : "locked"}</em></div>`;
    }).join("") || `<div class="empty-state">No exchange data loaded.</div>`;

  const filteredOrders = orders.filter((order) => appState.runtimeFilter === "all" || order.side === appState.runtimeFilter);
  $("#runtimeRows").innerHTML =
    filteredOrders.length > 0
      ? filteredOrders.slice(-8).reverse().map(orderRuntimeRow).join("")
      : `<tr><td colspan="7">No paper orders yet.</td></tr>`;

  $("#auditPreview").innerHTML =
    audit.length > 0
      ? audit.slice(-4).reverse().map((event) => `<li><strong>${escapeHtml(event.event_type)}</strong><span>${escapeHtml(JSON.stringify(event.data))}</span></li>`).join("")
      : `<li><strong>empty</strong><span>No audit events yet.</span></li>`;
}

function signalRow(signal, label = "needs approval") {
  const symbol = signal.symbol || "UNKNOWN";
  const size = signal.quote_amount ? `$${signal.quote_amount}` : signal.base_amount || "size?";
  const price = signal.price ? ` @ ${signal.price}` : "";
  const queued = signal.created_at ? `queued ${formatAuditTime(signal.created_at)} | ` : "";
  const actionButtons = label === "needs approval" ? approvalActions(signal) : `<em>${escapeHtml(label)}</em>`;
  return `
    <article class="signal-row ${label === "needs approval" ? "priority" : ""}">
      <div class="coin-dot ${coinClass(symbol)}">${escapeHtml(symbol[0] || "?")}</div>
      <div><strong>${escapeHtml(String(signal.side || "").toUpperCase())} ${escapeHtml(symbol)}</strong><span>${escapeHtml(queued)}${escapeHtml(signal.strategy_id || signal.source || "manual")} | ${escapeHtml(size)}${escapeHtml(price)}</span></div>
      ${actionButtons}
    </article>
  `;
}

function approvalActions(signal) {
  const signalId = escapeHtml(signal.signal_id);
  const payload = escapeHtml(JSON.stringify(signal));
  return `
    <div class="row-actions">
      <button type="button" data-action="approve" data-signal-id="${signalId}">Approve</button>
      <button type="button" data-action="reject" data-signal-id="${signalId}">Reject</button>
      <button type="button" data-action="preview-approval-ticket" data-signal-id="${signalId}">Preview</button>
      <button type="button" data-action="copy-json" data-json="${payload}">Copy JSON</button>
    </div>
  `;
}

function orderRuntimeRow(order) {
  return `
    <tr>
      <td>${escapeHtml(formatAuditTime(order.created_at))}</td>
      <td title="${escapeHtml(order.order_id)}">${escapeHtml(order.order_id)}</td>
      <td>${escapeHtml(order.symbol)}</td>
      <td class="${order.side === "buy" ? "up" : "down"}">${escapeHtml(order.side)}</td>
      <td>${money(order.notional)}</td>
      <td>${order.price ? money(order.price) : "market"}</td>
      <td><button type="button" data-action="inspect-order" data-order-id="${escapeHtml(order.order_id)}">Inspect</button></td>
    </tr>
  `;
}

function orderDeskRow(order) {
  const payload = escapeHtml(JSON.stringify(order));
  return `
    <tr>
      <td>${escapeHtml(formatAuditTime(order.created_at))}</td>
      <td title="${escapeHtml(order.order_id)}">${escapeHtml(order.order_id)}</td>
      <td>${escapeHtml(order.symbol)}</td>
      <td class="${order.side === "buy" ? "up" : "down"}">${escapeHtml(order.side)}</td>
      <td>${money(order.notional)}</td>
      <td>${order.price ? money(order.price) : "market"}</td>
      <td>${escapeHtml(order.status)}</td>
      <td>
        <div class="row-actions">
          <button type="button" data-action="inspect-order" data-order-id="${escapeHtml(order.order_id)}">Inspect</button>
          <button type="button" data-action="copy-json" data-json="${payload}">Copy</button>
        </div>
      </td>
    </tr>
  `;
}

function deskRowMatches(type, row) {
  const needle = appState.deskSearch.trim().toLowerCase();
  if (!needle) return true;

  if (type === "orders") {
    return [
      row.created_at,
      row.order_id,
      row.signal_id,
      row.symbol,
      compactSymbol(row.symbol),
      row.side,
      row.notional,
      row.price || "market",
      row.status,
    ].some((value) => String(value || "").toLowerCase().includes(needle));
  }

  const mark = currentMarkPrice(compactSymbol(row.symbol));
  const unrealized = Number(row.quantity || 0) * (mark - Number(row.avg_entry || 0));
  return [
    row.symbol,
    compactSymbol(row.symbol),
    row.quantity,
    row.avg_entry,
    row.realized_pnl,
    mark,
    unrealized,
  ].some((value) => String(value || "").toLowerCase().includes(needle));
}

function deskCountLabel(type, visible, total) {
  const noun = type === "orders" ? "orders" : "positions";
  return appState.deskSearch.trim() ? `${visible}/${total} ${noun}` : `${total} ${noun}`;
}

function renderSignals() {
  const approvals = appState.data?.approvals || [];
  $("#approvalCount").textContent = String(approvals.length);
  $("#approvalList").innerHTML =
    approvals.length > 0
      ? approvals.map((approval) => signalRow(approval)).join("")
      : `<div class="empty-state">No pending approvals. Enable approval mode and submit a signal to fill this queue.</div>`;

  if (!appState.parsedSignal) {
    $("#parsedSignal").innerHTML = `<div class="empty-state">Parse an alert to preview normalized fields.</div>`;
  } else {
    const signal = appState.parsedSignal;
    $("#parsedSignal").innerHTML = [
      ["symbol", signal.symbol],
      ["side", signal.side],
      ["quote", signal.quote_amount || "-"],
      ["base", signal.base_amount || "-"],
      ["price", signal.price || "-"],
      ["stop", signal.stop_loss_pct || "-"],
      ["take profit", signal.take_profit_pct || "-"],
      ["trail activation", signal.trailing_activation_pct || "-"],
      ["signal id", signal.signal_id],
    ]
      .map(([label, value]) => `<span>${escapeHtml(label)}<strong>${escapeHtml(value)}</strong></span>`)
      .join("");
  }
  renderRiskPreview();
  renderSignalHistory();
  $("#payloadPreview").textContent = JSON.stringify(appState.lastPayload || appState.parsedSignal || {}, null, 2);
}

function renderSignalHistory() {
  const query = $("#signalSearch").value.trim().toLowerCase();
  const allSignals = appState.data?.signals || [];
  const signals = allSignals.filter((signal) => {
    const text = [
      signal.signal_id,
      signal.symbol,
      signal.side,
      signal.source,
      signal.strategy_id,
      signal.quote_amount,
      signal.base_amount,
      signal.created_at,
    ].join(" ").toLowerCase();
    return !query || text.includes(query);
  });
  $("#signalResultCount").textContent = signalCountLabel(signals.length, allSignals.length);
  $("#signalHistoryRows").innerHTML =
    signals.length > 0
      ? signals.slice().reverse().map(signalHistoryRow).join("")
      : `<tr><td colspan="7">No submitted signals match.</td></tr>`;
}

function signalCountLabel(visible, total) {
  return $("#signalSearch").value.trim() ? `${visible}/${total} signals` : `${total} signals`;
}

function signalHistoryRow(signal) {
  const payload = escapeHtml(JSON.stringify(signal));
  const size = signal.quote_amount
    ? money(signal.quote_amount)
    : signal.base_amount
      ? `${signal.base_amount} ${baseAsset(signal.symbol)}`
      : "-";
  return `
    <tr>
      <td>${escapeHtml(formatAuditTime(signal.created_at))}</td>
      <td title="${escapeHtml(signal.signal_id)}">${escapeHtml(signal.symbol)}</td>
      <td class="${signal.side === "buy" ? "up" : "down"}">${escapeHtml(signal.side)}</td>
      <td>${escapeHtml(size)}</td>
      <td>${signal.price ? money(signal.price) : "market"}</td>
      <td>${escapeHtml(signal.strategy_id || "manual")}</td>
      <td>
        <div class="row-actions">
          <button type="button" data-action="load-signal-ticket" data-signal-id="${escapeHtml(signal.signal_id)}">Load</button>
          <button type="button" data-action="preview-signal-ticket" data-signal-id="${escapeHtml(signal.signal_id)}">Preview</button>
          <button type="button" data-action="copy-json" data-json="${payload}">Copy JSON</button>
        </div>
      </td>
    </tr>
  `;
}

function renderRiskPreview() {
  const preview = appState.riskPreview;
  if (!preview) {
    $("#riskPreview").innerHTML = `<div class="empty-state">Preview risk to see server-side checks before submission.</div>`;
    renderTicketPreview();
    return;
  }

  const risk = preview.risk || {};
  const execution = preview.execution || {};
  const reasons = risk.reason_codes || [];
  const status = String(execution.next_status || "unknown").replaceAll("_", " ");
  const approved = Boolean(risk.approved);
  const statusClass = execution.next_status === "halted" || !approved ? "down" : execution.next_status === "approval_required" ? "amber" : "up";
  const orderText = risk.order_notional ? money(risk.order_notional) : "unknown";
  const nextStep = execution.would_place_order
    ? "Paper order would be placed"
    : execution.next_status === "approval_required"
      ? "Would queue for operator approval"
      : execution.next_status === "halted"
        ? `Blocked by halt${execution.halt_reason ? `: ${execution.halt_reason}` : ""}`
        : approved
          ? "Ready after operator action"
          : "Submission would be rejected";

  $("#riskPreview").innerHTML = `
    <div class="preview-head">
      <span>Risk Preview</span>
      <strong class="${statusClass}">${escapeHtml(status)}</strong>
    </div>
    <div class="preview-metrics">
      <span>Risk<strong class="${approved ? "up" : "down"}">${approved ? "approved" : "blocked"}</strong></span>
      <span>Notional<strong>${escapeHtml(orderText)}</strong></span>
      <span>Open<strong>${money(preview.account?.open_notional || 0)}</strong></span>
    </div>
    <div class="reason-chips">
      ${
        reasons.length > 0
          ? reasons.map((reason) => `<span>${escapeHtml(String(reason).replaceAll("_", " "))}</span>`).join("")
          : `<span class="clear">No risk blockers</span>`
      }
    </div>
    <p class="preview-note">${escapeHtml(nextStep)}</p>
  `;
  renderTicketPreview();
}

function renderTicketPreview() {
  const container = $("#ticketPreviewSummary");
  if (!container) return;
  const preview = appState.riskPreview;
  if (!preview) {
    container.innerHTML = `
      <div class="ticket-preview-grid">
        <span>Risk<strong>not checked</strong></span>
        <span>Next<strong>preview</strong></span>
        <span>Notional<strong>-</strong></span>
      </div>
      <p>Load or preview a ticket to see server-side checks.</p>
    `;
    return;
  }

  const risk = preview.risk || {};
  const execution = preview.execution || {};
  const reasons = risk.reason_codes || [];
  const status = String(execution.next_status || "unknown").replaceAll("_", " ");
  const approved = Boolean(risk.approved);
  const statusClass = execution.next_status === "halted" || !approved ? "down" : execution.next_status === "approval_required" ? "amber" : "up";
  const orderText = risk.order_notional ? money(risk.order_notional) : "unknown";
  const reasonText = reasons.length
    ? reasons.map((reason) => String(reason).replaceAll("_", " ")).join(", ")
    : "No risk blockers";
  container.innerHTML = `
    <div class="ticket-preview-grid">
      <span>Risk<strong class="${approved ? "up" : "down"}">${approved ? "approved" : "blocked"}</strong></span>
      <span>Next<strong class="${statusClass}">${escapeHtml(status)}</strong></span>
      <span>Notional<strong>${escapeHtml(orderText)}</strong></span>
    </div>
    <p>${escapeHtml(reasonText)}</p>
  `;
}

function renderTradingDesk() {
  $("#chartTitle").textContent = `${prettySymbol(appState.selectedPair)} Context`;
  const mark = currentMarkPrice(appState.selectedPair);
  $("#markPrice").value = String(mark.toFixed(mark > 1000 ? 0 : 2));
  renderOrderBook(mark);
  renderDeskTable();
  renderTicketPreview();
}

function renderOrderBook(mid) {
  const rows = [];
  for (let index = 3; index >= 1; index -= 1) {
    const price = mid + index * (mid > 1000 ? 2.8 : 0.18);
    rows.push(`<div class="ask" style="--depth:${35 + index * 16}%"><span>${price.toFixed(mid > 1000 ? 1 : 2)}</span><strong>${(0.4 + index * 0.37).toFixed(3)}</strong></div>`);
  }
  rows.push(`<div class="mid">${mid.toFixed(mid > 1000 ? 2 : 3)}</div>`);
  for (let index = 1; index <= 3; index += 1) {
    const price = mid - index * (mid > 1000 ? 2.8 : 0.18);
    rows.push(`<div class="bid" style="--depth:${72 - index * 13}%"><span>${price.toFixed(mid > 1000 ? 1 : 2)}</span><strong>${(0.65 + index * 0.51).toFixed(3)}</strong></div>`);
  }
  $("#orderBook").innerHTML = rows.join("");
}

function renderDeskTable() {
  if (appState.deskTable === "orders") {
    $("#deskTableHead").innerHTML = `<tr><th>Time</th><th>Order</th><th>Pair</th><th>Side</th><th>Notional</th><th>Price</th><th>Status</th><th>Action</th></tr>`;
    const allOrders = appState.data?.orders || [];
    const orders = allOrders.filter((order) => deskRowMatches("orders", order));
    $("#deskResultCount").textContent = deskCountLabel("orders", orders.length, allOrders.length);
    $("#deskTableBody").innerHTML =
      orders.length > 0
        ? orders.slice(-12).reverse().map(orderDeskRow).join("")
        : `<tr><td colspan="8">${appState.deskSearch.trim() && allOrders.length > 0 ? "No orders match the current filter." : "No orders submitted yet."}</td></tr>`;
    return;
  }

  $("#deskTableHead").innerHTML = `<tr><th>Pair</th><th>Quantity</th><th>Average Entry</th><th>Realized P&L</th><th>Unrealized</th><th>Mark</th><th>Action</th></tr>`;
  const allPositions = appState.data?.positions || [];
  const positions = allPositions.filter((position) => deskRowMatches("positions", position));
  $("#deskResultCount").textContent = deskCountLabel("positions", positions.length, allPositions.length);
  $("#deskTableBody").innerHTML =
    positions.length > 0
      ? positions.map((position) => {
        const mark = currentMarkPrice(compactSymbol(position.symbol));
        const unrealized = Number(position.quantity || 0) * (mark - Number(position.avg_entry || 0));
        const compact = compactSymbol(position.symbol);
        const closeButtons = [
          ["25%", 0.25, "Close 25%"],
          ["50%", 0.5, "Close 50%"],
          ["All", 1, "Close Position"],
        ].map(([label, fraction, strategy]) => {
          const quantity = trimQuantity(Number(position.quantity || 0) * fraction);
          const disabled = Number(quantity) <= 0 ? " disabled" : "";
          return `<button type="button" data-action="close-position" data-symbol="${escapeHtml(compact)}" data-quantity="${escapeHtml(quantity)}" data-price="${mark}" data-close-label="${escapeHtml(strategy)}"${disabled}>${escapeHtml(label)}</button>`;
        }).join("");
        return `
          <tr>
            <td>${escapeHtml(position.symbol)}</td>
            <td>${escapeHtml(position.quantity)}</td>
            <td>${money(position.avg_entry)}</td>
            <td class="${Number(position.realized_pnl) >= 0 ? "up" : "down"}">${money(position.realized_pnl)}</td>
            <td class="${unrealized >= 0 ? "up" : "down"}">${money(unrealized)}</td>
            <td>${money(mark)}</td>
            <td>
              <div class="row-actions">
                <button type="button" data-action="load-position-price" data-symbol="${escapeHtml(compact)}" data-price="${mark}">Use Mark</button>
                ${closeButtons}
              </div>
            </td>
          </tr>
        `;
      }).join("")
      : `<tr><td colspan="7">${appState.deskSearch.trim() && allPositions.length > 0 ? "No positions match the current filter." : "No open positions. Submit a paper buy with a price to create one."}</td></tr>`;
}

function renderStrategies() {
  const pinned = readPinnedStrategies();
  const needle = appState.strategySearch.trim().toLowerCase();
  const visible = strategies
    .filter((strategy) => appState.strategyFilter === "all" || strategy.type === appState.strategyFilter)
    .filter((strategy) => {
      if (!needle) return true;
      return [strategy.name, strategy.type, strategy.pair, prettySymbol(strategy.pair), strategy.roi, strategy.win, strategy.drawdown]
        .some((value) => String(value).toLowerCase().includes(needle));
    })
    .sort((left, right) => {
      const pinnedDelta = Number(pinned.has(right.id)) - Number(pinned.has(left.id));
      if (pinnedDelta) return pinnedDelta;
      if (appState.strategySort === "sim-return") {
        const compared = compareOptional(backtestSortValue(left, "return_pct"), backtestSortValue(right, "return_pct"));
        if (compared) return compared;
      }
      if (appState.strategySort === "sim-drawdown") {
        const leftDrawdown = backtestSortValue(left, "max_drawdown_pct");
        const rightDrawdown = backtestSortValue(right, "max_drawdown_pct");
        const compared = compareOptional(
          leftDrawdown === null ? null : Math.abs(leftDrawdown),
          rightDrawdown === null ? null : Math.abs(rightDrawdown),
          "asc",
        );
        if (compared) return compared;
      }
      if (appState.strategySort === "drawdown") return metricNumber(left.drawdown) - metricNumber(right.drawdown);
      if (appState.strategySort === "win") return metricNumber(right.win) - metricNumber(left.win);
      if (appState.strategySort === "name") return left.name.localeCompare(right.name);
      return metricNumber(right.roi) - metricNumber(left.roi);
    });
  $("#strategyCards").innerHTML = visible.length
    ? visible.map((strategy) => strategyCard(strategy, pinned.has(strategy.id))).join("")
    : `<div class="empty-state strategy-empty">No strategies match the current search.</div>`;
  const activePinned = strategies.filter((strategy) => pinned.has(strategy.id)).length;
  $("#strategyResultCount").textContent = `${visible.length}/${strategies.length} shown | ${activePinned} pinned`;
  const imported = readImportedStrategy();
  $("#importStatus").textContent = imported ? `loaded: ${imported.name}` : "waiting";
  $("#strategyChecklist").innerHTML = [
    ["Backtest window", imported ? "365-day local simulation ready" : "run or copy a strategy", Boolean(imported)],
    ["Venue support", imported ? "paper venue mapped" : "pending", Boolean(imported)],
    ["Risk envelope", imported ? "stop and take-profit loaded" : "pending", Boolean(imported)],
    ["Operator gate", "paper-first; live trading locked", true],
  ]
    .map(([label, value, done]) => `<li class="${done ? "done" : ""}"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value)}</span></li>`)
    .join("");
  requestAnimationFrame(drawStrategySparks);
}

function strategyCard(strategy, isPinned) {
  return `
    <article class="strategy-card ${isPinned ? "is-pinned" : ""}">
      <div class="strategy-head">
        <strong>${escapeHtml(strategy.name)}</strong>
        <span>${escapeHtml(strategy.type.toUpperCase())} | ${prettySymbol(strategy.pair)}</span>
        <button type="button" data-action="toggle-strategy-pin" data-strategy-id="${strategy.id}" aria-pressed="${isPinned}">${isPinned ? "Pinned" : "Pin"}</button>
      </div>
      <canvas data-strategy-spark="${strategy.id}" width="320" height="96" aria-label="${escapeHtml(strategy.name)} backtest curve"></canvas>
      ${strategyBacktestSummary(strategy)}
      <dl>
        <div><dt>300D ROI</dt><dd class="up">${strategy.roi}</dd></div>
        <div><dt>Win rate</dt><dd>${strategy.win}</dd></div>
        <div><dt>Max DD</dt><dd>${strategy.drawdown}</dd></div>
      </dl>
      <div class="strategy-actions">
        <button type="button" data-action="backtest-strategy" data-strategy-id="${strategy.id}">Backtest</button>
        <button type="button" data-action="copy-strategy" data-strategy-id="${strategy.id}">Load Ticket</button>
      </div>
    </article>
  `;
}

function strategyBacktestSummary(strategy) {
  const backtest = appState.backtests[strategy.id];
  if (!backtest) {
    return `
      <div class="strategy-backtest is-empty">
        <span>Sim return<strong>-</strong></span>
        <span>Sim DD<strong>-</strong></span>
        <span>Last run<strong>not run</strong></span>
      </div>
    `;
  }
  return `
    <div class="strategy-backtest">
      <span>Sim return<strong class="${backtest.return_pct >= 0 ? "up" : "down"}">${percent(backtest.return_pct)}</strong></span>
      <span>Sim DD<strong>${Math.abs(Number(backtest.max_drawdown_pct || 0)).toFixed(2)}%</strong></span>
      <span>Last run<strong>${escapeHtml(formatBacktestTime(backtest.updated_at))}</strong></span>
    </div>
  `;
}

function renderPortfolio() {
  const account = appState.data?.account || {};
  $("#navAmount").textContent = `${money(account.equity || 0)} paper NAV`;
  const dailyPnl = Number(account.daily_pnl || 0);
  $("#dailyPnl").textContent = money(dailyPnl);
  $("#dailyPnl").className = `big-number ${dailyPnl >= 0 ? "up" : "down"}`;

  const positions = appState.data?.positions || [];
  const maxOpen = Number(appState.data?.risk?.max_open_notional || 0) || Number(account.equity || 10000);
  const grouped = positions.length > 0
    ? positions.map((position) => ({ symbol: position.symbol, value: Math.abs(Number(position.quantity)) * Number(position.avg_entry || 0) }))
    : (appState.data?.orders || []).slice(-4).map((order) => ({ symbol: order.symbol, value: Number(order.notional || 0) }));

  $("#limitBars").innerHTML =
    (grouped.length > 0 ? grouped : defaultMarkets.slice(0, 3).map((market) => ({ symbol: market.symbol, value: 0 })))
      .map((item) => {
        const pct = maxOpen > 0 ? Math.min(100, (item.value / maxOpen) * 100) : 0;
        return `<div><span>${escapeHtml(item.symbol)}</span><meter value="${pct}" min="0" max="100"></meter><strong>${Math.round(pct)}%</strong></div>`;
      })
      .join("");
  $("#limitState").textContent = grouped.some((item) => item.value > maxOpen * 0.8) ? "watch" : "within bounds";

  const exits = appState.data?.active_exits || [];
  $("#bracketCount").textContent = `${exits.length} active exits`;
  $("#bracketRows").innerHTML =
    exits.length > 0
      ? exits.map((exit) => {
        const compact = compactSymbol(exit.symbol);
        return `
          <tr>
            <td>${escapeHtml(exit.symbol)}</td>
            <td>${escapeHtml(exit.direction || "long")}</td>
            <td>${escapeHtml(exit.kind)}</td>
            <td>${escapeHtml(exit.remaining_quantity || "-")}</td>
            <td>${exit.entry_price ? money(exit.entry_price) : "-"}</td>
            <td>${money(exit.trigger_price)}</td>
            <td>
              <div class="row-actions">
                <button type="button" data-action="load-position-price" data-symbol="${escapeHtml(compact)}" data-price="${escapeHtml(exit.trigger_price)}">Load</button>
                <button type="button" data-action="preview-bracket" data-signal-id="${escapeHtml(exit.signal_id)}" data-price="${escapeHtml(exit.trigger_price)}">Preview</button>
                <button type="button" data-action="trigger-exit-price" data-symbol="${escapeHtml(compact)}" data-price="${escapeHtml(exit.trigger_price)}">Trigger</button>
                <button type="button" data-action="amend-bracket-stop" data-signal-id="${escapeHtml(exit.signal_id)}" data-price="${escapeHtml(exit.trigger_price)}">Tighten Stop</button>
                <button type="button" data-action="cancel-bracket" data-signal-id="${escapeHtml(exit.signal_id)}">Cancel Bracket</button>
              </div>
            </td>
          </tr>
        `;
      }).join("")
      : `<tr><td colspan="7">No active exits. Bracketed buy and short signals create rows.</td></tr>`;
}

function renderExchanges() {
  const query = $("#exchangeSearch").value.trim().toLowerCase();
  const exchanges = appState.exchanges.filter((exchange) => !query || exchange.exchange_id.includes(query));
  $("#exchangeCount").textContent = `${exchanges.length} shown`;
  renderPlatforms(query);
  $("#exchangeList").innerHTML =
    exchanges.slice(0, 80).map((exchange) => `
      <div class="exchange-row" data-action="exchange-cap" data-exchange-id="${escapeHtml(exchange.exchange_id)}">
        <div>
          <strong>${escapeHtml(exchange.exchange_id)}</strong>
          <span>${escapeHtml(exchange.driver)} · ${exchange.credentials_configured ? "credentials set" : "no keys"}</span>
        </div>
        <em class="${exchange.driver_available ? "up" : "down"}">${exchange.live_execution_enabled ? "live" : "locked"}</em>
      </div>
    `).join("") || `<div class="empty-state">No exchanges match the filter.</div>`;
  renderBitunixStatus();
}

function renderPlatforms(query = "") {
  const platforms = appState.platforms.filter((platform) => {
    const text = [
      platform.exchange_id,
      platform.display_name,
      platform.tier,
      platform.region,
      ...(platform.market_types || []),
    ].join(" ").toLowerCase();
    return !query || text.includes(query);
  });
  $("#platformCount").textContent = `${platforms.length} tracked`;
  $("#platformGrid").innerHTML =
    platforms.map((platform) => {
      const ready = platform.driver_available ? "ready" : platform.integration_status.replaceAll("_", " ");
      const markets = (platform.market_types || []).slice(0, 4).join(" · ");
      const credentialText = platform.credentials_configured ? "credentials set" : "no keys";
      return `
        <button class="platform-card" type="button" data-action="platform-integration" data-exchange-id="${escapeHtml(platform.exchange_id)}">
          <span>${escapeHtml(String(platform.priority).padStart(2, "0"))}</span>
          <strong>${escapeHtml(platform.display_name)}</strong>
          <em class="${platform.driver_available ? "up" : "down"}">${escapeHtml(ready)}</em>
          <small>${escapeHtml(markets || platform.tier)} · ${escapeHtml(credentialText)}</small>
        </button>
      `;
    }).join("") || `<div class="empty-state">No platforms match the filter.</div>`;
}

function renderBitunixStatus() {
  const bitunix = appState.exchanges.find((exchange) => exchange.exchange_id === "bitunix");
  if (!bitunix) {
    $("#bitunixStatus").textContent = "not discovered";
    return;
  }
  const credentialState = bitunix.credentials_configured ? "credentials set" : "credentials missing";
  const executionState = bitunix.live_execution_enabled ? "live enabled" : "live locked";
  $("#bitunixStatus").textContent = `${credentialState} · ${executionState}`;
}

function renderAudit() {
  const events = filteredAuditEvents();
  const total = appState.data?.audit?.length || 0;
  $("#auditResultCount").textContent = auditCountLabel(events.length, total);
  $("#auditRows").innerHTML =
    events.length > 0
      ? events.slice().reverse().map(auditRow).join("")
      : `<tr><td colspan="4">No audit events match.</td></tr>`;
}

function auditCountLabel(visible, total) {
  return $("#auditSearch").value.trim() ? `${visible}/${total} events` : `${total} events`;
}

function auditRow(event) {
  const data = event.data || {};
  const payload = escapeHtml(JSON.stringify(event));
  const relatedButton = data.order_id || data.signal_id
    ? `<button type="button" data-action="load-audit-related" data-order-id="${escapeHtml(data.order_id || "")}" data-signal-id="${escapeHtml(data.signal_id || "")}">Open</button>`
    : "";
  return `
    <tr>
      <td>${escapeHtml(formatAuditTime(event.created_at))}</td>
      <td>${escapeHtml(event.event_type)}</td>
      <td>${escapeHtml(JSON.stringify(data))}</td>
      <td>
        <div class="row-actions">
          ${relatedButton}
          <button type="button" data-action="copy-json" data-json="${payload}">Copy</button>
        </div>
      </td>
    </tr>
  `;
}

function filteredAuditEvents() {
  const query = $("#auditSearch").value.trim().toLowerCase();
  return (appState.data?.audit || []).filter((event) => {
    const text = `${event.created_at || ""} ${event.event_type} ${JSON.stringify(event.data)}`.toLowerCase();
    return !query || text.includes(query);
  });
}

function currentMarkPrice(symbol) {
  const pretty = prettySymbol(symbol);
  if (appState.markPrices[pretty] !== undefined) return Number(appState.markPrices[pretty]);
  const fromOrder = [...(appState.data?.orders || [])].reverse().find((order) => order.symbol === pretty && order.price);
  if (fromOrder) return Number(fromOrder.price);
  const market = defaultMarkets.find((item) => item.compact === compactSymbol(symbol));
  return Number(market?.price || 100);
}

function ticketToText() {
  const side = $("#ticketSide").value.toUpperCase();
  const symbol = compactSymbol($("#ticketSymbol").value || appState.selectedPair);
  const sizeMode = $("#ticketSizeMode").value;
  const amount = String($("#ticketAmount").value || "0");
  const size = sizeMode === "base" ? amount.replace("$", "") : `$${amount.replace("$", "")}`;
  const price = $("#ticketPrice").value || currentMarkPrice(symbol);
  const stop = $("#ticketStop").value;
  const takeProfit = $("#ticketTakeProfit").value;
  const trailingStop = $("#ticketTrailingStop").value;
  const trailActivation = $("#ticketTrailActivation").value;
  const breakeven = $("#ticketBreakeven").value;
  const stopPart = stop ? ` SL ${stop}%` : "";
  const tpPart = takeProfit ? ` TP ${takeProfit}%` : "";
  const trailingPart = trailingStop ? ` TRAIL ${trailingStop}%` : "";
  const activationPart = trailActivation ? ` ACT ${trailActivation}%` : "";
  const breakevenPart = breakeven ? ` BE ${breakeven}%` : "";
  return `${side} ${symbol} ${size} @ ${price}${stopPart}${tpPart}${trailingPart}${activationPart}${breakevenPart}`;
}

function ticketPayload() {
  const amount = String($("#ticketAmount").value || "0").replace("$", "");
  const payload = {
    symbol: $("#ticketSymbol").value,
    side: $("#ticketSide").value.toLowerCase(),
    price: $("#ticketPrice").value,
    stop_loss_pct: $("#ticketStop").value || null,
    take_profit_pct: $("#ticketTakeProfit").value || null,
    trailing_stop_pct: $("#ticketTrailingStop").value || null,
    trailing_activation_pct: $("#ticketTrailActivation").value || null,
    breakeven_trigger_pct: $("#ticketBreakeven").value || null,
    strategy_id: $("#ticketStrategy").value,
  };
  if ($("#ticketSizeMode").value === "base") {
    payload.base_amount = amount;
  } else {
    payload.quote_amount = amount;
  }
  return payload;
}

function setTicketSizeMode(mode, amount) {
  const normalized = mode === "base" ? "base" : "quote";
  $("#ticketSizeMode").value = normalized;
  $("#ticketAmountLabel").textContent = normalized === "base" ? "Base quantity" : "Quote amount";
  if (amount !== undefined) $("#ticketAmount").value = amount;
}

function maxTicketNotional() {
  const risk = appState.data?.risk || {};
  const account = appState.data?.account || {};
  const maxOrder = positiveNumber(risk.max_order_notional);
  const maxOpen = positiveNumber(risk.max_open_notional);
  const openNotional = positiveNumber(account.open_notional);
  const candidates = [];
  if (maxOrder > 0) candidates.push(maxOrder);
  if (maxOpen > 0) candidates.push(Math.max(0, maxOpen - openNotional));
  return candidates.length ? Math.min(...candidates) : 250;
}

function sizePresetAmount(preset) {
  if (preset === "max-order") return positiveNumber(appState.data?.risk?.max_order_notional) || maxTicketNotional();
  if (preset === "remaining-cap") return maxTicketNotional();
  return positiveNumber(preset);
}

function applySizePreset(preset) {
  const amount = sizePresetAmount(preset);
  if (amount <= 0) {
    setStatus("No available notional remains for this preset.", "warn");
    return;
  }
  setTicketSizeMode("quote", String(Number(amount.toFixed(2))));
  $("#signalText").value = ticketToText();
  saveTicketDraft();
  const label = preset === "max-order" ? "max order" : preset === "remaining-cap" ? "remaining capacity" : money(amount);
  setStatus(`Ticket quote amount set from ${label}.`, "ok");
}

function setTicketStrategy(name) {
  const value = String(name || "manual");
  const select = $("#ticketStrategy");
  if (!Array.from(select.options).some((option) => option.value === value)) {
    select.add(new Option(value, value));
  }
  select.value = value;
}

function ticketDraftPayload() {
  return {
    strategy: $("#ticketStrategy").value,
    symbol: compactSymbol($("#ticketSymbol").value || appState.selectedPair),
    side: $("#ticketSide").value,
    size_mode: $("#ticketSizeMode").value,
    amount: $("#ticketAmount").value,
    price: $("#ticketPrice").value,
    stop_loss_pct: $("#ticketStop").value,
    take_profit_pct: $("#ticketTakeProfit").value,
    trailing_stop_pct: $("#ticketTrailingStop").value,
    trailing_activation_pct: $("#ticketTrailActivation").value,
    breakeven_trigger_pct: $("#ticketBreakeven").value,
    saved_at: new Date().toISOString(),
  };
}

function renderTicketDraftStatus(draft = readStoredTicketDraft()) {
  const container = $("#ticketDraftStatus");
  if (!container) return;
  container.textContent = draft ? formatDraftTime(draft.saved_at) : "no saved draft";
}

function saveTicketDraft() {
  const draft = ticketDraftPayload();
  writeStoredTicketDraft(draft);
  renderTicketDraftStatus(draft);
}

function applyStoredTicketDraft() {
  const draft = readStoredTicketDraft();
  if (!draft) {
    renderTicketDraftStatus(null);
    return;
  }
  setTicketStrategy(draft.strategy || "manual");
  $("#ticketSymbol").value = draft.symbol || $("#ticketSymbol").value;
  $("#ticketSide").value = draft.side || $("#ticketSide").value;
  setTicketSizeMode(draft.size_mode || "quote", draft.amount);
  $("#ticketPrice").value = draft.price || $("#ticketPrice").value;
  $("#ticketStop").value = draft.stop_loss_pct || "";
  $("#ticketTakeProfit").value = draft.take_profit_pct || "";
  $("#ticketTrailingStop").value = draft.trailing_stop_pct || "";
  $("#ticketTrailActivation").value = draft.trailing_activation_pct || "";
  $("#ticketBreakeven").value = draft.breakeven_trigger_pct || "";
  appState.selectedPair = compactSymbol(draft.symbol || appState.selectedPair);
  $("#signalText").value = ticketToText();
  renderTicketDraftStatus(draft);
}

function clearTicketDraft() {
  clearStoredTicketDraft();
  renderTicketDraftStatus(null);
  setStatus("Ticket draft forgotten. Current fields are unchanged.", "ok");
}

async function copyTicketAlert() {
  const text = ticketToText();
  $("#signalText").value = text;
  saveTicketDraft();
  await copyText(text);
}

async function copyTicketJson() {
  const payload = ticketPayload();
  appState.lastPayload = payload;
  renderSignals();
  saveTicketDraft();
  await copyText(JSON.stringify(payload, null, 2));
}

async function parseSignal() {
  const message = $("#signalText").value;
  const payload = await api("/signals/parse-text", { method: "POST", body: { message } });
  appState.parsedSignal = payload.signal;
  appState.lastPayload = payload.signal;
  renderSignals();
  setStatus("Signal parsed without placing an order.", "ok");
  return payload.signal;
}

async function previewSignal(message) {
  const payload = await api("/signals/preview-text", { method: "POST", body: { message } });
  appState.parsedSignal = payload.signal;
  appState.riskPreview = payload;
  appState.lastPayload = payload;
  renderSignals();
  setStatus(`Risk preview: ${payload.execution.next_status}.`, payload.risk.approved ? "ok" : "warn");
  return payload;
}

async function submitSignal(message) {
  const payload = await api("/signals/submit-text", { method: "POST", body: { message } });
  appState.lastPayload = payload;
  setStatus(`Signal result: ${payload.status || "submitted"}.`, payload.status === "rejected" ? "warn" : "ok");
  await loadState(false);
}

async function previewTicket(options = {}) {
  const payload = await api("/signals/preview", { method: "POST", body: ticketPayload() });
  appState.parsedSignal = payload.signal;
  appState.riskPreview = payload;
  appState.lastPayload = payload;
  renderSignals();
  renderTicketPreview();
  if (options.activateSignals !== false) activateView("signals");
  const label = options.label || "Ticket";
  setStatus(`${label} risk preview: ${payload.execution.next_status}.`, payload.risk.approved ? "ok" : "warn");
  return payload;
}

async function submitTicket() {
  const payload = ticketPayload();
  appState.selectedPair = compactSymbol(payload.symbol);
  const result = await api("/signals/submit", { method: "POST", body: payload });
  appState.lastPayload = result;
  setStatus(`Ticket submitted as ${payload.strategy_id}: ${result.status || "submitted"}.`, result.status === "rejected" ? "warn" : "ok");
  await loadState(false);
}

async function closePosition(symbol, quantity, price, strategy = "Close Position") {
  const payload = {
    symbol,
    side: "sell",
    base_amount: quantity,
    price,
    strategy_id: strategy,
  };
  const result = await api("/signals/submit", { method: "POST", body: payload });
  appState.lastPayload = result;
  setStatus(`Close ${prettySymbol(symbol)} ${quantity} ${baseAsset(symbol)}: ${result.status || "submitted"}.`, result.status === "rejected" ? "warn" : "ok");
  await loadState(false);
}

async function approveSignal(signalId) {
  const result = await api(`/approvals/${encodeURIComponent(signalId)}/approve`, { method: "POST" });
  appState.lastPayload = result;
  setStatus(`Approved ${signalId}: ${result.status}.`, "ok");
  await loadState(false);
}

async function rejectSignal(signalId) {
  const reason = $("#rejectReasonInput").value.trim() || "Rejected from operator UI";
  const result = await api(`/approvals/${encodeURIComponent(signalId)}/reject`, {
    method: "POST",
    body: { reason },
  });
  appState.lastPayload = result;
  setStatus(`Rejected ${signalId}: ${reason}.`, "warn");
  await loadState(false);
}

async function updateMarkPrice(symbol, price) {
  const result = await api("/market/price", {
    method: "POST",
    body: { symbol, price },
  });
  appState.lastPayload = result;
  appState.markPrices[result.symbol] = Number(result.price);
  setStatus(`Updated ${result.symbol} to ${result.price}; triggered ${result.triggered.length} exits.`, result.triggered.length ? "warn" : "ok");
  await loadState(false);
}

async function previewBracket(signalId, price) {
  const result = await api(`/brackets/${encodeURIComponent(signalId)}/preview`, {
    method: "POST",
    body: { price },
  });
  appState.lastPayload = result;
  setStatus(
    `Previewed ${signalId} at ${result.price}; would trigger ${result.would_trigger.length} exits.`,
    result.would_trigger.length ? "warn" : "ok",
  );
  renderSignals();
}

async function amendBracketStop(signalId, currentPrice) {
  const triggerPrice = window.prompt("New protective stop trigger price", currentPrice || "");
  if (!triggerPrice) {
    setStatus("Stop amendment canceled.", "warn");
    return;
  }
  const result = await api(`/brackets/${encodeURIComponent(signalId)}/stop`, {
    method: "POST",
    body: { trigger_price: triggerPrice, reason: "operator UI stop tighten" },
  });
  appState.lastPayload = result;
  setStatus(`Tightened ${signalId} stop to ${triggerPrice}.`, "ok");
  await loadState(false);
}

async function cancelBracket(signalId) {
  if (!window.confirm(`Cancel synthetic exits for ${signalId}? The paper position stays open.`)) {
    setStatus("Bracket cancellation aborted.", "warn");
    return;
  }
  const result = await api(`/brackets/${encodeURIComponent(signalId)}/cancel`, {
    method: "POST",
    body: { reason: "operator UI bracket cancel" },
  });
  appState.lastPayload = result;
  setStatus(`Canceled synthetic bracket exits for ${signalId}.`, "warn");
  await loadState(false);
}

async function haltTrading() {
  const reason = $("#haltReasonInput").value.trim() || "operator requested halt";
  const result = await api("/control/halt", { method: "POST", body: { reason } });
  appState.lastPayload = result;
  setStatus(`Trading halted: ${result.reason}.`, "warn");
  await loadState(false);
}

async function resumeTrading() {
  const result = await api("/control/resume", { method: "POST" });
  appState.lastPayload = result;
  setStatus("Trading resumed.", "ok");
  await loadState(false);
}

async function inspectExchange(exchangeId) {
  $("#capabilityTitle").textContent = exchangeId;
  $("#capabilityView").textContent = "Loading...";
  try {
    const payload = await api(`/exchanges/${encodeURIComponent(exchangeId)}/capabilities`);
    $("#capabilityView").textContent = JSON.stringify(payload.capabilities, null, 2);
    setStatus(`Loaded ${exchangeId} capabilities.`, "ok");
  } catch (error) {
    $("#capabilityView").textContent = JSON.stringify({ error: error.message }, null, 2);
    setStatus(`Capability lookup failed: ${error.message}`, "error");
  }
}

async function inspectPlatform(exchangeId) {
  $("#capabilityTitle").textContent = `${exchangeId} integration`;
  $("#capabilityView").textContent = "Loading...";
  try {
    const payload = await api(`/exchanges/${encodeURIComponent(exchangeId)}/integration`);
    $("#capabilityView").textContent = JSON.stringify(payload, null, 2);
    appState.lastPayload = payload;
    setStatus(`Loaded ${exchangeId} integration details.`, "ok");
  } catch (error) {
    $("#capabilityView").textContent = JSON.stringify({ error: error.message }, null, 2);
    setStatus(`Integration lookup failed: ${error.message}`, "error");
  }
}

async function loadBitunixTickers() {
  const symbols = $("#bitunixSymbols").value.trim();
  const path = `/exchanges/bitunix/futures/tickers${symbols ? `?symbols=${encodeURIComponent(symbols)}` : ""}`;
  $("#bitunixView").textContent = "Loading...";
  const payload = await api(path);
  $("#bitunixView").textContent = JSON.stringify(payload, null, 2);
  appState.lastPayload = payload;
  setStatus("Bitunix futures tickers loaded.", "ok");
}

async function loadBitunixAccount() {
  const marginCoin = $("#bitunixMarginCoin").value.trim() || "USDT";
  $("#bitunixView").textContent = "Loading...";
  const payload = await api(`/exchanges/bitunix/futures/account?margin_coin=${encodeURIComponent(marginCoin)}`);
  $("#bitunixView").textContent = JSON.stringify(payload, null, 2);
  appState.lastPayload = payload;
  setStatus("Bitunix futures account check completed.", "ok");
}

async function copyStrategy(strategyId) {
  const strategy = strategies.find((item) => item.id === strategyId);
  if (!strategy) return;
  setTicketStrategy(strategy.name);
  $("#ticketSymbol").value = strategy.pair;
  $("#ticketSide").value = "BUY";
  setTicketSizeMode("quote", strategy.amount);
  $("#ticketPrice").value = strategy.price;
  $("#ticketStop").value = strategy.stop;
  $("#ticketTakeProfit").value = strategy.takeProfit;
  $("#ticketTrailingStop").value = strategy.trailingStop || "";
  $("#ticketTrailActivation").value = strategy.trailingActivation || "";
  $("#ticketBreakeven").value = strategy.breakeven || "";
  $("#signalText").value = ticketToText();
  writeImportedStrategy(strategy);
  appState.selectedPair = strategy.pair;
  saveTicketDraft();
  renderStrategies();
  activateView("trading");
  $("#ticketStatus").textContent = "previewing";
  const preview = await previewTicket({ activateSignals: false, label: strategy.name });
  const status = String(preview.execution?.next_status || "unknown").replaceAll("_", " ");
  $("#ticketStatus").textContent = `risk: ${status}`;
}

function runBacktest(strategyId) {
  const strategy = strategies.find((item) => item.id === strategyId);
  if (!strategy) return;
  const points = Array.from({ length: 32 }, (_, index) => {
    const wave = Math.sin((index + strategy.name.length) * 0.55) * 9;
    return 50 + index * (strategy.type === "grid" ? 1.2 : 1.8) + wave;
  });
  const first = points[0] || 1;
  const last = points[points.length - 1] || first;
  let highWater = first;
  let maxDrawdown = 0;
  points.forEach((point) => {
    highWater = Math.max(highWater, point);
    const drawdown = highWater > 0 ? ((point - highWater) / highWater) * 100 : 0;
    maxDrawdown = Math.min(maxDrawdown, drawdown);
  });
  appState.backtests[strategyId] = {
    points,
    return_pct: ((last - first) / first) * 100,
    max_drawdown_pct: maxDrawdown,
    updated_at: new Date().toISOString(),
  };
  writeStoredBacktests(appState.backtests);
  renderStrategies();
  setStatus(`${strategy.name} backtest: ${percent(appState.backtests[strategyId].return_pct)} return, ${Math.abs(maxDrawdown).toFixed(2)}% drawdown.`, "ok");
}

function toggleStrategyPin(strategyId) {
  const strategy = strategies.find((item) => item.id === strategyId);
  if (!strategy) return;
  const pinned = readPinnedStrategies();
  const pinnedNow = !pinned.has(strategyId);
  if (pinnedNow) {
    pinned.add(strategyId);
  } else {
    pinned.delete(strategyId);
  }
  writePinnedStrategies(pinned);
  renderStrategies();
  setStatus(`${strategy.name} ${pinnedNow ? "pinned" : "unpinned"}.`, "ok");
}

function inspectOrder(orderId) {
  const order = (appState.data?.orders || []).find((item) => item.order_id === orderId);
  if (!order) return;
  $("#ticketSymbol").value = compactSymbol(order.symbol);
  $("#ticketSide").value = String(order.side || "buy").toUpperCase();
  setTicketSizeMode("quote", order.notional || "");
  $("#ticketPrice").value = order.price || "";
  $("#ticketStop").value = "";
  $("#ticketTakeProfit").value = "";
  $("#ticketTrailingStop").value = "";
  $("#ticketTrailActivation").value = "";
  $("#ticketBreakeven").value = "";
  $("#ticketStatus").textContent = `loaded ${orderId}`;
  saveTicketDraft();
  activateView("trading");
}

function loadSignalTicket(signalId) {
  const signal = (appState.data?.signals || []).find((item) => item.signal_id === signalId);
  if (!signal) return;
  loadSignalIntoTicket(signal, `loaded ${signal.signal_id}`);
  setStatus(`Loaded ${prettySymbol(signal.symbol)} signal into the ticket.`, "ok");
}

function loadApprovalTicket(signalId) {
  const signal = (appState.data?.approvals || []).find((item) => item.signal_id === signalId);
  if (!signal) return;
  loadSignalIntoTicket(signal, `approval ${signal.signal_id}`);
  setStatus(`Loaded ${prettySymbol(signal.symbol)} approval into the ticket.`, "ok");
}

function loadSignalIntoTicket(signal, status) {
  setTicketStrategy(signal.strategy_id || "manual");
  $("#ticketSymbol").value = compactSymbol(signal.symbol);
  $("#ticketSide").value = String(signal.side || "buy").toUpperCase();
  setTicketSizeMode(signal.base_amount ? "base" : "quote", signal.base_amount || signal.quote_amount || "");
  $("#ticketPrice").value = signal.price || "";
  $("#ticketStop").value = signal.stop_loss_pct || "";
  $("#ticketTakeProfit").value = signal.take_profit_pct || "";
  $("#ticketTrailingStop").value = signal.trailing_stop_pct || "";
  $("#ticketTrailActivation").value = signal.trailing_activation_pct || "";
  $("#ticketBreakeven").value = signal.breakeven_trigger_pct || "";
  $("#ticketStatus").textContent = status;
  appState.selectedPair = compactSymbol(signal.symbol);
  saveTicketDraft();
  activateView("trading");
}

async function previewSignalTicket(signalId) {
  loadSignalTicket(signalId);
  const signal = (appState.data?.signals || []).find((item) => item.signal_id === signalId);
  const label = signal?.strategy_id || signalId;
  const preview = await previewTicket({ activateSignals: false, label });
  const status = String(preview.execution?.next_status || "unknown").replaceAll("_", " ");
  $("#ticketStatus").textContent = `risk: ${status}`;
}

async function previewApprovalTicket(signalId) {
  loadApprovalTicket(signalId);
  const signal = (appState.data?.approvals || []).find((item) => item.signal_id === signalId);
  const label = signal?.strategy_id || signalId;
  const preview = await previewTicket({ activateSignals: false, label });
  const status = String(preview.execution?.next_status || "unknown").replaceAll("_", " ");
  $("#ticketStatus").textContent = `risk: ${status}`;
}

function loadAuditRelated(orderId, signalId) {
  if (orderId) {
    inspectOrder(orderId);
    return;
  }
  if (signalId && (appState.data?.signals || []).some((item) => item.signal_id === signalId)) {
    loadSignalTicket(signalId);
    return;
  }
  if (signalId && (appState.data?.approvals || []).some((item) => item.signal_id === signalId)) {
    loadApprovalTicket(signalId);
    return;
  }
  setStatus("No related order or signal is available in the current UI state.", "warn");
}

function activateView(viewName) {
  appState.activeView = viewName;
  $$(".view").forEach((view) => view.classList.toggle("is-active", view.dataset.view === viewName));
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === viewName));
  history.replaceState(null, "", `#${viewName}`);
  drawAllCharts();
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width || canvas.width));
  const height = Math.max(1, Math.floor(width * (canvas.height / canvas.width)));
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

function drawGrid(ctx, width, height) {
  ctx.strokeStyle = "rgba(154, 168, 187, 0.12)";
  ctx.lineWidth = 1;
  for (let x = 0; x <= width; x += width / 8) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = 0; y <= height; y += height / 5) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
}

function linePath(ctx, points, width, height, color, fill) {
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = (index / (points.length - 1)) * width;
    const y = height - ((point - min) / range) * (height * 0.72) - height * 0.14;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();
  if (fill) {
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  }
}

function drawRiskRing() {
  const canvas = $("#riskRing");
  if (!canvas) return;
  const { ctx, width, height } = setupCanvas(canvas);
  const usage = Number($("#riskPercent").textContent.replace("%", "")) / 100;
  const centerX = width / 2;
  const centerY = height / 2;
  const radius = Math.min(width, height) * 0.35;
  ctx.clearRect(0, 0, width, height);
  ctx.lineWidth = 18;
  ctx.strokeStyle = "rgba(154, 168, 187, 0.16)";
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.strokeStyle = usage > 0.8 ? "#ffbe3d" : "#28d8a1";
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * usage);
  ctx.stroke();
}

function drawMainChart() {
  const canvas = $("#mainChart");
  if (!canvas) return;
  const { ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  drawGrid(ctx, width, height);
  const seed = appState.selectedPair.length + appState.timeframe.length;
  const values = Array.from({ length: 42 }, (_, index) => 60 + index * 0.56 + Math.sin((index + seed) * 0.52) * 8 + Math.cos(index * 0.21) * 5);
  const max = Math.max(...values) + 4;
  const min = Math.min(...values) - 4;
  const candleWidth = Math.max(5, width / values.length - 6);
  values.forEach((value, index) => {
    const open = values[Math.max(0, index - 1)] + ((index % 4) - 1.5);
    const close = value;
    const high = Math.max(open, close) + 1.8 + (index % 3);
    const low = Math.min(open, close) - 1.8 - (index % 2);
    const x = (index / values.length) * width + 8;
    const map = (v) => height - ((v - min) / (max - min)) * (height - 44) - 20;
    const bullish = close >= open;
    ctx.strokeStyle = bullish ? "#28d8a1" : "#ff5470";
    ctx.fillStyle = ctx.strokeStyle;
    ctx.beginPath();
    ctx.moveTo(x + candleWidth / 2, map(high));
    ctx.lineTo(x + candleWidth / 2, map(low));
    ctx.stroke();
    ctx.fillRect(x, Math.min(map(open), map(close)), candleWidth, Math.max(3, Math.abs(map(open) - map(close))));
  });
  const ma = values.map((_, i) => values.slice(Math.max(0, i - 5), i + 1).reduce((sum, item) => sum + item, 0) / (Math.min(i, 5) + 1));
  linePath(ctx, ma, width, height, "#ffbe3d");
}

function drawAllocationChart() {
  const canvas = $("#allocationChart");
  if (!canvas) return;
  const { ctx, width, height } = setupCanvas(canvas);
  const centerX = width / 2;
  const centerY = height / 2;
  const radius = Math.min(width, height) * 0.34;
  const positions = appState.data?.positions || [];
  const values = positions.length
    ? positions.map((position) => Math.abs(Number(position.quantity)) * Number(position.avg_entry || 0))
    : [36, 24, 18, 14, 8];
  const total = values.reduce((sum, value) => sum + value, 0) || 1;
  const colors = ["#ffbe3d", "#27d9ef", "#28d8a1", "#a678ff", "#ff5470"];
  let start = -Math.PI / 2;
  ctx.clearRect(0, 0, width, height);
  values.forEach((value, index) => {
    const size = value / total;
    ctx.strokeStyle = colors[index % colors.length];
    ctx.lineWidth = 24;
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, start, start + Math.PI * 2 * size - 0.04);
    ctx.stroke();
    start += Math.PI * 2 * size;
  });
  ctx.fillStyle = "#edf4ff";
  ctx.font = "700 26px Cascadia Mono, Consolas, monospace";
  ctx.textAlign = "center";
  ctx.fillText(money(appState.data?.account?.open_notional || 0, { digits: 0 }), centerX, centerY - 2);
  ctx.fillStyle = "#9cabc0";
  ctx.font = "14px Bahnschrift, Aptos, Segoe UI, sans-serif";
  ctx.fillText("open", centerX, centerY + 24);
}

function drawEquityChart() {
  const canvas = $("#equityChart");
  if (!canvas) return;
  const { ctx, width, height } = setupCanvas(canvas);
  const orders = appState.data?.orders || [];
  const base = 100;
  const points = Array.from({ length: 28 }, (_, index) => base + index * 1.6 + Math.sin(index * 0.7) * 5 + orders.length * 0.8);
  ctx.clearRect(0, 0, width, height);
  drawGrid(ctx, width, height);
  linePath(ctx, points, width, height, "#28d8a1", "rgba(40, 216, 161, 0.14)");
  linePath(ctx, points.map((point, index) => point - 6 - Math.sin(index) * 4), width, height, "#ff5470");
}

function drawPnlBars() {
  const canvas = $("#pnlBars");
  if (!canvas) return;
  const { ctx, width, height } = setupCanvas(canvas);
  const daily = Number(appState.data?.account?.daily_pnl || 0);
  const values = [12, -9, 16, 22, -4, 30, 11, 44, daily, 18, 29, 35, -7, 42];
  ctx.clearRect(0, 0, width, height);
  const zeroY = height * 0.58;
  ctx.strokeStyle = "rgba(154, 168, 187, 0.16)";
  ctx.beginPath();
  ctx.moveTo(0, zeroY);
  ctx.lineTo(width, zeroY);
  ctx.stroke();
  const barWidth = width / values.length - 7;
  values.forEach((value, index) => {
    const barHeight = Math.abs(value) / 50 * (height * 0.48);
    ctx.fillStyle = value >= 0 ? "#28d8a1" : "#ff5470";
    ctx.fillRect(index * (barWidth + 7), value >= 0 ? zeroY - barHeight : zeroY, barWidth, Math.max(2, barHeight));
  });
}

function drawStrategySparks() {
  $$("[data-strategy-spark]").forEach((canvas) => {
    const id = canvas.dataset.strategySpark;
    const strategy = strategies.find((item) => item.id === id);
    const storedBacktest = appState.backtests[id];
    const points = storedBacktest?.points || storedBacktest || Array.from({ length: 28 }, (_, index) => 42 + Math.sin((index + strategy.name.length) * 0.62) * 10 + index * 1.4);
    const { ctx, width, height } = setupCanvas(canvas);
    ctx.clearRect(0, 0, width, height);
    linePath(ctx, points, width, height, "#27d9ef", "rgba(39, 217, 239, 0.14)");
  });
}

function drawAllCharts() {
  requestAnimationFrame(() => {
    drawRiskRing();
    drawMainChart();
    drawAllocationChart();
    drawEquityChart();
    drawPnlBars();
    drawStrategySparks();
  });
}

function exportState() {
  const blob = new Blob([JSON.stringify(appState.data || {}, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `auto-crypto-state-${new Date().toISOString().replaceAll(":", "-")}.json`;
  link.click();
  URL.revokeObjectURL(url);
  setStatus("Exported current UI state JSON.", "ok");
}

function exportAuditCsv() {
  const events = filteredAuditEvents();
  const rows = [
    ["created_at", "event_type", "data_json"],
    ...events.map((event) => [event.created_at || "", event.event_type, JSON.stringify(event.data)]),
  ];
  const csv = `${rows.map((row) => row.map(csvCell).join(",")).join("\n")}\n`;
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `auto-crypto-audit-${new Date().toISOString().replaceAll(":", "-")}.csv`;
  link.click();
  URL.revokeObjectURL(url);
  setStatus(`Exported ${events.length} audit events to CSV.`, "ok");
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    setStatus("Copied to clipboard.", "ok");
  } else {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.inset = "0 auto auto 0";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    setStatus(copied ? "Copied to clipboard." : "Clipboard copy unavailable in this browser.", copied ? "ok" : "warn");
  }
}

function bindEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => activateView(item.dataset.view)));
  $("#refreshButton").addEventListener("click", loadState);
  $("#autoRefreshButton").addEventListener("click", () => {
    setAutoRefresh($("#autoRefreshButton").getAttribute("aria-pressed") !== "true");
  });
  $("#haltButton").addEventListener("click", () => haltTrading().catch((error) => setStatus(error.message, "error")));
  $("#resumeButton").addEventListener("click", () => resumeTrading().catch((error) => setStatus(error.message, "error")));
  $("#sampleSignalButton").addEventListener("click", () => {
    $("#signalText").value = `BUY ${appState.selectedPair} $250 @ ${Math.round(currentMarkPrice(appState.selectedPair))} SL 2% TP 4.5%`;
    setStatus("Sample signal loaded.", "ok");
  });
  $("#parseSignalButton").addEventListener("click", () => parseSignal().catch((error) => setStatus(error.message, "error")));
  $("#previewSignalButton").addEventListener("click", () => previewSignal($("#signalText").value).catch((error) => setStatus(error.message, "error")));
  $("#submitSignalButton").addEventListener("click", () => submitSignal($("#signalText").value).catch((error) => setStatus(error.message, "error")));
  $("#copyPayloadButton").addEventListener("click", () => copyText($("#payloadPreview").textContent).catch((error) => setStatus(error.message, "error")));
  $("#copyCapabilityButton").addEventListener("click", () => copyText($("#capabilityView").textContent).catch((error) => setStatus(error.message, "error")));
  $("#copyBitunixButton").addEventListener("click", () => copyText($("#bitunixView").textContent).catch((error) => setStatus(error.message, "error")));
  $("#copyTicketAlertButton").addEventListener("click", () => copyTicketAlert().catch((error) => setStatus(error.message, "error")));
  $("#copyTicketJsonButton").addEventListener("click", () => copyTicketJson().catch((error) => setStatus(error.message, "error")));
  $("#clearTicketDraftButton").addEventListener("click", clearTicketDraft);
  $("#buildTicketButton").addEventListener("click", () => {
    $("#signalText").value = ticketToText();
    saveTicketDraft();
    activateView("signals");
    setStatus("Ticket converted into Signal Forge text.", "ok");
  });
  $("#previewTicketButton").addEventListener("click", () => previewTicket().catch((error) => setStatus(error.message, "error")));
  $("#submitTicketButton").addEventListener("click", () => submitTicket().catch((error) => setStatus(error.message, "error")));
  $("#signalChannel").addEventListener("change", () => {
    appState.lastPayload = { channel: $("#signalChannel").value, message: $("#signalText").value };
    renderSignals();
    setStatus(`Signal channel set to ${$("#signalChannel").value}.`, "ok");
  });
  $("#ticketSizeMode").addEventListener("change", () => {
    setTicketSizeMode($("#ticketSizeMode").value);
    saveTicketDraft();
    setStatus(`Ticket size mode set to ${$("#ticketAmountLabel").textContent.toLowerCase()}.`, "ok");
  });
  $("#ticketStrategy").addEventListener("change", () => {
    saveTicketDraft();
    setStatus(`Ticket strategy set to ${$("#ticketStrategy").value}.`, "ok");
  });
  $$("[data-size-preset]").forEach((button) => {
    button.addEventListener("click", () => applySizePreset(button.dataset.sizePreset));
  });
  ["ticketSymbol", "ticketSide", "ticketAmount", "ticketPrice", "ticketStop", "ticketTakeProfit", "ticketTrailingStop", "ticketTrailActivation", "ticketBreakeven"].forEach((id) => {
    $(`#${id}`).addEventListener("input", saveTicketDraft);
    $(`#${id}`).addEventListener("change", saveTicketDraft);
  });
  $("#updatePriceButton").addEventListener("click", () => {
    appState.selectedPair = compactSymbol($("#ticketSymbol").value || appState.selectedPair);
    updateMarkPrice(appState.selectedPair, $("#markPrice").value).catch((error) => setStatus(error.message, "error"));
  });
  $("#exportStateButton").addEventListener("click", exportState);
  $("#exportAuditButton").addEventListener("click", exportAuditCsv);
  $("#refreshExchangesButton").addEventListener("click", async () => {
    await loadExchanges(true);
    await loadPlatforms(true);
    renderExchanges();
    renderDashboard();
  });
  $("#refreshAuditButton").addEventListener("click", loadState);
  $("#exchangeSearch").addEventListener("input", renderExchanges);
  $("#auditSearch").addEventListener("input", renderAudit);
  $("#signalSearch").addEventListener("input", renderSignalHistory);
  $("#deskSearch").addEventListener("input", (event) => {
    appState.deskSearch = event.target.value;
    renderDeskTable();
  });
  $("#bitunixTickerButton").addEventListener("click", () => loadBitunixTickers().catch((error) => {
    $("#bitunixView").textContent = JSON.stringify({ error: error.message }, null, 2);
    setStatus(`Bitunix ticker check failed: ${error.message}`, "error");
  }));
  $("#bitunixAccountButton").addEventListener("click", () => loadBitunixAccount().catch((error) => {
    $("#bitunixView").textContent = JSON.stringify({ error: error.message }, null, 2);
    setStatus(`Bitunix account check failed: ${error.message}`, "error");
  }));

  $("[data-view='dashboard']").addEventListener("click", (event) => {
    const button = event.target.closest("[data-runtime-filter]");
    if (!button) return;
    appState.runtimeFilter = button.dataset.runtimeFilter;
    $$("[data-runtime-filter]").forEach((item) => item.classList.toggle("is-selected", item === button));
    renderDashboard();
  });

  $$(".pair-selector [data-pair]").forEach((button) => {
    button.addEventListener("click", () => {
      appState.selectedPair = button.dataset.pair;
      $("#ticketSymbol").value = button.dataset.pair;
      $("#ticketPrice").value = Math.round(currentMarkPrice(button.dataset.pair));
      saveTicketDraft();
      renderTradingDesk();
      drawMainChart();
      setStatus(`${prettySymbol(appState.selectedPair)} selected.`, "ok");
    });
  });

  $$("[data-timeframe]").forEach((button) => {
    button.addEventListener("click", () => {
      appState.timeframe = button.dataset.timeframe;
      $$("[data-timeframe]").forEach((item) => item.classList.toggle("is-selected", item === button));
      drawMainChart();
    });
  });

  $$("[data-desk-table]").forEach((button) => {
    button.addEventListener("click", () => {
      appState.deskTable = button.dataset.deskTable;
      $$("[data-desk-table]").forEach((item) => item.classList.toggle("is-selected", item === button));
      renderDeskTable();
    });
  });

  $$("[data-strategy-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      appState.strategyFilter = button.dataset.strategyFilter;
      $$("[data-strategy-filter]").forEach((item) => item.classList.toggle("is-selected", item === button));
      renderStrategies();
    });
  });

  $("#strategySearch").addEventListener("input", (event) => {
    appState.strategySearch = event.target.value;
    renderStrategies();
  });

  $("#strategySort").addEventListener("change", (event) => {
    appState.strategySort = event.target.value;
    renderStrategies();
  });

  $$("[data-equity-range]").forEach((button) => {
    button.addEventListener("click", () => {
      appState.equityRange = button.dataset.equityRange;
      $$("[data-equity-range]").forEach((item) => item.classList.toggle("is-selected", item === button));
      drawEquityChart();
      setStatus(`Portfolio range set to ${appState.equityRange.toUpperCase()}.`, "ok");
    });
  });

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;
    if (action === "nav") activateView(target.dataset.target);
    if (action === "approve") approveSignal(target.dataset.signalId).catch((error) => setStatus(error.message, "error"));
    if (action === "reject") rejectSignal(target.dataset.signalId).catch((error) => setStatus(error.message, "error"));
    if (action === "inspect-order") inspectOrder(target.dataset.orderId);
    if (action === "load-signal-ticket") loadSignalTicket(target.dataset.signalId);
    if (action === "preview-signal-ticket") previewSignalTicket(target.dataset.signalId).catch((error) => setStatus(error.message, "error"));
    if (action === "preview-approval-ticket") previewApprovalTicket(target.dataset.signalId).catch((error) => setStatus(error.message, "error"));
    if (action === "load-audit-related") loadAuditRelated(target.dataset.orderId, target.dataset.signalId);
    if (action === "load-position-price") {
      appState.selectedPair = target.dataset.symbol;
      $("#ticketSymbol").value = target.dataset.symbol;
      $("#markPrice").value = target.dataset.price;
      activateView("trading");
    }
    if (action === "trigger-exit-price") {
      updateMarkPrice(target.dataset.symbol, target.dataset.price)
        .catch((error) => setStatus(error.message, "error"));
    }
    if (action === "preview-bracket") {
      previewBracket(target.dataset.signalId, target.dataset.price)
        .catch((error) => setStatus(error.message, "error"));
    }
    if (action === "amend-bracket-stop") {
      amendBracketStop(target.dataset.signalId, target.dataset.price)
        .catch((error) => setStatus(error.message, "error"));
    }
    if (action === "cancel-bracket") {
      cancelBracket(target.dataset.signalId)
        .catch((error) => setStatus(error.message, "error"));
    }
    if (action === "close-position") {
      closePosition(target.dataset.symbol, target.dataset.quantity, target.dataset.price, target.dataset.closeLabel)
        .catch((error) => setStatus(error.message, "error"));
    }
    if (action === "exchange-cap") inspectExchange(target.dataset.exchangeId);
    if (action === "platform-integration") inspectPlatform(target.dataset.exchangeId);
    if (action === "copy-strategy") copyStrategy(target.dataset.strategyId).catch((error) => {
      $("#ticketStatus").textContent = "preview failed";
      setStatus(`Strategy load failed: ${error.message}`, "error");
    });
    if (action === "backtest-strategy") runBacktest(target.dataset.strategyId);
    if (action === "toggle-strategy-pin") toggleStrategyPin(target.dataset.strategyId);
    if (action === "copy-json") copyText(target.dataset.json).catch((error) => setStatus(error.message, "error"));
  });

  window.addEventListener("resize", drawAllCharts);
  window.addEventListener("hashchange", () => activateView(location.hash.slice(1) || "dashboard"));
}

bindEvents();
applyStoredTicketDraft();
restoreAutoRefresh();
activateView(location.hash.slice(1) || "dashboard");
loadState();
