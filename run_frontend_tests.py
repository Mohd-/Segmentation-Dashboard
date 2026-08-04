#!/usr/bin/env python
"""Zero-dependency front-end test driver.

Boots the Flask app on 127.0.0.1:8021 against a scratch SQLite database
(NEVER the real pipeline_tracker.db), starts a tiny stdlib HTTP receiver for
the harness results beacon, drives an installed headless browser at
/static/tests/runner.html?live=1&post=<port>, and prints a report.

Run:            python run_frontend_tests.py
Manual viewing: python run_frontend_tests.py --browser open

Exit codes: 0 all tests passed, 1 at least one failure, 2 harness/timeout
problems (server would not boot, the browser produced no results, ...).
"""
import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
HOST = "127.0.0.1"
APP_PORT = 8021  # 8020 is the normal dev port; the test server must not collide.
HEALTH_URL = "http://%s:%d/api/health" % (HOST, APP_PORT)
RESULTS_TIMEOUT_S = 60
BOOT_TIMEOUT_S = 15


def browser_candidates():
    """Installed browser executables in preference order for this platform."""
    if sys.platform == "darwin":
        return [
            ("firefox", "/Applications/Firefox.app/Contents/MacOS/firefox"),
            ("chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("chromium", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    if os.name == "nt":
        roots = [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")]
        return [
            ("firefox", os.path.join(roots[0], "Mozilla Firefox", "firefox.exe")),
            ("firefox", os.path.join(roots[1], "Mozilla Firefox", "firefox.exe")),
            ("chromium", os.path.join(roots[1], "Microsoft", "Edge", "Application", "msedge.exe")),
            ("chromium", os.path.join(roots[0], "Microsoft", "Edge", "Application", "msedge.exe")),
            ("chromium", os.path.join(roots[0], "Google", "Chrome", "Application", "chrome.exe")),
            ("chromium", os.path.join(roots[2], "Google", "Chrome", "Application", "chrome.exe")),
        ]
    return [
        ("firefox", shutil.which("firefox") or ""),
        ("chromium", shutil.which("chromium") or ""),
        ("chromium", shutil.which("chromium-browser") or ""),
        ("chromium", shutil.which("google-chrome") or ""),
        ("chromium", shutil.which("microsoft-edge") or ""),
    ]


def find_browser(requested="auto"):
    for kind, path in browser_candidates():
        if requested not in {"auto", kind}:
            continue
        if path and os.path.isfile(path):
            return kind, path
    return None, None


# ---------------------------------------------------------------------------
# Results receiver (CORS-friendly: the beacon POST is cross-origin)
# ---------------------------------------------------------------------------

class ResultsReceiver(BaseHTTPRequestHandler):
    payload = None
    received = threading.Event()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Headers", "content-type")

    def do_OPTIONS(self):  # preflight for the application/json POST
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path != "/results":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            ResultsReceiver.payload = json.loads(body.decode("utf-8"))
        except ValueError:
            ResultsReceiver.payload = {"error": "unparseable results body",
                                       "raw": body.decode("utf-8", "replace")}
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')
        ResultsReceiver.received.set()

    def log_message(self, *args):  # keep the report clean
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def url_ok(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_for_health(deadline_s):
    started = time.time()
    while time.time() - started < deadline_s:
        if url_ok(HEALTH_URL):
            return True
        time.sleep(0.25)
    return False


def stop_process(proc, name):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("WARNING: could not kill %s (pid %s)" % (name, proc.pid))


def kill_by_marker(marker):
    """Kill any process whose command line contains the (run-unique) marker.

    Safety net for macOS Firefox, which can relaunch itself through
    LaunchServices as a process that is not our direct child; the throwaway
    profile path uniquely identifies this run's browser.
    """
    try:
        out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
        for pid in out.stdout.split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (OSError, ValueError):
                pass
    except OSError:
        pass


def write_firefox_prefs(profile_dir):
    prefs = [
        'user_pref("browser.shell.checkDefaultBrowser", false);',
        'user_pref("browser.sessionstore.resume_from_crash", false);',
        'user_pref("toolkit.startup.max_resumed_crashes", -1);',
        'user_pref("datareporting.policy.dataSubmissionEnabled", false);',
        'user_pref("app.update.enabled", false);',
        'user_pref("browser.aboutwelcome.enabled", false);',
        'user_pref("dom.max_script_run_time", 0);',
    ]
    with open(os.path.join(profile_dir, "user.js"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(prefs) + "\n")


def print_report(payload):
    tests = payload.get("tests", [])
    passed = payload.get("passed", 0)
    failed = payload.get("failed", 0)
    skipped = payload.get("skipped", 0)
    print("")
    print("=" * 70)
    print("FRONT-END TEST RESULTS")
    print("=" * 70)
    for entry in tests:
        status = entry.get("status", "?").upper()
        if status == "PASS":
            continue
        print("  [%s] %s" % (status, entry.get("name", "(unnamed)")))
        message = (entry.get("message") or "").strip()
        if message:
            for line in message.splitlines():
                print("         %s" % line)
    if failed == 0 and skipped == 0:
        print("  (all tests passed; nothing to itemise)")
    print("-" * 70)
    print("TOTAL: %d   PASS: %d   FAIL: %d   SKIP: %d" %
          (payload.get("total", len(tests)), passed, failed, skipped))
    print("VERDICT: %s" % payload.get("verdict", "FAIL" if failed else "PASS"))
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run the front-end test suite.")
    parser.add_argument("--browser", choices=["auto", "firefox", "chromium", "open"], default="auto",
                        help="auto: use an installed Firefox/Chromium browser (default); "
                             "open: boot the server and open the runner for manual viewing")
    args = parser.parse_args()

    if not os.path.exists(PYTHON):
        print("ERROR: %s not found" % PYTHON)
        return 2

    if url_ok(HEALTH_URL):
        print("ERROR: something already answers on %s — refusing to reuse an "
              "unknown server (it may be running against the real database)." % HEALTH_URL)
        return 2

    tmp_dir = tempfile.mkdtemp(prefix="segdash-fe-tests-")
    scratch_db = os.path.join(tmp_dir, "frontend_tests.db")
    env = dict(os.environ)
    # DATABASE_URL beats SEGMENT_TRACKER_DB_PATH in config.database_url(), so an
    # inherited value would silently repoint the test server (and its boot-time
    # migrations) at another database. Strip it, and pin auth off so the live
    # tests never 401 because of inherited auth settings.
    env.pop("DATABASE_URL", None)
    env.pop("SEGMENT_TRACKER_PASSCODE", None)
    env["AUTH_REQUIRED"] = "false"
    env["SEGMENT_TRACKER_DB_PATH"] = scratch_db          # NEVER the real DB
    env["SEGMENT_TRACKER_RF_MODEL_PATH"] = os.path.join(tmp_dir, "rf_model.joblib")

    flask_proc = None
    browser_proc = None
    httpd = None
    exit_code = 2

    try:
        # 1. Boot the Flask app on the test port against the scratch DB.
        flask_proc = subprocess.Popen(
            [PYTHON, "-c",
             'import main; main.app.run(host="%s", port=%d)' % (HOST, APP_PORT)],
            cwd=BASE_DIR, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_for_health(BOOT_TIMEOUT_S):
            print("ERROR: Flask app did not answer %s within %ds" % (HEALTH_URL, BOOT_TIMEOUT_S))
            return 2
        # Belt and braces: the booted server itself must report the scratch DB.
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
                health = json.loads(resp.read().decode("utf-8"))
        except Exception:
            health = {}
        if scratch_db not in str(health.get("db", "")):
            print("ERROR: booted server reports db %r instead of the scratch DB "
                  "%s — refusing to run against it." % (health.get("db"), scratch_db))
            return 2
        print("Server up on %s (db: %s)" % (HEALTH_URL, scratch_db))

        browser_kind, browser_path = find_browser(args.browser)
        manual = args.browser == "open"
        if not manual and not browser_path:
            print("ERROR: no supported Firefox, Chrome, Chromium, or Edge executable was found.")
            return 2

        if manual:
            url = "http://%s:%d/static/tests/runner.html?live=1" % (HOST, APP_PORT)
            print("Opening %s — Ctrl-C to stop the server." % url)
            webbrowser.open(url)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping.")
            return 0

        # 2. Results receiver on an ephemeral port.
        ResultsReceiver.payload = None
        ResultsReceiver.received.clear()
        httpd = HTTPServer((HOST, 0), ResultsReceiver)
        results_port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        # 3. Headless installed browser with a throwaway profile.
        profile_dir = os.path.join(tmp_dir, "browser-profile")
        os.makedirs(profile_dir, exist_ok=True)
        runner_url = ("http://%s:%d/static/tests/runner.html?live=1&post=%d"
                      % (HOST, APP_PORT, results_port))
        print("Launching headless %s: %s" % (browser_kind, runner_url))
        browser_env = dict(env)
        if browser_kind == "firefox":
            write_firefox_prefs(profile_dir)
            browser_env["MOZ_HEADLESS"] = "1"
            command = [browser_path]
            # This keeps macOS Firefox attached to our process tree.
            if sys.platform == "darwin":
                command.append("-foreground")
            command.extend(["-headless", "-no-remote", "-profile", profile_dir, runner_url])
        else:
            command = [
                browser_path,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--user-data-dir=" + profile_dir,
                runner_url,
            ]
        browser_proc = subprocess.Popen(
            command, env=browser_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 4. Wait for the beacon.
        if not ResultsReceiver.received.wait(RESULTS_TIMEOUT_S):
            print("ERROR: no test results arrived within %ds "
                  "(harness crash or the browser failed to load the runner)." % RESULTS_TIMEOUT_S)
            return 2

        payload = ResultsReceiver.payload or {}
        print_report(payload)
        if payload.get("total", 0) == 0:
            # A beacon that reports zero tests means the suite never ran
            # (import failure the runner could not catch, truncated payload...).
            print("ERROR: results payload reported zero tests — harness problem, not a pass.")
            exit_code = 2
        else:
            exit_code = 0 if payload.get("failed", 1) == 0 else 1
        return exit_code
    finally:
        stop_process(browser_proc, "browser")
        if sys.platform == "darwin":
            kill_by_marker(tmp_dir)  # any LaunchServices-relaunched Firefox
        stop_process(flask_proc, "flask app")
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
