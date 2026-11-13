# Game Spec Check

A small Windows app that answers one question: can this PC run that game?

It reads the hardware in your machine, looks up the requirements the game's publisher lists, and puts the two side by side with a plain answer on top. It does the same job as Can You Run It or PCGameBenchmark, but it runs on your own computer and your hardware details are never uploaded.

## About this project

- **A personal tool.** It was made for the author's own use and is shared as is, with no support and no promise of updates.
- **Written with AI.** The code was written by Claude, an AI model from Anthropic, through Claude Code. It has had no professional review or security audit, and some answers will be wrong.
- **Noncommercial use only.** It is licensed under the PolyForm Noncommercial License 1.0.0. Personal, hobby and study use is fine. Commercial use is not.
- **No donations.** The author is not accepting donations or sponsorship.

## Running it

### With the exe

Download `GameSpecCheck.exe` from the Releases page and double-click it. It needs Windows 10 or 11 and nothing else, Python included.

A console window opens and your browser goes to `http://127.0.0.1:8765`. Close the console window when you are done. Starting the exe a second time opens the copy that is already running.

The exe is not code-signed yet, so the first time you run it Windows may show "Windows protected your PC". Choose "More info", then "Run anyway". Some antivirus tools also flag single-file Python programs they have not seen before.

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

This creates a `.venv` folder, installs PyInstaller into it, runs the tests and writes `dist\GameSpecCheck.exe`.

### Signing the exe

The released exe is not signed. Code-signing certificates come from outside companies after an identity check, and most cost money. `build.bat` signs the exe when a certificate is configured and leaves it unsigned otherwise. Set one of these before running it:

```
set GSC_SIGN_THUMBPRINT=<thumbprint of a certificate in your Windows certificate store>
set GSC_SIGN_PFX=C:\path\to\certificate.pfx       (and GSC_SIGN_PFX_PASSWORD)
set GSC_SIGNTOOL_ARGS=<arguments for signtool.exe, for cloud or hardware-token signing>
```

`tools\sign.ps1` documents each option and checks the signature afterwards. A signature replaces "Unknown publisher" with the publisher's name, but SmartScreen can still warn about a new publisher until it builds a reputation.

## What you can do with it

- Search for a game by name and get an answer: runs well, runs, just about, or probably not. If it falls short, the app says which part is the problem and by roughly how much.
- Check every Steam game installed on the PC in one pass.
- Try a different processor, graphics card or amount of memory to see what an upgrade would change.
- Switch between Steam's requirements and PCGamingWiki's when both exist.

## Picking the right graphics chip

Laptops and many desktops report more than one graphics adapter: integrated graphics, a discrete card, and sometimes virtual displays from remote desktop or streaming software. The app scores each one separately, ignores the virtual ones, and uses the fastest. The order Windows lists them in does not matter. You can pick a different adapter under "Details and upgrades" if you want to see how a game does on integrated graphics.

Video memory is read from the registry value the driver publishes, because the usual Windows field stops at 4 GB.

## Where the requirements come from

- Steam's store API, for anything sold on Steam.
- PCGamingWiki, as a second source and for games that are not on Steam, such as Fortnite or Valorant.

SteamDB has no public API and asks not to be scraped. Its requirement data is Steam's own, which is what the store API returns, so the app reads that and links to the SteamDB page for each game.

Lookups are cached for a week, in `.cache/` when run from source and in `%LOCALAPPDATA%\GameSpecCheck\cache` when run from the exe. Delete that folder to force fresh data.

## How the comparison works

`specbench/hardware_db.py` holds a speed score for about 780 processors and 360 graphics cards. Requirement text such as "GTX 1060 6GB or RX 580" is matched against that table and your part's score is divided by the required part's score.

- 95% or more of the required score counts as meeting it.
- 80% to 95% is reported as close.
- Below 80% is reported as too slow.

When a requirement lists alternatives, your part is compared with the one from the same maker (an NVIDIA card against the NVIDIA card listed), otherwise with the easiest alternative. Laptop graphics chips are scored lower than the desktop card with the same name. Models missing from the table are estimated from their generation and tier, and the app says so when it does that.

The scores are hand-maintained approximations on a PassMark-like scale. They are good enough to tell "about the same" from "half as fast", and not good enough to predict frame rates. Vague requirements such as "2 GHz processor" or "512 MB video card" are turned into rough scores, and requirements too vague for that are shown as written.

Free disk space is treated as a warning, not a failure, since it is the one thing you can fix in a few minutes.

## Command line

```
python run.py --specs            print what was detected, as JSON
python run.py --check 1245620    check one Steam app id and print the result
python run.py --port 9000        use another port
python run.py --no-browser       start without opening a browser tab
```

## Tests

```
python -m unittest discover tests
```

## Layout

```
run.py                   entry point
specbench/specs.py       reads this PC's hardware through PowerShell
specbench/hardware_db.py speed scores
specbench/matcher.py     turns hardware names into table entries
specbench/requirements.py parses Steam and PCGamingWiki requirement text
specbench/compare.py     the comparison and the wording of the answer
specbench/sources.py     Steam and PCGamingWiki lookups, with the cache
specbench/steam_library.py  finds installed Steam games
specbench/server.py      local web server and JSON API
specbench/paths.py       file locations, from source or inside the exe
web/                     the page itself (plain HTML, CSS and JavaScript)
build.bat                builds the exe
tools/make_icon.py       draws the exe's icon
tools/make_version_info.py  writes the exe's Details tab
tools/sign.ps1           signs the exe when a certificate is configured
LICENSE, NOTICE, CREDITS.md, licenses/   license, notices and credits
```

To add or correct a part, edit its line in `specbench/hardware_db.py`. The name is what gets matched and the number is its relative speed.

## License and credits

Game Spec Check is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use, copy and change it for noncommercial purposes. That is not an open source license in the Open Source Initiative's sense, because it does not allow commercial use.

[NOTICE](NOTICE) has the copyright line, the AI and donation statements, and the trademark notes. [CREDITS](CREDITS.md) lists the data sources and software the app relies on, including PCGamingWiki, whose content is available under a Creative Commons Attribution-NonCommercial-ShareAlike license. The running app shows the same information on its About page.

This project is not affiliated with or endorsed by Valve, SteamDB, PCGamingWiki, NVIDIA, AMD, Intel, Microsoft or Anthropic.
