# Security and safety

ace2k-u1 moves filament on a printer and runs a web page on it. The heater, the fans and the
motor limits live in the firmware, [ace2k](https://github.com/tobecwb/ace2k). A problem there is
reported in that repository, following its `SECURITY.md`.

## If a unit is misbehaving now

Switch the unit off at the mains and unplug it. Do not wait for a reply here.

## What to report privately

Report any of these in private, not as a public issue:

- the adapter moving filament when it should not: during a print on a lane in use, after a STOP,
  or after an error;
- anything that makes the adapter bypass a limit of the firmware;
- the web page running something the user did not ask for, or showing tag data as page content
  (a script stored on a tag, for example);
- any way for another device on the network to make the web page send commands the user did not
  send.

## How to report

Use **Security → Report a vulnerability** on this repository. That opens a private advisory that
only the maintainer can see. Include:

- the ace2k-u1 and ace2k versions (the release tags, or `git describe` for a build from source);
- the printer firmware version;
- the steps, the commands sent and the Klipper log;
- what happened, and what you expected.

This is a one-maintainer project. Expect a first reply within a week. A fix for a confirmed safety
problem comes before any other work.

## Supported versions

Only the latest release gets fixes. Before you report, check that the problem is still there on
the latest release.
