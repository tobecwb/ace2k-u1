# Contributing to ace2k-u1

Contributions are welcome. This page says what a pull request needs.

## Before you start

For anything larger than a small fix, open an issue first. Say what you want to change and why.

A change to how the unit itself behaves (the heater, the fans, the motors) belongs to
[ace2k](https://github.com/tobecwb/ace2k), not here. The adapter never carries a safety rule of
its own: the firmware enforces them.

## Rules

- The Python rules are ace2k's: see "Python rules" in
  [ace2k's CONTRIBUTING.md](https://github.com/tobecwb/ace2k/blob/main/CONTRIBUTING.md).
- Code, comments, documentation and commit messages are in English.
- Commit messages are in the imperative, prefixed by the module (`eject: wait for the buffer`), with
  no trailers.
- Per-unit data (UID, calibration, serial numbers) never enters the repository.
- `install.sh` is generated: edit `installer/*.sh`, then run `make installer`.

## Checks

Every pull request runs these in CI. Run them before you push:

```sh
make lint
make test
make web-test
make web-lint
make web-quality
```

`make test` needs `pytest`, `make lint` needs `ruff`, `make web-test` needs `node`, and
`make web-lint` needs the page's tools: run `npm ci --prefix web` once (it installs Biome,
TypeScript and knip, pinned in `web/package-lock.json`). `make test` also reads ace2k's
`klippy/extras`: check [ace2k](https://github.com/tobecwb/ace2k) out beside this repository
(`../ace2k`), or set `ACE2K_EXTRAS` to its `klippy/extras`.

## Quality gate

`make quality` checks the host code in `klippy/extras` and `scripts`, and then the page (see "The
page" below). For the host code it runs five tools:

- `xenon` (complexity): every function at C or better, and every module and the average at A;
- a module-size check: at most 1000 lines of code in a module. Blank lines, comments and
  docstrings do not count. A module over 1000 lines in total only gets a warning;
- `vulture` (dead code);
- `pytest` with a coverage floor of 97 %;
- `mypy --strict` on every host module.

Install the tools once, in a virtual environment:
`python3 -m pip install -r requirements-dev.txt`. The page's part also needs its tools: run
`npm ci --prefix web` once. Without them, `make quality`, `make web-lint` and `make web-quality`
stop at once and say so. Then run `make quality PYTHON=<that environment's python>`.

Run it before you open a pull request. It is not part of `make lint` and no hook calls it,
because the coverage step runs the whole test suite. The limits live at the top of the `quality`
target. They are final: a limit is never relaxed, and no ruff ignore or `vulture` entry is added
to make a change pass.

The tests follow the same ruff rules as the code, type hints on every function included. Six
codes are off for test files only:

- `PLR2004`: a number in an assertion is the point of the test;
- `ARG001`, `ARG002`, `ARG005`: a fake keeps the real signature, so it may not use every argument;
- `PLR0913`, `PLR0917`: a test helper may take many parameters.

`mypy` does not run on the tests.

### The page

The page in `web/` has its own limits. They come from `npm ci --prefix web` and run in
`make web-quality` (Biome lint is also `make web-lint`, which CI runs):

- Biome (`web/biome.jsonc`): the `recommended` rules, and cognitive complexity 15 for every
  function. Cognitive complexity counts nesting and breaks in flow, so it suits a page with many
  small branches better than the host's cyclomatic count;
- `tsc --strict` with `checkJs` (`web/tsconfig.json`): no type error in any source file. Every
  function has a JSDoc block with its types;
- a JSDoc check (`scripts/check_web_jsdoc.mjs`): every named function and method has a
  `/** ... */` block right above it. An anonymous callback and an arrow given to an object key
  need none, because `tsc` already knows their types;
- a module-size check (`scripts/check_web_module_size.mjs`): at most 1000 lines of code in a `.js`
  or `.css` file. Blank lines and comments, JSDoc included, do not count;
- `knip` (`web/knip.json`): no unused file, export or dependency;
- `node --test` with a coverage floor of 100 % of lines, 98 % of branches and 99 % of functions.
  Two files are not counted. `main.js` needs a browser. `theme-boot.js` is a plain script, not a
  module: its tests run its text in `node:vm`, so the coverage never sees it;
- a contrast check (`scripts/check_contrast.mjs`): text at 4.5:1, and the focus ring and the strong
  control edge at 3:1 against the panel, in both themes.

The JavaScript rules:

- ES modules, no framework, no build step.
- Every text the user sees goes through `t()`. Every value from the printer goes through `esc()`
  or `textContent`.
- Only `main.js` finds elements and owns the DOM. View modules return HTML strings. `theme-boot.js`
  is the one script that runs before the page paints.
- A module that needs `fetch`, a WebSocket, the clock, `toast` or `confirm` takes it as a
  parameter, so the tests can run it in `node`.
- Names are `camelCase`, constants `UPPER_SNAKE`, and a quantity carries its unit (`moveMm`,
  `nowS`).

The limits are final, like the host's: a limit is never relaxed. A `biome-ignore`, a `tsc` exclusion
or a `knip` ignore is never added to make a change pass, and each one that exists has its reason
written beside it.

## Pull requests

- One topic per pull request, against `main`.
- The docs change in the same pull request: `docs/commands.md` for a command,
  `docs/configuration.md` for a setting.
- A change that moves filament says what was run on a printer, and with which ace2k version.
- Every pull request is reviewed by the maintainer before it is merged.
