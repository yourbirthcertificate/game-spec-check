"""Start Game Spec Check and open it in the browser.

    python run.py                 start the app
    python run.py --no-browser    start without opening a browser tab
    python run.py --check 1245620 print a verdict for one Steam app id and exit
"""
import argparse
import json
import sys
import threading
import webbrowser

import specbench
from specbench import server, specs


def main():
    ap = argparse.ArgumentParser(description="Compare this PC with game requirements.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    ap.add_argument("--specs", action="store_true", help="print detected specs and exit")
    ap.add_argument("--check", metavar="APPID", help="check one Steam app id and exit")
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

    httpd = server.make_server(args.port, args.verbose)
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    print("Game Spec Check is running at %s  (Ctrl+C to stop)" % url, flush=True)
    # Read the hardware in the background so the first page load is instant.
    threading.Thread(target=specs.detect, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
