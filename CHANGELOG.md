# Changelog

## v0.4.0 — 2026-11-03 — beta

For ace2k `v0.13.0`. The changes since `v0.3.0-rc.1`. There was no final `v0.3.0`: this release
follows the release candidate.

**This release is a beta.** A print with a shared head (several lanes on one head, through a
splitter) needs a prime tower. The lane swap in a shared head is experimental: stay near the
printer during such a print.

### Breaking

- **A custom `ACEK_SWAP_LOAD` must purge** (2026-11-03). The U1's pre-extrusion at the end of a
  swap no longer purges its own 20 mm, so the macro's purge is the swap's only one. The default
  macro purges 30 mm. A custom macro with no purge leaves the nozzle unprimed
  ([`configuration.md`](docs/configuration.md), "The lane swap's macros").
- **The installer runs ace2k's flasher as one file, `ace2k_flash.pyz`** (2026-10-20). A release
  whose ace2k has no `ace2k_flash.pyz` is refused, and nothing changes.
- **A load, an eject and a print need the lane's material** (2026-10-25). A lane's material comes
  from its spool's tag, or from Edit spool on the web page. The U1's load of a lane with no
  material rolls the lane back out of the head and fails. Set Edit spool for a spool without a tag.
- **Every G-code command is named `ACEK_*`.** See the entry below, under "Earlier changes since
  the release candidate".

### New

- **Mounting notes** in the README and in [`install.md`](docs/install.md#mounting): PTFE tubes
  with a 2 mm inner diameter, and the units turned on before the U1 or with it.
- **The installer handles several units** (2026-10-27). It blinks each unit's LEDs and asks for
  its name, asks which lanes feed each head, flashes the units one at a time, and writes each
  unit's `uid:`. `--update` adds or takes out a unit
  ([`install.md`](docs/install.md), "Several units").
- **`ACEK_SPLITTER_MEASURE HEAD=n`** (2026-11-03) measures how far a lane of a shared head must
  roll back to leave its splitter, and stages `headN_splitter_mm` for `SAVE_CONFIG`
  ([`commands.md`](docs/commands.md#acek_splitter_measure)).
- **The Print tab** (2026-10-26 to 2026-11-02). It plans a print from a file: the file's colors, a
  lane for each, and where to put each spool for the fewest swaps. It shows the swaps along the
  print on a timeline with zoom, and the print's progress while it runs.
- **The dryer's mode on the page** (2026-10-26): Manual, Humidity and During print. Humidity
  shows only with `unattended_drying: True`, and it opens a warning first. The page names the new
  dryer fault `lid`: "The heater stopped: the box did not warm up — is the lid open?"
- **The dryer's temperature follows the lanes' materials** (2026-10-26), from the tags or from
  Edit spool.
- **A temperature outside the range of the lane's material is warned about** (2026-10-26).

### Changed

- **A faster lane swap** (2026-11-02). A docked head takes the slicer's pre-heat, so it is hot
  when its swap starts. The swap loads at one temperature, and the first wait for temperature
  after it does not hold the head. A swap takes about 95 s.
- **The end-of-print unload pre-heats every head** (2026-11-02), in order.
- **`runout_source: unit` also refills a head that one lane feeds** (2026-10-30): the print
  pauses at the end of the spool, the head is unloaded, and Resume loads the new spool.
- **A spool's tag is read when its lane moves** (2026-11-02), with ace2k `v0.13.0`. A spool left
  in its bay keeps its tag across a Klipper restart (2026-10-31).
- **The docs, the installer's messages and the page are in American English** (2026-10-31).
- **Many small fixes** from the code reviews of the adapter, the installer and the page.

### Earlier changes since the release candidate

- **Editing an ace2k key and restarting no longer stops the U1** (2026-10-17). With ace2k
  v0.13.0, changing `laneN_insert_mv`, the encoder scale or the other ace2k keys no longer makes
  Klipper restart inside its process, the restart the U1's RFID reader fails
  ([`install.md`](docs/install.md), "Troubleshooting").

- **After an emergency stop, Klipper comes back by itself** (2026-10-17). ace2k now sends one
  `FIRMWARE_RESTART` at a start that meets a board still stopped from an emergency stop or a lost
  unit ([`install.md`](docs/install.md), "Troubleshooting").

- **After an emergency stop** (2026-10-17). [`install.md`](docs/install.md), "Troubleshooting",
  says what `MCU shutdown` or `Can not update MCU config as it is shutdown` at start means: a
  board still stopped from an earlier emergency stop or a lost unit, which neither the init
  script's restart nor turning the printer off and on clears for the units. `FIRMWARE_RESTART`
  does. "Restart" no longer says that Firmware restart fails on the U1: it does not load new `.py`
  files, and can fail right after a `SAVE_CONFIG`.

- **A spool put into a shared head's bay goes to the parking point** (2026-10-17). It was pulled
  in only about 40 mm (the grip), because the grip was the only load without the tag search, and
  the search could reach the splitter. At 40 mm the probe's roll back of 100 mm took it out of
  the unit. Now the adapter asks ace2k for the park (host interface 15): the load goes to the
  parking point (about 300 mm), reads the tag on the way, and does not search past it. The
  probe's roll back leaves the lane in the unit. A unit on an older image is gripped as before,
  with one console line ([`install.md`](docs/install.md), "During the print").

- **An eject of a short filament ends done, not in error** (2026-10-17). A lane holding a few
  hundred millimetres could leave its end past the unit's drive gear, under the bay's sensor: the
  unload ended `unload_incomplete`, and the eject said it failed and left the lane in error. Now
  that end clears the lane's error and says to take the filament out of the bay by hand
  ([`commands.md`](docs/commands.md), `ACEK_EJECT`). An unload that carried a whole move still
  unloads again, as before. It stays a failure when the unload spent its whole length, during a
  print or one of the U1's own flows, and while the head's sensor still sees that filament.

- **Restarting Klipper without a terminal** (2026-10-17). [`install.md`](docs/install.md),
  "Restart", now names the extended firmware's Firmware Config page (Troubleshooting, Restart
  Klipper), which runs the same command as the terminal, and says not to use the Restart and
  Firmware restart buttons of Fluidd or Mainsail, which restart Klipper inside its process: on the
  U1 that fails and does not load new `.py` files.

- Fixed: `ACEK_EJECT` could take a filament held in the head's gear for a free one. While the
  follow is on, the tube and the buffer between the unit and the head can hold slack. The eject's
  test rolled the filament back only 10 mm, which took up that slack and nothing more. The unit
  then tried to unload the filament, met the gear and stopped with `eject failed: stuck`. Now the
  test rolls back up to 80 mm (`eject_probe_mm`, from 20 to 150 mm). It stops as soon as the
  buffer plunger is pulled tight, which means the head holds the filament. Without a proven link
  to the unit, the test stays 10 mm long. When the head holds the filament, the adapter now feeds
  back the length of filament that came out, not the motor's travel. If your configuration sets
  `eject_probe_mm` below 20, the adapter uses 20 and prints a warning when Klipper starts. Update
  ace2k's host module with the adapter: the longer test needs its new `sensors_reports`.
- New: if the unit's unload still stops `stuck`, `motor_stalled` or `tangled` within the first
  150 mm, and the head's sensor still sees filament, the eject goes on by itself. It does not
  during a print or while the U1 runs its own load or unload on that head. It clears the lane's error, feeds the filament back, unloads the head on the U1 and then
  unloads the unit. The console says `the unload met the head's gear … — unloading the head on
  the U1`. This happens once per eject.

- **The follow is on at Klipper's start.** ace2k now turns the follow on for every lane with
  filament when Klipper starts, whatever the head holds. Before, a lane waited for a load or a
  print. A filament you insert later goes into the follow once the U1 has pushed it to the head.
  The adapter needs ace2k's host interface 8. With an older one it does not attach, and the
  console says `ace2k host interface older than 8`. Update ace2k's host module and the adapter
  together.
- A move you type on a lane in the follow, such as `ACEK_FEED`, now pauses the follow and turns
  it back on afterwards. The adapter takes that quietly. Nothing else changes on the U1: each
  lane has its own head, so `ACEK_LANE_ACTIVE` is never needed.
- Fixed: at Klipper's start the adapter could start the follow twice on one lane, then report
  `follow stopped by hand; left off` and leave that lane off. A stop the adapter or ace2k sends
  is no longer read as one by hand.
- Fixed: each line of the adapter appeared twice in `klippy.log`.

- **Breaking: every G-code command is now named `ACEK_*`.** The old `ACE_*` names are gone, with
  no alias. For example, `ACE_EJECT` is now `ACEK_EJECT` and `ACE_ADAPTER_STATUS` is
  `ACEK_ADAPTER_STATUS`. ace2k's own commands changed the same way (`ACE_FEED` is now
  `ACEK_FEED`). This leaves the `ACE_*` names free for other ACE modules. When you update, replace
  ace2k's host module, the adapter's `.py` files and the web page together. With an older ace2k
  host module the adapter does not attach, and the console says
  `ace2k host interface older than 8`. Then update your own
  macros and every line of your configuration that calls one of these commands. See "Updating"
  in [`docs/install.md`](docs/install.md#updating).

- The dryer's PLA preset is 45 °C for 6 h. It was 50 °C.
- The install guide no longer describes the burn-in logger: its section "The burn-in logger
  (optional)" and the logger's steps in the upgrade and removal sections are gone.

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
