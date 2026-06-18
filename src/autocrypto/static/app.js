const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const defaultMarkets = [
  { symbol: "BTC/USDT", compact: "BTCUSDT", price: 66234.13, change: 3.15 },
  { symbol: "ETH/USDT", compact: "ETHUSDT", price: 3421.9, change: 1.42 },
  { symbol: "SOL/USDT", compact: "SOLUSDT", price: 148.26, change: -0.68 },
  { symbol: "USDC Drift", compact: "USDC", price: 0.04, change: 0, suffix: "%" },
];

const strategies = [
  {
    id: "breakout-guard",
    name: "Breakout Guard",
    type: "signal",
    pair: "BTCUSDT",
    amount: "250",
    price: "66234",
    stop: "2",
    takeProfit: "4.5",
    roi: "+229.35%",
    win: "86.44%",
    drawdown: "8.2%",
  },
  {
    id: "mean-grid-18",
    name: "Mean Grid 18",
    type: "grid",
    pair: "ETHUSDT",
    amount: "150",
    price: "3421",
    stop: "2.5",
    takeProfit: "5",
    roi: "+110.11%",
    win: "74.93%",
    drawdown: "5.9%",
  },
  {
    id: "dca-ladder",
    name: "DCA Ladder",
    type: "dca",
    pair: "SOLUSDT",
    amount: "90",
    price: "148",
    stop: "3",
    takeProfit: "7",
    roi: "+64.28%",
    win: "80.95%",
    drawdown: "6.7%",
  },
];

const appState = {
  data: null,
  exchanges: [],
  platforms: [],
  activeView: "dashboard",
  selectedPair: "BTCUSDT",
  timeframe: "15m",
  deskTable: "positions",
  runtimeFilter: "all",
  strategyFilter: "all",
  equityRange: "1d",
  parsedSignal: null,
  riskPreview: null,
  lastPayload: null,
  backtests: {},
  selectedExchange: null,
};

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

function coinClass(symbol) {
  const compact = compactSymbol(symbol);
  if (compact.startsWith("BTC")) return "btc";
  if (compact.startsWith("ETH")) return "eth";
  return "sol";
}

function setStatus(message, type = "") {
  const el = $("#statusLine");
  el.textContent = message;
  el.className = `status-line ${type}`.trim();
}

async function api(path, options = {}) {
  const request = {
    method: options.method || "GET",
    headers: { ...(options.headers || {}) },
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

async function loadState() {
  try {
    appState.data = await api("/ui/state");
    await loadExchanges(false);
    await loadPlatforms(false);
    renderAll();
    setStatus("State refreshed from Auto-Crypto API.", "ok");
  } catch (error) {
    setStatus(`Unable to load API state: ${error.message}`, "error");
  }
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

function renderShell() {
  const data = appState.data || {};
  const halted = Boolean(data.control?.halted);
  $("#railMode").textContent = halted ? "Halted" : "Armed";
  $("#railMode").className = halted ? "red" : "green";
  $("#lastRefresh").textContent = new Date().toLocaleTimeString();
  $("#haltButton").disabled = halted;
  $("#resumeButton").disabled = !halted;
  $("#webhookPill").textContent = halted ? "Webhook intake halted" : "Webhook intake live";
  $("#approvalPill").textContent = `${(data.approvals || []).length} approvals`;
}

function renderMarketStrip() {
  const latestBySymbol = new Map();
  for (const order of appState.data?.orders || []) {
    if (order.price) latestBySymbol.set(order.symbol, Number(order.price));
  }
  $("#marketStrip").innerHTML = defaultMarkets
    .map((market) => {
      const price = latestBySymbol.get(market.symbol) ?? market.price;
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
      : `<tr><td colspan="6">No paper orders yet.</td></tr>`;

  $("#auditPreview").innerHTML =
    audit.length > 0
      ? audit.slice(-4).reverse().map((event) => `<li><strong>${escapeHtml(event.event_type)}</strong><span>${escapeHtml(JSON.stringify(event.data))}</span></li>`).join("")
      : `<li><strong>empty</strong><span>No audit events yet.</span></li>`;
}

function signalRow(signal, label = "needs approval") {
  const symbol = signal.symbol || "UNKNOWN";
  const size = signal.quote_amount ? `$${signal.quote_amount}` : signal.base_amount || "size?";
  const price = signal.price ? ` @ ${signal.price}` : "";
  const actionButtons = label === "needs approval"
    ? `<div class="row-actions"><button type="button" data-action="approve" data-signal-id="${escapeHtml(signal.signal_id)}">Approve</button><button type="button" data-action="reject" data-signal-id="${escapeHtml(signal.signal_id)}">Reject</button></div>`
    : `<em>${escapeHtml(label)}</em>`;
  return `
    <article class="signal-row ${label === "needs approval" ? "priority" : ""}">
      <div class="coin-dot ${coinClass(symbol)}">${escapeHtml(symbol[0] || "?")}</div>
      <div><strong>${escapeHtml(String(signal.side || "").toUpperCase())} ${escapeHtml(symbol)}</strong><span>${escapeHtml(signal.strategy_id || signal.source || "manual")} | ${escapeHtml(size)}${escapeHtml(price)}</span></div>
      ${actionButtons}
    </article>
  `;
}

function orderRuntimeRow(order) {
  return `
    <tr>
      <td title="${escapeHtml(order.order_id)}">${escapeHtml(order.order_id)}</td>
      <td>${escapeHtml(order.symbol)}</td>
      <td class="${order.side === "buy" ? "up" : "down"}">${escapeHtml(order.side)}</td>
      <td>${money(order.notional)}</td>
      <td>${order.price ? money(order.price) : "market"}</td>
      <td><button type="button" data-action="inspect-order" data-order-id="${escapeHtml(order.order_id)}">Inspect</button></td>
    </tr>
  `;
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
      ["signal id", signal.signal_id],
    ]
      .map(([label, value]) => `<span>${escapeHtml(label)}<strong>${escapeHtml(value)}</strong></span>`)
      .join("");
  }
  renderRiskPreview();
  $("#payloadPreview").textContent = JSON.stringify(appState.lastPayload || appState.parsedSignal || {}, null, 2);
}

function renderRiskPreview() {
  const preview = appState.riskPreview;
  if (!preview) {
    $("#riskPreview").innerHTML = `<div class="empty-state">Preview risk to see server-side checks before submission.</div>`;
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
}

function renderTradingDesk() {
  $("#chartTitle").textContent = `${prettySymbol(appState.selectedPair)} Context`;
  const mark = currentMarkPrice(appState.selectedPair);
  $("#markPrice").value = String(mark.toFixed(mark > 1000 ? 0 : 2));
  renderOrderBook(mark);
  renderDeskTable();
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
    $("#deskTableHead").innerHTML = `<tr><th>Order</th><th>Pair</th><th>Side</th><th>Notional</th><th>Price</th><th>Status</th></tr>`;
    const orders = appState.data?.orders || [];
    $("#deskTableBody").innerHTML =
      orders.length > 0
        ? orders.slice(-12).reverse().map((order) => `<tr><td>${escapeHtml(order.order_id)}</td><td>${escapeHtml(order.symbol)}</td><td>${escapeHtml(order.side)}</td><td>${money(order.notional)}</td><td>${order.price ? money(order.price) : "market"}</td><td>${escapeHtml(order.status)}</td></tr>`).join("")
        : `<tr><td colspan="6">No orders submitted yet.</td></tr>`;
    return;
  }

  $("#deskTableHead").innerHTML = `<tr><th>Pair</th><th>Quantity</th><th>Average Entry</th><th>Realized P&L</th><th>Mark</th><th>Action</th></tr>`;
  const positions = appState.data?.positions || [];
  $("#deskTableBody").innerHTML =
    positions.length > 0
      ? positions.map((position) => {
        const mark = currentMarkPrice(compactSymbol(position.symbol));
        return `<tr><td>${escapeHtml(position.symbol)}</td><td>${escapeHtml(position.quantity)}</td><td>${money(position.avg_entry)}</td><td class="${Number(position.realized_pnl) >= 0 ? "up" : "down"}">${money(position.realized_pnl)}</td><td>${money(mark)}</td><td><button type="button" data-action="load-position-price" data-symbol="${escapeHtml(compactSymbol(position.symbol))}" data-price="${mark}">Use Mark</button></td></tr>`;
      }).join("")
      : `<tr><td colspan="6">No open positions. Submit a paper buy with a price to create one.</td></tr>`;
}

function renderStrategies() {
  const visible = strategies.filter((strategy) => appState.strategyFilter === "all" || strategy.type === appState.strategyFilter);
  $("#strategyCards").innerHTML = visible.map(strategyCard).join("");
  const imported = JSON.parse(localStorage.getItem("autoCryptoImportedStrategy") || "null");
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

function strategyCard(strategy) {
  return `
    <article class="strategy-card">
      <div class="strategy-head">
        <strong>${escapeHtml(strategy.name)}</strong>
        <span>${escapeHtml(strategy.type.toUpperCase())} | ${prettySymbol(strategy.pair)}</span>
        <button type="button" data-action="copy-strategy" data-strategy-id="${strategy.id}">Copy</button>
      </div>
      <canvas data-strategy-spark="${strategy.id}" width="320" height="96" aria-label="${escapeHtml(strategy.name)} backtest curve"></canvas>
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

function renderPortfolio() {
  const account = appState.data?.account || {};
  $("#navAmount").textContent = `${money(account.equity || 0)} paper NAV`;
  const dailyPnl = Number(account.daily_pnl || 0);
  $("#dailyPnl").textContent = money(dailyPnl);
  $("#dailyPnl").className = `big-number ${dailyPnl >= 0 ? "up" : "down"}`;

  const positions = appState.data?.positions || [];
  const maxOpen = Number(appState.data?.risk?.max_open_notional || 0) || Number(account.equity || 10000);
  const grouped = positions.length > 0
    ? positions.map((position) => ({ symbol: position.symbol, value: Number(position.quantity) * Number(position.avg_entry || 0) }))
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
      ? exits.map((exit) => `<tr><td>${escapeHtml(exit.symbol)}</td><td>${escapeHtml(exit.kind)}</td><td>${money(exit.trigger_price)}</td><td><button type="button" data-action="load-position-price" data-symbol="${escapeHtml(compactSymbol(exit.symbol))}" data-price="${escapeHtml(exit.trigger_price)}">Test Trigger</button></td></tr>`).join("")
      : `<tr><td colspan="4">No active exits. Buy signals with stop/TP create bracket rows.</td></tr>`;
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
  const query = $("#auditSearch").value.trim().toLowerCase();
  const events = (appState.data?.audit || []).filter((event) => {
    const text = `${event.event_type} ${JSON.stringify(event.data)}`.toLowerCase();
    return !query || text.includes(query);
  });
  $("#auditRows").innerHTML =
    events.length > 0
      ? events.slice().reverse().map((event) => `<tr><td>${escapeHtml(event.event_type)}</td><td>${escapeHtml(JSON.stringify(event.data))}</td><td><button type="button" data-action="copy-json" data-json="${escapeHtml(JSON.stringify(event))}">Copy</button></td></tr>`).join("")
      : `<tr><td colspan="3">No audit events match.</td></tr>`;
}

function currentMarkPrice(symbol) {
  const pretty = prettySymbol(symbol);
  const fromOrder = [...(appState.data?.orders || [])].reverse().find((order) => order.symbol === pretty && order.price);
  if (fromOrder) return Number(fromOrder.price);
  const market = defaultMarkets.find((item) => item.compact === compactSymbol(symbol));
  return Number(market?.price || 100);
}

function ticketToText() {
  const side = $("#ticketSide").value.toUpperCase();
  const symbol = compactSymbol($("#ticketSymbol").value || appState.selectedPair);
  const amount = String($("#ticketAmount").value || "0").replace("$", "");
  const price = $("#ticketPrice").value || currentMarkPrice(symbol);
  const stop = $("#ticketStop").value;
  const takeProfit = $("#ticketTakeProfit").value;
  const stopPart = stop ? ` SL ${stop}%` : "";
  const tpPart = takeProfit ? ` TP ${takeProfit}%` : "";
  return `${side} ${symbol} $${amount} @ ${price}${stopPart}${tpPart}`;
}

function ticketPayload() {
  return {
    symbol: $("#ticketSymbol").value,
    side: $("#ticketSide").value.toLowerCase(),
    quote_amount: $("#ticketAmount").value,
    price: $("#ticketPrice").value,
    stop_loss_pct: $("#ticketStop").value || null,
    take_profit_pct: $("#ticketTakeProfit").value || null,
    strategy_id: $("#ticketStrategy").value,
  };
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
  await loadState();
}

async function previewTicket() {
  const payload = await api("/signals/preview", { method: "POST", body: ticketPayload() });
  appState.parsedSignal = payload.signal;
  appState.riskPreview = payload;
  appState.lastPayload = payload;
  renderSignals();
  activateView("signals");
  setStatus(`Ticket risk preview: ${payload.execution.next_status}.`, payload.risk.approved ? "ok" : "warn");
  return payload;
}

async function submitTicket() {
  const payload = ticketPayload();
  const result = await api("/signals/submit", { method: "POST", body: payload });
  appState.lastPayload = result;
  setStatus(`Ticket submitted as ${payload.strategy_id}: ${result.status || "submitted"}.`, result.status === "rejected" ? "warn" : "ok");
  await loadState();
}

async function approveSignal(signalId) {
  const result = await api(`/approvals/${encodeURIComponent(signalId)}/approve`, { method: "POST" });
  appState.lastPayload = result;
  setStatus(`Approved ${signalId}: ${result.status}.`, "ok");
  await loadState();
}

async function rejectSignal(signalId) {
  const result = await api(`/approvals/${encodeURIComponent(signalId)}/reject`, {
    method: "POST",
    body: { reason: "Rejected from operator UI" },
  });
  appState.lastPayload = result;
  setStatus(`Rejected ${signalId}.`, "warn");
  await loadState();
}

async function updateMarkPrice(symbol, price) {
  const result = await api("/market/price", {
    method: "POST",
    body: { symbol, price },
  });
  appState.lastPayload = result;
  setStatus(`Updated ${result.symbol} to ${result.price}; triggered ${result.triggered.length} exits.`, result.triggered.length ? "warn" : "ok");
  await loadState();
}

async function haltTrading() {
  const reason = prompt("Halt reason", "operator requested halt") || "operator requested halt";
  const result = await api("/control/halt", { method: "POST", body: { reason } });
  appState.lastPayload = result;
  setStatus(`Trading halted: ${result.reason}.`, "warn");
  await loadState();
}

async function resumeTrading() {
  const result = await api("/control/resume", { method: "POST" });
  appState.lastPayload = result;
  setStatus("Trading resumed.", "ok");
  await loadState();
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

function copyStrategy(strategyId) {
  const strategy = strategies.find((item) => item.id === strategyId);
  if (!strategy) return;
  $("#ticketStrategy").value = strategy.name;
  $("#ticketSymbol").value = strategy.pair;
  $("#ticketSide").value = "BUY";
  $("#ticketAmount").value = strategy.amount;
  $("#ticketPrice").value = strategy.price;
  $("#ticketStop").value = strategy.stop;
  $("#ticketTakeProfit").value = strategy.takeProfit;
  $("#signalText").value = ticketToText();
  localStorage.setItem("autoCryptoImportedStrategy", JSON.stringify(strategy));
  appState.selectedPair = strategy.pair;
  renderStrategies();
  activateView("trading");
  setStatus(`${strategy.name} loaded into the trading desk.`, "ok");
}

function runBacktest(strategyId) {
  const strategy = strategies.find((item) => item.id === strategyId);
  if (!strategy) return;
  appState.backtests[strategyId] = Array.from({ length: 32 }, (_, index) => {
    const wave = Math.sin((index + strategy.name.length) * 0.55) * 9;
    return 50 + index * (strategy.type === "grid" ? 1.2 : 1.8) + wave;
  });
  drawStrategySparks();
  setStatus(`${strategy.name} backtest simulation refreshed.`, "ok");
}

function inspectOrder(orderId) {
  const order = (appState.data?.orders || []).find((item) => item.order_id === orderId);
  if (!order) return;
  $("#ticketSymbol").value = compactSymbol(order.symbol);
  $("#ticketSide").value = String(order.side || "buy").toUpperCase();
  $("#ticketAmount").value = order.notional || "";
  $("#ticketPrice").value = order.price || "";
  $("#ticketStop").value = "";
  $("#ticketTakeProfit").value = "";
  $("#ticketStatus").textContent = `loaded ${orderId}`;
  activateView("trading");
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
    ? positions.map((position) => Number(position.quantity) * Number(position.avg_entry || 0))
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
    const points = appState.backtests[id] || Array.from({ length: 28 }, (_, index) => 42 + Math.sin((index + strategy.name.length) * 0.62) * 10 + index * 1.4);
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

async function copyText(value) {
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(value);
    setStatus("Copied to clipboard.", "ok");
  } else {
    setStatus("Clipboard API unavailable in this browser.", "warn");
  }
}

function bindEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => activateView(item.dataset.view)));
  $("#refreshButton").addEventListener("click", loadState);
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
  $("#buildTicketButton").addEventListener("click", () => {
    $("#signalText").value = ticketToText();
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
  $("#ticketStrategy").addEventListener("change", () => {
    setStatus(`Ticket strategy set to ${$("#ticketStrategy").value}.`, "ok");
  });
  $("#updatePriceButton").addEventListener("click", () => updateMarkPrice(appState.selectedPair, $("#markPrice").value).catch((error) => setStatus(error.message, "error")));
  $("#exportStateButton").addEventListener("click", exportState);
  $("#refreshExchangesButton").addEventListener("click", async () => {
    await loadExchanges(true);
    await loadPlatforms(true);
    renderExchanges();
    renderDashboard();
  });
  $("#refreshAuditButton").addEventListener("click", loadState);
  $("#exchangeSearch").addEventListener("input", renderExchanges);
  $("#auditSearch").addEventListener("input", renderAudit);
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
    if (action === "load-position-price") {
      appState.selectedPair = target.dataset.symbol;
      $("#ticketSymbol").value = target.dataset.symbol;
      $("#markPrice").value = target.dataset.price;
      activateView("trading");
    }
    if (action === "exchange-cap") inspectExchange(target.dataset.exchangeId);
    if (action === "platform-integration") inspectPlatform(target.dataset.exchangeId);
    if (action === "copy-strategy") copyStrategy(target.dataset.strategyId);
    if (action === "backtest-strategy") runBacktest(target.dataset.strategyId);
    if (action === "copy-json") copyText(target.dataset.json).catch((error) => setStatus(error.message, "error"));
  });

  window.addEventListener("resize", drawAllCharts);
  window.addEventListener("hashchange", () => activateView(location.hash.slice(1) || "dashboard"));
}

bindEvents();
activateView(location.hash.slice(1) || "dashboard");
loadState();
