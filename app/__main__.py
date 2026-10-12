"""Start the app: `python -m app`. Opens your browser with this run's sign-in link."""

import argparse
import os
import secrets
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("--no-browser", action="store_true", help="print the sign-in link instead of opening it")
    args = parser.parse_args()

    # A new secret every run; the reload worker inherits it. Only someone who sees this link
    # (you, in this terminal) can start a session. See app/auth.py.
    launch_token = secrets.token_urlsafe(32)
    os.environ["APP_LAUNCH_TOKEN"] = launch_token
    link = f"http://localhost:8000/auth/start?t={launch_token}"
    print(f"\n  Household Bills: {link}\n  (this link signs you in; don't share it)\n", flush=True)
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, [link]).start()

    # 127.0.0.1 only: this app holds Gmail/bank access. Login is enforced on top (app/auth.py).
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True, reload_dirs=["app"])
