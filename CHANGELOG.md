# Changelog

## v0.3.0-rc.1 — 2026-10-09

First public release (Release Candidate).

ace2k-u1 is the Snapmaker U1 side of ace2k. It lets one ACE 2 Pro unit work with the U1's own
filament flows when each of the four heads is fed directly by one lane of the unit:

- load, unload, the print-start feed and the print-end unload;
- runout and tangle detection;
- spool tags carried to the printer, and a web page (`/ace2k/`) showing the unit, its lanes, the
  dryer and a chart of the chamber.

The adapter supports exactly one unit: lane n feeds head n.

See [`docs/install.md`](docs/install.md) and [`docs/commands.md`](docs/commands.md).

The long-run print test (burn-in) is pending.
