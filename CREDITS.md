# Credits

Game Spec Check is a personal tool that was written with AI and is licensed for
noncommercial use only. See [NOTICE](NOTICE) for the short version and
[LICENSE](LICENSE) for the terms.

## Written with

- [Claude](https://www.anthropic.com/claude), an AI model from Anthropic, used through
  [Claude Code](https://claude.com/claude-code). It wrote the code and the documentation
  from the author's instructions.

## Data

- **Steam store.** Game requirements, names and header images come from Valve's public
  store API, at the time you ask about a game. Steam is a trademark of Valve Corporation.
- **PCGamingWiki.** Its system requirement tables are the second source, and the only
  source for games that are not on Steam. The wiki's content is available under a
  Creative Commons Attribution-NonCommercial-ShareAlike license, as stated on its
  [copyright page](https://www.pcgamingwiki.com/wiki/PCGamingWiki:Copyrights). The app
  reads these tables when you open a game, shows them next to a link back to the page,
  and keeps a local copy in its cache. None of the wiki's content is stored in this
  repository.
- **SteamDB.** The app links to each game's SteamDB page. It does not read anything from
  SteamDB.
- **Launcher records on this PC.** Installed games are found from the files and registry
  entries that Steam, Epic Games, GOG, the Xbox app, EA, Ubisoft Connect, Battle.net and
  the itch app write on this PC. Nothing is read from those companies' services. GOG
  product ids are matched to PCGamingWiki pages through PCGamingWiki's own GOG lookup.
- **Speed scores.** The processor and graphics card scores in
  `specbench/hardware_db.py` are hand-written estimates on a scale similar to PassMark's.
  They are not exported from any benchmark site.

## Software inside the exe

- [Python](https://www.python.org/), under the Python Software Foundation License. The
  exe contains the Python runtime. The license text is in
  [licenses/PSF-LICENSE.txt](licenses/PSF-LICENSE.txt).
- [SQLite](https://sqlite.org/), which is in the public domain, comes with Python and is
  used to read the itch app's database without changing it.
- [PyInstaller](https://pyinstaller.org/) builds the exe. It is licensed under the GNU
  General Public License version 2 with an exception that lets programs built with it be
  distributed under other terms. PyInstaller is a build tool and is not part of the source
  code in this repository.

## Wording

- The text in the app and the documentation follows the
  [avoid-ai-writing](https://github.com/conorbronsdon/avoid-ai-writing) guide by Conor
  Bronsdon (MIT license).
