"""Headless-Chromium check of the built frontend against the LIVE Studionet contract.

    cd frontend && npm run build && cd ..
    pip install playwright && python -m playwright install chromium
    python tools/browser_check.py read                                  # Grants tab + grant #1 detail
    python tools/browser_check.py faucet <burner_key>                   # "+10 test GEN" button
    python tools/browser_check.py post   <burner_key> <builder_addr>    # Funder tab: post a psf/requests grant for <builder_addr>
    python tools/browser_check.py verify <burner_key> <grant> <index>   # grant page: "Verify on GitHub"
    python tools/browser_check.py apply  <burner_key> <grant>           # grant page: apply as builder
    python tools/browser_check.py post-open <burner_key>                # Funder tab: post an OPEN pallets/flask grant
    python tools/browser_check.py form   <burner_key>                   # Funder tab: example form, screenshot only (no tx)
    python tools/browser_check.py assign <burner_key> <grant>           # grant page: assign the first pending applicant
    python tools/browser_check.py tabs   <burner_key>                   # Funder / Builder / Settings screenshots

<burner_key> is a throwaway Studionet private key injected as the app's burner account.
Never use a key that holds real funds. Screenshots go to docs/screenshots/.
If the machine reaches the internet through HTTPS_PROXY, the proxy is passed to Chromium.
"""
import functools
import http.server
import os
import socketserver
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DIST = str(ROOT / "frontend" / "dist")
SHOTS = ROOT / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


srv = Server(("127.0.0.1", 8766), functools.partial(Quiet, directory=DIST))
threading.Thread(target=srv.serve_forever, daemon=True).start()

step = sys.argv[1]
key = sys.argv[2] if len(sys.argv) > 2 else None


def wait_tx(pg, timeout_s=110):
    """Wait until the newest tx-log entry is ok or err; print it."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        cls = pg.eval_on_selector("#txlog .tx", "e => e.className")
        if " ok" in cls or " err" in cls:
            break
        time.sleep(2)
    print("TXLOG:", pg.inner_text("#txlog"))
    print("TX HASHES:", pg.eval_on_selector_all("#txlog code[title]", "els => els.map(e => e.title)"))


with sync_playwright() as p:
    u = urllib.parse.urlparse(os.environ.get("HTTPS_PROXY", ""))
    proxy = None
    if u.hostname:
        proxy = {"server": f"{u.scheme}://{u.hostname}:{u.port}", "bypass": "127.0.0.1,localhost"}
        if u.username:
            proxy["username"] = urllib.parse.unquote(u.username)
        if u.password:
            proxy["password"] = urllib.parse.unquote(u.password)
    b = p.chromium.launch(proxy=proxy)
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    if key:
        ctx.add_init_script(f"localStorage.setItem('grantflow.burnerKey','{key}');localStorage.setItem('grantflow.accountMode','burner');")
    pg = ctx.new_page()
    logs = []
    pg.on("console", lambda m: logs.append(m.type + ": " + m.text))
    pg.on("pageerror", lambda e: logs.append("PAGEERROR " + str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto("http://127.0.0.1:8766/index.html")
    try:
        pg.wait_for_selector(".tiles", timeout=40000)
    except Exception:
        print("LOGS", "\n".join(logs)[:3000])
        print(pg.inner_text("main")[:1000])
        pg.screenshot(path=str(SHOTS / "fail.png"))
        raise

    if step == "read":
        pg.screenshot(path=str(SHOTS / "grants.png"), full_page=True)
        print(pg.inner_text(".tiles"))
        pg.click("[data-grant='1']")
        pg.wait_for_selector("#grant-detail .detail", timeout=30000)
        for s in pg.query_selector_all("#grant-detail details.evidence"):
            s.evaluate("e => e.open = true")
        time.sleep(1)
        pg.locator("#grant-detail").screenshot(path=str(SHOTS / "grant-detail.png"))
        print(pg.inner_text("#grant-detail")[:2500])
    elif step == "faucet":
        pg.wait_for_selector("#btn-faucet", timeout=20000)
        pg.click("#btn-faucet")
        time.sleep(6)
        print(pg.inner_text("#account"))
    elif step == "post":
        builder = sys.argv[3]
        pg.click("[data-tab=funder]")
        pg.wait_for_selector("#grant-form", timeout=20000)
        pg.fill("input[name=title]", "Requests v2.34.2 CI grant (UI demo)")
        pg.fill("textarea[name=description]", "Posted through the GrantFlow web UI: pays the named builder if psf/requests release v2.34.2 has every check run passing.")
        pg.fill("input[name=owner]", "psf")
        pg.fill("input[name=repo]", "requests")
        pg.fill("input[name=grace]", "5")
        pg.fill("input[name=builder]", builder)
        pg.fill("input[name=label0]", "v2.34.2 with all check runs green")
        pg.fill("input[name=amount0]", "0.5")
        pg.fill("input[name=tag0]", "v2.34.2")
        pg.select_option("select[name=checks0]", "check_runs")
        pg.screenshot(path=str(SHOTS / "funder-post.png"), full_page=True)
        pg.click("#grant-form button[type=submit]")
        wait_tx(pg)
        time.sleep(4)
        pg.screenshot(path=str(SHOTS / "funder-posted.png"), full_page=True)
    elif step == "verify":
        gid, idx = sys.argv[3], sys.argv[4]
        pg.click(f"[data-grant='{gid}']")
        pg.wait_for_selector("#grant-detail .detail", timeout=30000)
        pg.click(f"#grant-detail [data-act=verify][data-i='{idx}']")
        wait_tx(pg, 112)
        time.sleep(5)
        for s in pg.query_selector_all("#grant-detail details.evidence"):
            s.evaluate("e => e.open = true")
        pg.screenshot(path=str(SHOTS / "verify-ui.png"), full_page=True)
        print(pg.inner_text("#grant-detail")[:2000])
    elif step == "apply":
        gid = sys.argv[3]
        pg.click(f"[data-grant='{gid}']")
        pg.wait_for_selector("#apply-form", timeout=30000)
        pg.fill("#apply-form input[name=login]", "grantflow-ui-demo")
        pg.fill("#apply-form textarea[name=pitch]", "Applying through the GrantFlow web UI (Studionet burner account, demo).")
        pg.click("#apply-form button[type=submit]")
        wait_tx(pg)
        time.sleep(4)
        pg.screenshot(path=str(SHOTS / "apply-ui.png"), full_page=True)
    elif step == "post-open":
        pg.click("[data-tab=funder]")
        pg.wait_for_selector("#grant-form", timeout=20000)
        pg.click("#btn-example")
        pg.wait_for_selector("input[name=tag0]", timeout=5000)
        pg.fill("input[name=amount0]", "0.3")
        pg.screenshot(path=str(SHOTS / "funder-example.png"), full_page=True)
        pg.click("#grant-form button[type=submit]")
        wait_tx(pg)
        time.sleep(4)
    elif step == "form":
        pg.click("[data-tab=funder]")
        pg.wait_for_selector("#grant-form", timeout=20000)
        pg.click("#btn-example")
        pg.wait_for_selector("input[name=tag0]", timeout=5000)
        pg.fill("input[name=amount0]", "0.3")
        time.sleep(3)
        pg.screenshot(path=str(SHOTS / "funder-example.png"), full_page=True)
        print(pg.inner_text("#grant-form button[type=submit]"))
    elif step == "assign":
        gid = sys.argv[3]
        pg.click(f"[data-grant='{gid}']")
        pg.wait_for_selector("#grant-detail [data-assign]", timeout=30000)
        pg.click("#grant-detail [data-assign]")
        wait_tx(pg)
        time.sleep(5)
        pg.locator("#grant-detail").screenshot(path=str(SHOTS / "assign-ui.png"))
        print(pg.inner_text("#grant-detail")[:800])
    elif step == "tabs":
        for t in ["funder", "builder", "settings"]:
            pg.click(f"[data-tab={t}]")
            time.sleep(5)
            pg.screenshot(path=str(SHOTS / f"{t}.png"), full_page=True)
            print("==", t, pg.inner_text(f"#tab-{t}")[:600])
    errs = [l for l in logs if "PAGEERROR" in l or l.startswith("error")]
    if errs:
        print("CONSOLE ERRORS:", "\n".join(errs)[:2000])
    b.close()
