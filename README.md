# Game Spec Check

A small Windows app that answers one question: can this PC run that game?

It reads the hardware in your machine, finds the games you have installed, looks up the requirements each game's publisher lists, and puts the two side by side with a plain answer on top. It does the same job as Can You Run It or PCGameBenchmark, but it runs on your own computer and your hardware details are never uploaded.

## About this project

- **A personal tool.** It was made for the author's own use and is shared as is, with no support and no promise of updates.
- **Written with AI.** The code was written by Claude, an AI model from Anthropic, through Claude Code. It has had no professional review or security audit, and some answers will be wrong.
- **Noncommercial use only.** It is licensed under the PolyForm Noncommercial License 1.0.0. Personal, hobby and study use is fine. Commercial use is not.
- **No donations.** The author is not accepting donations or sponsorship.

## Running it

### With the exe

Download `GameSpecCheck.exe` from the Releases page and double-click it. It needs Windows 10 or 11 and nothing else, Python included.

A console window opens and your browser goes to `http://127.0.0.1:8765`. Close the console window when you are done. Starting the exe a second time opens the copy that is already running, even if that copy had to move to another port.

The exe is not code-signed yet, so the first time you run it Windows may show "Windows protected your PC". Choose "More info", then "Run anyway". Some antivirus tools also flag single-file Python programs they have not seen before.

### Updates

When the app opens, it asks GitHub once a day whether there is a newer release. If there is, the page shows a notice with the version and a link to the release page, and "Not now" hides it until the next version.

- **What the check sends.** One GET request to `https://api.github.com/repos/yourbirthcertificate/game-spec-check/releases/latest`. It carries the User-Agent `GameSpecCheck/<version> (local desktop tool)` and nothing else: no details about this PC, no game names and no ids. GitHub sees your IP address, as with any web request. The answer is cached for a day, and a failed check is not repeated on its own for an hour.
- **Turning it off.** Untick "Check for a new version when the app opens" on the About page, or start the app with `--no-update-check`, which stops every request to GitHub for that session. "Check for updates" on the About page asks right away.
- **Installing.** Nothing is downloaded until you press "Update now", and that button only appears in the exe. The exe downloads `GameSpecCheck.exe` from the release over HTTPS into `%LOCALAPPDATA%\GameSpecCheck\update`, compares its SHA-256 checksum with the `digest` GitHub lists for that file, and refuses it if the digest is missing or different. It starts the new file once with `--version` to check that it runs, renames the running exe to `GameSpecCheck.exe.old`, puts the new one in its place and restarts. If the swap fails, the old exe keeps its name and keeps working. The `.old` file is deleted on the next start. If the exe sits in a folder you cannot write to, download the new version from the release page instead.
- **What the checksum proves.** The exe is not code-signed. A matching checksum only shows that the file is the one GitHub serves for that release. It does not show who built it, so if this project's GitHub account were taken over, a bad file would pass the check too.

Run from source, the notice says to run `git pull` instead of offering a download.

### From source

You need Windows and Python 3.8 or newer. There is nothing else to install.

Double-click `Start Game Spec Check.bat`, or from a terminal:

```
python run.py
```

### Building the exe yourself

```
build.bat
```

This creates a `.venv` folder, installs the exact PyInstaller version listed in `requirements-build.txt` (hash-checked), runs the tests, writes `dist\GameSpecCheck.exe` and then starts it once with `tools\smoke.py` to check that it serves its page, files and API.

### Signing the exe

The released exe is not signed. Code-signing certificates come from outside companies after an identity check, and most cost money. `build.bat` signs the exe when a certificate is configured and leaves it unsigned otherwise. Set one of these before running it:

```
set GSC_SIGN_THUMBPRINT=<thumbprint of a certificate in your Windows certificate store>
set GSC_SIGN_PFX=C:\path\to\certificate.pfx       (and GSC_SIGN_PFX_PASSWORD)
set GSC_SIGNTOOL_ARGS=<arguments for signtool.exe, for cloud or hardware-token signing>
```

`tools\sign.ps1` documents each option and checks the signature afterwards. A signature replaces "Unknown publisher" with the publisher's name, but SmartScreen can still warn about a new publisher until it builds a reputation.

## What you can do with it

- **Check a game.** Search by name and get an answer: meets the recommended specs, meets the minimum specs, close, below, or only partly checked. If it falls short, the app says which part is the problem and by roughly how much.
- **My games.** Find the games installed through Steam, Epic, GOG, the Xbox app, EA, Ubisoft Connect, Battle.net and itch.io, plus games found through installed programs, Start menu shortcuts, folders you add and exes you add by hand. Check them all in one pass, group them by result, filter by the part that holds them back or by launcher, and press Play to start one through its launcher.
- **Wanted games.** Save games you are thinking about, installed or not, including requirements you pasted.
- **Profiles and upgrades.** Save another PC, a laptop or a planned build as a profile, check any game against it, and compare up to five profiles across all your installed and wanted games at once.
- **Paste requirements from anywhere.** Paste text from a store page, a forum post or a box, check what was read, fix it, and compare.
- **Save a report image.** A PNG or SVG with the game, the PC it was checked against, the comparison, what limits it, what could not be checked and where the requirements came from.

## Finding installed games

Discovery only reads. It never runs a file, never signs in to anything and never searches a drive from top to bottom. It reads each launcher's own records, the Windows installed-program list, Start menu shortcuts and folders you point it at. Each source reports its own status, so "not installed" and "failed" are shown apart from "no games".

| Source | What is read | Play button | Limits |
| --- | --- | --- | --- |
| Steam | `libraryfolders.vdf` and `appmanifest_*.acf` in every library | Yes, `steam://rungameid/` | Tools such as Proton and Steamworks Redistributables are left out. |
| Epic Games | Launcher manifests (`*.item`) in ProgramData | Yes, through the launcher | DLC and unfinished installs are skipped. |
| GOG | `HKLM\SOFTWARE\WOW6432Node\GOG.com\Games` | Yes, runs the exe GOG registered | DLC entries are skipped. |
| Xbox app | `.GamingRoot` on each fixed drive and `MicrosoftGame.config` in each game folder | Yes, through `shell:AppsFolder` | Older Microsoft Store games in the protected WindowsApps folder cannot be listed. |
| EA app | Installed-program records with EA install data (`__Installer\installerdata.xml`) | No, set an exe to add one | EA does not publish a documented way to start a game. |
| Ubisoft Connect | `HKLM\SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs` | Yes, `uplay://launch/` | Titles come from the matching installed-program record. |
| Battle.net | Installed-program records whose uninstaller belongs to Battle.net | No, set an exe to add one | Battle.net product codes for starting games are not documented. |
| itch.io | The itch app's database (read-only), or install receipts | No, set an exe to add one | Only items itch classes as games. |
| Installed programs | The Windows installed-program list | Runs the likeliest exe | Only folders with files that ship with games (Unity, Unreal, Steamworks and similar). Shown for review first. |
| Start menu | Shortcuts in both Start menu folders, three levels deep | Opens the shortcut | Same game-file test. Shown for review first. |
| Your folders | Each folder you add, one level deep | Runs the likeliest exe | Folders without game files are shown for review first. |
| Added by you | Exes you add by hand | Yes | None. |

Copies of the same game found by several sources are merged into one game with several installs. Games with the same Steam app id are the same game. Otherwise titles are compared after dropping cosmetic edition words (Deluxe, Gold, Game of the Year), while editions that are usually different builds (Remastered, Definitive, Special) stay separate. A launcher record whose folder is gone is shown as missing; a game whose folder moved keeps its place. You can hide any game, accept or ignore each uncertain find, and add folders or exes the scan missed.

## How a game's requirements are found

Finding a game on disk and finding its requirements are separate steps, so a game from Epic or GOG can still use Steam's or PCGamingWiki's requirements. The app tries, in order:

1. a match you chose with "Choose the correct game" (saved on this PC),
2. a Steam app id from the Steam library,
3. a GOG product id, matched to its PCGamingWiki page through PCGamingWiki's GOG lookup,
4. one exact title match on Steam, then on PCGamingWiki.

Anything less certain asks you to choose. When Steam fails or publishes nothing usable, PCGamingWiki is used and the app says so.

## How the comparison works

`specbench/hardware_db.py` holds a speed score for about 780 processors and 360 graphics cards. Requirement text such as "GTX 1060 6GB or RX 580" is matched against that table and your part's score is divided by the required part's score.

- 95% or more of the required score counts as meeting it, 80% to 95% is close, and below 80% is too slow.
- Memory and video memory: 95% or more meets it, down to about three quarters is close (games usually start, with stutter or lower texture settings), and less is not met.
- Video memory is checked whenever the listing gives an amount, whether it is in the graphics line (Steam) or its own field (PCGamingWiki). Integrated graphics are counted as borrowing up to half of system memory, and the app says it is an estimate.
- DirectX is checked against what the graphics chip supports, not the Windows version. Windows 10 and 11 ship the DirectX 12 runtime, but an old chip may still be unable to run Direct3D 12. The app asks Direct3D which feature levels each adapter supports. For a card in a profile or upgrade it estimates from the card's architecture and says so, and an unknown card stays unknown.
- When a requirement lists alternatives, your part is compared with the one from the same maker, otherwise with the easiest alternative. A laptop marker such as "Laptop GPU" or "Max-Q" only applies to the card it is written next to.

Every check is marked as checked, estimated, not listed or not checked. A requirement that was published but could not be matched, or a part of this PC that is not in the table, makes the answer "only partly checked" rather than a pass, and a definite failure always wins. Things listings mention that the table cannot check, such as AVX2, an SSD or ray tracing, are listed as not checked. When Steam and PCGamingWiki list materially different requirements, the app says so and keeps them apart.

The scores are hand-maintained approximations on a PassMark-like scale. They are good enough to tell "about the same" from "half as fast", and not good enough to predict frame rates or graphics settings.

Free disk space is treated as a warning, not a failure, and is not checked for games that are already installed.

## Data and privacy

- Hardware details, installed games and install folders stay on this PC. The local server listens on 127.0.0.1 only and refuses requests addressed to any other name.
- The network requests are to Steam and PCGamingWiki, which carry a game id or a title to search for, and the daily update check to GitHub, which carries nothing about this PC (see [Updates](#updates)).
- Requests that change something or start a game must come from the app's own page: they are JSON POSTs, and requests from other sites are refused.
- Saved data (profiles, wanted games, games you matched by hand, scan results) lives in `.data\` when run from source and in `%LOCALAPPDATA%\GameSpecCheck\data` when run from the exe. A file that cannot be read is renamed to `<name>.bad-<time>.json` and the app starts that file over and says so.
- Lookups are cached for a week in `.cache\` or `%LOCALAPPDATA%\GameSpecCheck\cache`. Delete that folder to force fresh data.
- Report images are made on this PC. User names and anything that looks like a file path are removed by default.

## Request limits

Valve publishes no request limit for the store endpoints the app uses. The figure developers usually quote, about 200 requests per five minutes, comes from a 2015 SteamDB blog post, not from Valve. The app sends Steam requests one at a time, at most one every 1.5 seconds. PCGamingWiki runs MediaWiki, whose API etiquette asks for requests in series; the app sends them one at a time with a short gap. GitHub allows 60 unauthenticated API requests per hour from one IP address. The update check sends at most one a day plus one per click, and does not retry. An HTTP 429 or 503, or GitHub's 403 with `X-RateLimit-Remaining: 0`, pauses that source for the Retry-After or reset time when given, otherwise for a growing backoff. Steam and PCGamingWiki requests are retried twice before the error is shown. All of this is in `specbench/net.py`.

## Command line

```
python run.py --specs            print what was detected, as JSON
python run.py --check 1245620    check one Steam app id and print the result
python run.py --port 9000        use another port
python run.py --no-browser       start without opening a browser tab
python run.py --no-update-check  do not ask GitHub for a newer version
```

## Tests

```
python -m unittest discover tests
python tools\smoke.py run.py
```

The unit tests cover the comparison rules, every discovery source (with synthetic launcher records, so no launcher needs to be installed), merging and identity, saved data, pasted requirements, reports, the planner, rate limiting, the update check (versions, malformed answers, rate limits, the digest check and the exe swap) and the server's request checks. `tests/js/logic.test.js` covers the page's search, filter and planner logic and runs when Node is installed. `.github/workflows/ci.yml` runs all of this on Windows with Python 3.8 and 3.13, then builds the exe and smoke-tests it.

## Layout

```
run.py                      entry point
specbench/specs.py          reads this PC's hardware through PowerShell
specbench/dx.py             DirectX support of each graphics adapter
specbench/hardware_db.py    speed scores
specbench/matcher.py        turns hardware names into table entries
specbench/requirements.py   parses Steam and PCGamingWiki requirement text
specbench/compare.py        the comparison and the wording of the answer
specbench/net.py            outbound requests: pacing, retries, HTTP 429
specbench/sources.py        Steam and PCGamingWiki lookups, with the cache
specbench/update.py         the update check and the one-click exe update
specbench/discovery/        installed-game discovery, one provider per source
specbench/identity.py       merges installs into games and finds their requirements
specbench/library.py        scans, saved choices and launching
specbench/checks.py         checks that combine a game, a profile and the sources
specbench/userdata.py       profiles, wanted games and matches you chose
specbench/paste.py          reads pasted requirements
specbench/planner.py        what an upgrade changes, game by game
specbench/report.py         the report image
specbench/store.py          safe saving of local JSON files
specbench/server.py         local web server and JSON API
specbench/paths.py          file locations, from source or inside the exe
web/                        the page itself (plain HTML, CSS and JavaScript)
build.bat                   builds the exe
requirements-build.txt      pinned, hash-checked build tools
tools/make_icon.py          draws the exe's icon
tools/make_version_info.py  writes the exe's Details tab
tools/smoke.py              starts the app or the exe and checks it works
tools/sign.ps1              signs the exe when a certificate is configured
LICENSE, NOTICE, CREDITS.md, licenses/   license, notices and credits
```

To add or correct a part, edit its line in `specbench/hardware_db.py`. The name is what gets matched and the number is its relative speed. To add a discovery source, write a function `scan(env, ctx)` that returns `{"status", "message", "installs"}` (see `specbench/discovery/launchers.py`) and add it to `PROVIDERS` in `specbench/discovery/__init__.py`.

## License and credits

Game Spec Check is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, copy and change it for noncommercial purposes. That is not an open source license in the Open Source Initiative's sense, because it does not allow commercial use.

[NOTICE](NOTICE) has the copyright line, the AI and donation statements, and the trademark notes. [CREDITS](CREDITS.md) lists the data sources and software the app relies on, including PCGamingWiki, whose content is available under a Creative Commons Attribution-NonCommercial-ShareAlike license. The running app shows the same information on its About page.

This project is not affiliated with or endorsed by Valve, SteamDB, PCGamingWiki, Epic Games, GOG, Microsoft, Electronic Arts, Ubisoft, Blizzard Entertainment, itch.io, NVIDIA, AMD, Intel or Anthropic.
