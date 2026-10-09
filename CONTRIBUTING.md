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

## Checks

Every pull request runs these in CI. Run them before you push:

```sh
make lint
make test
make web-test
```

`make test` needs `pytest`, `make lint` needs `ruff`, and `make web-test` needs `node`. No other
package is needed.

## Pull requests

- One topic per pull request, against `main`.
- The docs change in the same pull request: `docs/commands.md` for a command, `docs/configuration.md`
  for a setting.
- A change that moves filament says what was run on a printer, and with which ace2k version.
- Every pull request is reviewed by the maintainer before it is merged.
