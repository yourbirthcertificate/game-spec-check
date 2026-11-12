# Game Spec Check

Local Windows app (Python standard library only, no dependencies) with a browser UI in `web/`.

- Run: `python run.py` (add `--no-browser` when previewing). Tests: `python -m unittest discover tests`.
- Python changes need a server restart. Files in `web/` are served fresh on reload.
- Steam results are cached raw in `.cache/` and parsed on every read, so parser fixes apply without clearing it.

## Wording

All user-facing text (UI strings, answer sentences in `specbench/compare.py`, README) follows
https://github.com/conorbronsdon/avoid-ai-writing. In short: no em dashes, no hype words, no
"it's not X, it's Y" framing, no filler or stacked hedges, sentence-case headings, bold used sparingly.
Say what is true about the PC and the game in plain words.
