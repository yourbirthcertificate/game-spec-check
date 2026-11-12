"""Start Game Spec Check and open it in the browser.

    python run.py                 start the app
    python run.py --no-browser    start without opening a browser tab
    python run.py --check 1245620 print a verdict for one Steam app id and exit
"""
import argparse
import json
import sys
import threading
import urllib.request
import webbrowser

import specbench
from specbench import paths, server, specs


def already_running(port):
    """True if another copy of this app is answering on `port`."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == server.APP_ID
    except (OSError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser(description="Compare this PC with game requirements.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    ap.add_argument("--specs", action="store_true", help="print detected specs and exit")
    ap.add_argument("--check", metavar="APPID", help="check one Steam app id and exit")
    ap.add_argument("--version", action="version", version="Game Spec Check " + specbench.__version__)
    args = ap.parse_args()

    if sys.platform != "win32":
        sys.exit("Game Spec Check reads hardware through Windows APIs; run it on Windows.")

    if args.specs:
        print(json.dumps(specs.detect(), indent=2))
        return
    if args.check:
        game = specbench.check_game("steam", args.check, specs.detect())
        print("%s: %s - %s" % (game["name"], game["summary"]["headline"],
                               game["summary"]["detail"]))
        for level, res in game["levels"].items():
            print("  %s: %s" % (level, res["verdict"]))
            for r in res["rows"]:
                print("    %-17s %-8s %s  |  %s" % (r["label"], r["status"],
                                                    r["required"][:60], r["yours"]))
        return

    # Double-clicking the exe twice should reuse the copy that is running.
    if already_running(args.port):
        url = "http://127.0.0.1:%d/" % args.port
        print("Game Spec Check is already running. Opening %s" % url)
        if not args.no_browser:
            webbrowser.open(url)
        return

    httpd = server.make_server(args.port, args.verbose)
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    print("Game Spec Check %s is running." % specbench.__version__)
    print()
    print("  Open %s in your browser if it did not open by itself." % url)
    print("  Close this window to stop the app.")
    print(flush=True)
    # Read the hardware in the background so the first page load is instant.
    threading.Thread(target=specs.detect, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # In the packaged exe the window would vanish before this can be read.
        print("Game Spec Check could not start: %s: %s" % (type(exc).__name__, exc))
        if paths.FROZEN:
            input("Press Enter to close this window.")
        sys.exit(1)
