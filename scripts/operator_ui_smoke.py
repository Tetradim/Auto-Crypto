from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
REQUIRED_CANVASES = {"riskRing", "mainChart", "allocationChart", "equityChart", "pnlBars"}


class SmokeFailure(RuntimeError):
    """Raised when the operator UI smoke test detects a failure."""


def main() -> int:
    args = parse_args()
    server: subprocess.Popen[str] | None = None
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        port = args.port or find_free_port()
        base_url = f"http://{args.host}:{port}"
        if not args.reuse_server:
            temp_dir = tempfile.TemporaryDirectory(prefix="auto_crypto_ui_smoke_")
            db_path = Path(temp_dir.name) / "smoke.sqlite3"
            server = start_server(args.host, port, db_path)
            wait_for_health(base_url, timeout=args.server_timeout)

        summary = run_browser_smoke(
            base_url=base_url,
            browser_path=args.browser_path or os.getenv("AUTO_CRYPTO_BROWSER_PATH") or discover_browser_path(),
            headless=not args.show_browser,
            timeout_ms=args.timeout_ms,
        )
        print(json.dumps(summary, indent=2))
        return 0
    except SmokeFailure as exc:
        print(f"operator UI smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            stop_server(server)
        if temp_dir is not None:
            temp_dir.cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real-browser smoke pass against the Auto-Crypto operator UI.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Server bind host for the temporary app.")
    parser.add_argument("--port", type=int, default=0, help="Server port. Defaults to a free local port.")
    parser.add_argument("--reuse-server", action="store_true", help="Use an already running server at host/port.")
    parser.add_argument("--server-timeout", type=float, default=30.0, help="Seconds to wait for the temporary server.")
    parser.add_argument(
        "--browser-path",
        default="",
        help="Browser executable path. Defaults to AUTO_CRYPTO_BROWSER_PATH or a discovered Chrome/Edge install.",
    )
    parser.add_argument("--show-browser", action="store_true", help="Run with a visible browser instead of headless mode.")
    parser.add_argument("--timeout-ms", type=int, default=180_000, help="Overall Playwright action timeout.")
    return parser.parse_args()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_HOST, 0))
        return int(sock.getsockname()[1])


def start_server(host: str, port: int, db_path: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    pythonpath = str(ROOT / "src")
    env["PYTHONPATH"] = f"{pythonpath}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else pythonpath
    env["AUTO_CRYPTO_DB_PATH"] = str(db_path)
    env["AUTO_CRYPTO_REQUIRE_APPROVAL"] = "true"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "autocrypto.app:create_app_from_env",
            "--factory",
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def wait_for_health(base_url: str, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except URLError as exc:
            last_error = str(exc)
        except TimeoutError as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise SmokeFailure(f"server did not become ready at {base_url}; last error: {last_error}")


def stop_server(server: subprocess.Popen[str]) -> None:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


def discover_browser_path() -> str | None:
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    return None


def run_browser_smoke(*, base_url: str, browser_path: str | None, headless: bool, timeout_ms: int) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SmokeFailure(
            "Python Playwright is not installed. Run `python -m pip install -e .[dev]` first."
        ) from exc

    with sync_playwright() as playwright:
        launch_options: dict[str, Any] = {"headless": headless}
        if browser_path:
            launch_options["executable_path"] = browser_path
        browser = playwright.chromium.launch(**launch_options)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 1200}, accept_downloads=True)
            context.add_init_script(CLICK_AND_CLIPBOARD_SCRIPT)
            page = context.new_page()
            return exercise_operator_ui(page, base_url=base_url, timeout_ms=timeout_ms)
        finally:
            browser.close()


def exercise_operator_ui(page: Any, *, base_url: str, timeout_ms: int) -> dict[str, Any]:
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []

    page.on("console", lambda message: collect_console_message(message, console_errors, warnings))
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("requestfailed", lambda request: collect_failed_request(request, failed_requests))
    page.set_default_timeout(timeout_ms)

    driver = UiDriver(page=page, actions=actions)
    page.goto(f"{base_url}/ui", wait_until="networkidle")
    page.wait_for_function("() => document.querySelector('#statusLine')?.textContent.includes('State refreshed')")

    observations: dict[str, Any] = {
        "initialView": driver.active_view(),
        "checkboxCount": page.locator("input[type='checkbox']").count(),
        "navButtons": page.locator(".nav-item").count(),
    }

    for view in ["dashboard", "signals", "trading", "strategies", "portfolio", "exchanges", "audit"]:
        driver.nav(view)
    driver.click("#refreshButton", "topbar refresh")
    driver.click("#autoRefreshButton", "topbar auto refresh on")
    page.wait_for_function("() => document.querySelector('#autoRefreshButton')?.getAttribute('aria-pressed') === 'true'")
    driver.click("#autoRefreshButton", "topbar auto refresh off")
    page.wait_for_function("() => document.querySelector('#autoRefreshButton')?.getAttribute('aria-pressed') === 'false'")

    exercise_dashboard(driver)
    halted_approval_id = exercise_signal_builder_and_halt_flow(driver)
    exercise_signal_history(driver, halted_approval_id)
    close_approvals = exercise_trading_desk(driver)
    exercise_portfolio(driver)
    for signal_id in close_approvals:
        driver.approve_pending(signal_id)
    exercise_strategies(driver)
    exercise_exchanges(driver, warnings)
    exercise_audit(driver)

    driver.nav("dashboard")
    page.wait_for_timeout(500)
    final_state = driver.ui_state()
    observations.update(
        {
            "activeView": driver.active_view(),
            "closeApprovalCount": len(close_approvals),
            "clickedControls": page.evaluate("() => window.__clickedControls.length"),
            "copiedTexts": page.evaluate("() => window.__copiedTexts.length"),
            "finalOrders": len(final_state["orders"]),
            "finalSignals": len(final_state["signals"]),
            "finalAuditEvents": len(final_state["audit"]),
            "finalApprovals": len(final_state["approvals"]),
            "activeExits": len(final_state["active_exits"]),
            "canvasStats": canvas_stats(page),
        }
    )
    blank_required = [item["id"] for item in observations["canvasStats"] if item["id"] in REQUIRED_CANVASES and item["nonBlank"] == 0]
    if blank_required:
        raise SmokeFailure(f"required canvases were blank: {', '.join(blank_required)}")
    if page_errors:
        raise SmokeFailure(f"page errors: {page_errors}")
    if console_errors:
        raise SmokeFailure(f"console errors: {console_errors}")

    return {
        "ok": True,
        "baseUrl": base_url,
        "actions": len(actions),
        "observations": observations,
        "warnings": warnings,
        "failedRequests": failed_requests,
        "sampleActions": actions[:20],
        "lastActions": actions[-20:],
    }


class UiDriver:
    def __init__(self, *, page: Any, actions: list[str]) -> None:
        self.page = page
        self.actions = actions

    def record(self, label: str) -> None:
        self.actions.append(label)

    def settle(self, ms: int = 250) -> None:
        self.page.wait_for_timeout(ms)

    def click(self, selector: str, label: str, *, settle_ms: int = 250) -> None:
        locator = self.page.locator(selector).first
        locator.wait_for(state="visible")
        locator.scroll_into_view_if_needed()
        locator.click()
        self.record(label)
        self.settle(settle_ms)

    def click_locator(self, locator: Any, label: str, *, settle_ms: int = 250) -> None:
        locator.wait_for(state="visible")
        locator.scroll_into_view_if_needed()
        locator.click()
        self.record(label)
        self.settle(settle_ms)

    def fill(self, selector: str, value: str, label: str) -> None:
        locator = self.page.locator(selector).first
        locator.wait_for(state="visible")
        locator.fill(value)
        self.record(label)
        self.settle(100)

    def select(self, selector: str, value: str | None = None, *, label_value: str | None = None, label: str) -> None:
        locator = self.page.locator(selector).first
        locator.wait_for(state="visible")
        if label_value is not None:
            locator.select_option(label=label_value)
        else:
            locator.select_option(value=value)
        self.record(label)
        self.settle(150)

    def active_view(self) -> str:
        return str(self.page.locator(".view.is-active").get_attribute("data-view"))

    def nav(self, view: str) -> None:
        self.click(f".nav-item[data-view='{view}']", f"nav {view}")
        self.page.wait_for_function(
            "(expected) => document.querySelector('.view.is-active')?.dataset.view === expected",
            arg=view,
        )

    def ui_state(self) -> dict[str, Any]:
        return dict(
            self.page.evaluate(
                """async () => {
                    const response = await fetch('/ui/state');
                    if (!response.ok) throw new Error(`state ${response.status}`);
                    return response.json();
                }"""
            )
        )

    def approvals(self) -> list[dict[str, Any]]:
        return list(self.ui_state()["approvals"])

    def wait_until(self, label: str, predicate: Callable[[], Any], *, timeout_ms: int = 10_000) -> Any:
        deadline = time.monotonic() + timeout_ms / 1000
        last: Any = None
        while time.monotonic() < deadline:
            last = predicate()
            if last:
                return last
            self.page.wait_for_timeout(200)
        raise SmokeFailure(f"timed out waiting for {label}; last={last!r}")

    def latest_approval_id(self, previous_ids: set[str]) -> str:
        pending = self.wait_until(
            "new pending approval",
            lambda: [item for item in self.approvals() if item["signal_id"] not in previous_ids],
        )
        return str(pending[-1]["signal_id"])

    def click_approval_action(self, signal_id: str, action: str) -> None:
        self.nav("signals")
        self.click(f"[data-action='{action}'][data-signal-id='{signal_id}']", f"{action} approval {signal_id}")

    def approve_pending(self, signal_id: str) -> None:
        self.click_approval_action(signal_id, "approve")
        self.wait_until(
            f"approval {signal_id} removed",
            lambda: all(item["signal_id"] != signal_id for item in self.approvals()),
        )


def exercise_dashboard(driver: UiDriver) -> None:
    driver.nav("dashboard")
    driver.click("[data-view='dashboard'] [data-action='nav'][data-target='signals']", "dashboard review all shortcut")
    if driver.active_view() != "signals":
        raise SmokeFailure("dashboard Review all shortcut did not navigate to Signals")
    driver.nav("dashboard")
    driver.click("[data-view='dashboard'] [data-action='nav'][data-target='exchanges']", "dashboard inspect exchanges shortcut")
    if driver.active_view() != "exchanges":
        raise SmokeFailure("dashboard Inspect shortcut did not navigate to Exchanges")
    driver.nav("dashboard")
    for filter_name in ["all", "buy", "sell"]:
        driver.click(f"[data-runtime-filter='{filter_name}']", f"runtime filter {filter_name}")
    driver.click("[data-view='dashboard'] [data-action='nav'][data-target='audit']", "dashboard audit open shortcut")
    if driver.active_view() != "audit":
        raise SmokeFailure("dashboard Audit shortcut did not navigate to Audit")


def exercise_signal_builder_and_halt_flow(driver: UiDriver) -> str:
    driver.nav("signals")
    for channel in ["discord", "tradingview", "operator"]:
        driver.select("#signalChannel", channel, label=f"signal channel {channel}")
    driver.click("#sampleSignalButton", "sample signal")
    driver.fill("#signalText", "BUY BTCUSDT $251 @ 66234 SL 2% TP 4.5% TRAIL 2.5%", "fill BTC approval signal")
    driver.click("#parseSignalButton", "parse signal text")
    driver.page.wait_for_function("() => document.querySelector('#parsedSignal')?.textContent.includes('BTC/USDT')")
    driver.click("#previewSignalButton", "preview signal risk")
    driver.page.wait_for_function("() => document.querySelector('#riskPreview')?.textContent.length > 10")
    driver.click("#copyPayloadButton", "copy signal payload")

    before_first = {item["signal_id"] for item in driver.approvals()}
    driver.click("#submitSignalButton", "submit signal for approval")
    halted_approval_id = driver.latest_approval_id(before_first)
    driver.click_approval_action(halted_approval_id, "preview-approval-ticket")
    if driver.active_view() != "trading":
        raise SmokeFailure("approval preview did not load the trading ticket")
    driver.nav("signals")
    driver.click("#approvalList [data-action='copy-json']", "copy pending approval json")
    driver.fill("#haltReasonInput", "ui halt approval retention", "fill halt reason")
    driver.click("#haltButton", "global halt")
    driver.wait_until("halted control state", lambda: driver.ui_state()["control"]["halted"] is True)
    driver.click_approval_action(halted_approval_id, "approve")
    driver.wait_until(
        "halted approval still pending",
        lambda: any(item["signal_id"] == halted_approval_id for item in driver.ui_state()["approvals"])
        and len(driver.ui_state()["orders"]) == 0,
    )
    driver.click("#resumeButton", "resume after halted approval")
    driver.wait_until("resumed control state", lambda: driver.ui_state()["control"]["halted"] is False)
    driver.approve_pending(halted_approval_id)
    driver.wait_until("first approved order exists", lambda: len(driver.ui_state()["orders"]) >= 1)

    driver.nav("signals")
    driver.fill("#signalText", "BUY ETHUSDT $122 @ 3000 SL 2% TP 5%", "fill ETH rejection signal")
    before_reject = {item["signal_id"] for item in driver.approvals()}
    driver.click("#submitSignalButton", "submit signal for rejection")
    reject_approval_id = driver.latest_approval_id(before_reject)
    driver.fill("#rejectReasonInput", "UI rejection smoke test", "fill reject reason")
    driver.click_approval_action(reject_approval_id, "reject")
    driver.wait_until(
        "rejected approval removed",
        lambda: all(item["signal_id"] != reject_approval_id for item in driver.approvals()),
    )
    return halted_approval_id


def exercise_signal_history(driver: UiDriver, loaded_signal_id: str) -> None:
    driver.nav("signals")
    driver.fill("#signalSearch", "BTC", "filter signal history BTC")
    driver.page.wait_for_function("() => document.querySelector('#signalResultCount')?.textContent.includes('/')")
    driver.click("[data-action='load-signal-ticket']", "load signal history row into ticket")
    if driver.active_view() != "trading":
        raise SmokeFailure("signal history Load did not navigate to Trading")
    driver.nav("signals")
    driver.fill("#signalSearch", "BTC", "filter signal history BTC again")
    driver.click("[data-action='preview-signal-ticket']", f"preview signal history row ticket {loaded_signal_id}")
    driver.nav("signals")
    driver.fill("#signalSearch", "", "clear signal search")
    driver.click("[data-view='signals'] [data-action='copy-json']", "copy signal history row json")


def exercise_trading_desk(driver: UiDriver) -> list[str]:
    driver.nav("trading")
    for pair in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        driver.click(f"[data-pair='{pair}']", f"select pair {pair}")
    for strategy in ["Breakout Guard", "DCA Ladder", "Mean Grid 18"]:
        driver.select("#ticketStrategy", label_value=strategy, label=f"ticket strategy {strategy}")
    for side in ["SELL", "BUY"]:
        driver.select("#ticketSide", side, label=f"ticket side {side}")
    for mode in ["base", "quote"]:
        driver.select("#ticketSizeMode", mode, label=f"ticket size mode {mode}")
    for preset in ["25", "100", "max-order", "remaining-cap"]:
        driver.click(f"[data-size-preset='{preset}']", f"size preset {preset}")
    driver.fill("#ticketSymbol", "SOLUSDT", "ticket symbol SOL")
    driver.select("#ticketSide", "BUY", label="ticket side buy final")
    driver.select("#ticketSizeMode", "quote", label="ticket quote final")
    driver.fill("#ticketAmount", "123", "ticket amount 123")
    driver.fill("#ticketPrice", "148", "ticket price 148")
    driver.fill("#ticketStop", "3", "ticket stop 3")
    driver.fill("#ticketTakeProfit", "7", "ticket take profit 7")
    driver.fill("#ticketTrailingStop", "4", "ticket trailing stop 4")
    driver.click("#copyTicketAlertButton", "copy ticket alert")
    driver.click("#copyTicketJsonButton", "copy ticket json")
    driver.click("#buildTicketButton", "build ticket into alert")
    if driver.active_view() != "signals":
        raise SmokeFailure("Build Alert did not navigate to Signals")
    driver.nav("trading")
    driver.click("#previewTicketButton", "preview ticket risk")
    if driver.active_view() != "signals":
        raise SmokeFailure("Preview Risk did not show Signals risk view")
    driver.nav("trading")
    before_ticket = {item["signal_id"] for item in driver.approvals()}
    driver.click("#submitTicketButton", "submit ticket for approval")
    sol_approval_id = driver.latest_approval_id(before_ticket)
    driver.approve_pending(sol_approval_id)
    driver.wait_until(
        "SOL order approved",
        lambda: any(order["symbol"] == "SOL/USDT" for order in driver.ui_state()["orders"]),
    )
    driver.nav("trading")
    for timeframe in ["15m", "1h", "4h", "1d"]:
        driver.click(f"[data-timeframe='{timeframe}']", f"chart timeframe {timeframe}")
    driver.fill("#markPrice", "156", "fill mark price 156")
    driver.click("#updatePriceButton", "update mark price")
    driver.wait_until("mark price update reflected", lambda: len(driver.ui_state()["orders"]) >= 2)
    for table in ["positions", "orders"]:
        driver.click(f"[data-desk-table='{table}']", f"desk table {table}")
    driver.fill("#deskSearch", "SOL", "desk search SOL")
    driver.click("#deskTableBody [data-action='inspect-order']", "inspect order row")
    driver.nav("trading")
    driver.click("[data-desk-table='orders']", "desk table orders again")
    driver.fill("#deskSearch", "SOL", "desk search SOL for copy")
    driver.click("#deskTableBody [data-action='copy-json']", "copy order row json")
    driver.click("[data-desk-table='positions']", "desk table positions for close buttons")
    driver.fill("#deskSearch", "SOL", "desk search SOL for close")
    driver.click("[data-action='load-position-price'][data-symbol='SOLUSDT']", "use mark from SOL position")

    before_close = {item["signal_id"] for item in driver.approvals()}
    for close_label in ["Close 25%", "Close 50%", "Close Position"]:
        driver.click(
            f"[data-action='close-position'][data-symbol='SOLUSDT'][data-close-label='{close_label}']",
            f"SOL {close_label}",
        )
    return [item["signal_id"] for item in driver.approvals() if item["signal_id"] not in before_close]


def exercise_portfolio(driver: UiDriver) -> None:
    driver.nav("portfolio")
    for range_name in ["1d", "1w", "1m", "ytd"]:
        driver.click(f"[data-equity-range='{range_name}']", f"portfolio range {range_name}")
    with driver.page.expect_download(timeout=10_000) as state_download:
        driver.click("#exportStateButton", "export state json")
    state_download.value.suggested_filename
    if driver.page.locator("[data-view='portfolio'] [data-action='load-position-price']").count() > 0:
        driver.click("[data-view='portfolio'] [data-action='load-position-price']", "portfolio bracket load trigger price")
        driver.nav("portfolio")
    if driver.page.locator("[data-view='portfolio'] [data-action='trigger-exit-price']").count() > 0:
        driver.click("[data-view='portfolio'] [data-action='trigger-exit-price']", "portfolio bracket trigger exit")
        driver.wait_until(
            "exit trigger audited",
            lambda: any(event["event_type"] == "exit.triggered" for event in driver.ui_state()["audit"]),
        )
    driver.click("[data-view='portfolio'] [data-action='nav'][data-target='trading']", "portfolio update price shortcut")
    if driver.active_view() != "trading":
        raise SmokeFailure("portfolio Update Price shortcut did not navigate to Trading")


def exercise_strategies(driver: UiDriver) -> None:
    driver.nav("strategies")
    for filter_name in ["all", "signal", "grid", "dca"]:
        driver.click(f"[data-strategy-filter='{filter_name}']", f"strategy filter {filter_name}")
    driver.click("[data-strategy-filter='all']", "strategy filter all for card actions")
    driver.fill("#strategySearch", "BTC", "strategy search BTC")
    driver.fill("#strategySearch", "", "clear strategy search")
    for sort_name in ["featured", "roi", "sim-return", "sim-drawdown", "win", "drawdown", "name"]:
        driver.select("#strategySort", sort_name, label=f"strategy sort {sort_name}")
    driver.click("[data-strategy-filter='all']", "strategy filter all before strategy loop")
    for strategy_id in ["breakout-guard", "mean-grid-18", "dca-ladder"]:
        driver.click(f"[data-action='toggle-strategy-pin'][data-strategy-id='{strategy_id}']", f"pin {strategy_id}")
        driver.click(f"[data-action='toggle-strategy-pin'][data-strategy-id='{strategy_id}']", f"unpin {strategy_id}")
        driver.click(f"[data-action='backtest-strategy'][data-strategy-id='{strategy_id}']", f"backtest {strategy_id}")
        driver.click(f"[data-action='copy-strategy'][data-strategy-id='{strategy_id}']", f"load strategy {strategy_id}", settle_ms=800)
        if driver.active_view() != "trading":
            raise SmokeFailure(f"strategy {strategy_id} did not load the trading ticket")
        driver.nav("strategies")
        driver.click("[data-strategy-filter='all']", f"strategy filter all after {strategy_id}")


def exercise_exchanges(driver: UiDriver, warnings: list[str]) -> None:
    driver.nav("exchanges")
    driver.fill("#exchangeSearch", "bit", "exchange search bit")
    driver.fill("#exchangeSearch", "", "clear exchange search")
    driver.click("#refreshExchangesButton", "refresh exchanges", settle_ms=800)
    platform_cards = driver.page.locator(".platform-card")
    for index in range(platform_cards.count()):
        driver.click_locator(platform_cards.nth(index), f"platform card {index + 1}/{platform_cards.count()}", settle_ms=150)
    exchange_rows = driver.page.locator(".exchange-row")
    for index in range(exchange_rows.count()):
        driver.click_locator(exchange_rows.nth(index), f"exchange row {index + 1}/{exchange_rows.count()}", settle_ms=150)
    driver.click("#copyCapabilityButton", "copy capability payload")
    driver.fill("#bitunixSymbols", "BTCUSDT", "bitunix symbols")
    driver.fill("#bitunixMarginCoin", "USDT", "bitunix margin coin")
    driver.click("#bitunixTickerButton", "load bitunix tickers", settle_ms=500)
    try:
        driver.page.wait_for_function("() => document.querySelector('#bitunixView')?.textContent !== 'Loading...'", timeout=15_000)
    except Exception:
        warnings.append("Bitunix ticker request did not complete within 15 seconds")
    driver.click("#bitunixAccountButton", "check bitunix account", settle_ms=500)
    try:
        driver.page.wait_for_function("() => document.querySelector('#bitunixView')?.textContent !== 'Loading...'", timeout=5_000)
    except Exception:
        warnings.append("Bitunix account request did not complete within 5 seconds")
    driver.click("#copyBitunixButton", "copy bitunix payload")


def exercise_audit(driver: UiDriver) -> None:
    driver.nav("audit")
    driver.fill("#auditSearch", "order", "audit search order")
    driver.fill("#auditSearch", "", "clear audit search")
    with driver.page.expect_download(timeout=10_000) as audit_download:
        driver.click("#exportAuditButton", "export audit csv")
    audit_download.value.suggested_filename
    driver.click("#refreshAuditButton", "refresh audit")
    if driver.page.locator("[data-view='audit'] [data-action='load-audit-related']").count() > 0:
        driver.click("[data-view='audit'] [data-action='load-audit-related']", "open audit related object")
        driver.nav("audit")
    driver.click("[data-view='audit'] [data-action='copy-json']", "copy audit row json")


def collect_console_message(message: Any, console_errors: list[str], warnings: list[str]) -> None:
    if message.type != "error":
        return
    text = message.text
    if "Failed to load resource" in text:
        warnings.append(f"browser resource: {text}")
    else:
        console_errors.append(f"{message.type}: {text}")


def collect_failed_request(request: Any, failed_requests: list[str]) -> None:
    url = request.url
    if "favicon" in url:
        return
    failure = request.failure
    error_text = failure if isinstance(failure, str) else "failed"
    failed_requests.append(f"{request.method} {url}: {error_text}")


def canvas_stats(page: Any) -> list[dict[str, Any]]:
    return list(
        page.evaluate(
            """() => Array.from(document.querySelectorAll('canvas')).map((canvas) => {
                const ctx = canvas.getContext('2d');
                const id = canvas.id || canvas.dataset.strategySpark || 'canvas';
                if (!ctx) return { id, nonBlank: 0, width: canvas.width, height: canvas.height };
                const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                let nonBlank = 0;
                for (let index = 0; index < data.length; index += 4) {
                    if (data[index] || data[index + 1] || data[index + 2] || data[index + 3]) nonBlank += 1;
                }
                return { id, nonBlank, width: canvas.width, height: canvas.height };
            })"""
        )
    )


CLICK_AND_CLIPBOARD_SCRIPT = """
window.__clickedControls = [];
window.__copiedTexts = [];
document.addEventListener('click', (event) => {
  const control = event.target.closest('button, [data-action], input[type="checkbox"]');
  if (!control) return;
  window.__clickedControls.push({
    tag: control.tagName,
    id: control.id || '',
    text: (control.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
    action: control.dataset ? (control.dataset.action || '') : '',
    view: control.dataset ? (control.dataset.view || '') : '',
    target: control.dataset ? (control.dataset.target || '') : '',
  });
}, true);
try {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: async (text) => { window.__copiedTexts.push(String(text)); } },
  });
} catch {}
document.execCommand = () => true;
"""


if __name__ == "__main__":
    raise SystemExit(main())
