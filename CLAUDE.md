# Game Spec Check

Local Windows app (Python standard library only, no dependencies) with a browser UI in `web/`.

- Run: `python run.py` (add `--no-browser` when previewing). Tests: `python -m unittest discover tests`, then `python tools\smoke.py run.py`. `tests/js/logic.test.js` needs Node (CI has it; it is skipped locally without Node).
- Python changes need a server restart. Files in `web/` are served fresh on reload.
- Exe: `build.bat` writes `dist\GameSpecCheck.exe` (PyInstaller onefile, in a local `.venv`, versions pinned with hashes in `requirements-build.txt`) and smoke-tests it. Bundled files resolve through `specbench/paths.py`, so add new data folders there and to `--add-data` in `build.bat`.
- Steam results are cached raw in `.cache/` and parsed on every read, so parser fixes apply without clearing it.
- Saved user data (profiles, wanted games, mappings, last scan) is in `.data/` from source, `%LOCALAPPDATA%\GameSpecCheck\data` in the exe; `GSC_DATA_DIR` overrides it, and tests use a temp folder through it.
- All outbound requests go through `specbench/net.py` (pacing, 429 handling). Discovery must stay read-only: never run or open files while scanning, and never walk whole drives.
- State-changing API calls are JSON POSTs checked for Origin and Sec-Fetch-Site in `server.py`; keep new ones that way. Launching only uses actions stored in a scan record or an exe the user set.

## License and legal files

- Licensed under PolyForm Noncommercial 1.0.0: `LICENSE` is the verbatim text. `NOTICE` has the required notice line and the statements that this is a personal tool, written with AI, for noncommercial use only, with no donations. `CREDITS.md` and `licenses/PSF-LICENSE.txt` hold third-party credits.
- The exe bundles these files in a `legal` folder (`--add-data` in `build.bat`), and the server serves them at `/legal/<name>` and `/about.html`. Keep `web/about.html`, `NOTICE` and the README statements consistent when one changes.
- Do not add donation, sponsor or funding links or files such as `FUNDING.yml`. The author does not want donations.
- The released exe is unsigned until a certificate exists. `tools/sign.ps1` signs it when one is configured.

## Wording

All user-facing text (UI strings, answer sentences in `specbench/compare.py`, README) follows
https://github.com/conorbronsdon/avoid-ai-writing. In short: no em dashes, no hype words, no
"it's not X, it's Y" framing, no filler or stacked hedges, sentence-case headings, bold used sparingly.
Say what is true about the PC and the game in plain words.
