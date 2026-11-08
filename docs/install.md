# Installing on a Snapmaker U1

This guide shows how to install ace2k on a **Snapmaker U1 running the paxx extended firmware**,
together with the adapter `[ace2k_u1]` (`klippy/extras/ace2k_u1.py`, `config/ace2k-u1.cfg`). With
the adapter, the U1's own filament handling (load, unload, runout and the rest) drives the unit.
The guide also explains what the adapter does once it is installed.

You need ace2k `v0.14.0` on the unit and on the printer. The installer installs it. The adapter
does not attach to the U1 when ace2k's host interface is older than `API_VERSION` 12. ace2k
`v0.12.0-rc.1` has version 6 ([Troubleshooting](#troubleshooting)).

The installation steps cover **one** ACE 2 Pro unit. With one unit, lane n feeds head n by
default. The adapter also works with several units, and with several lanes on one head:

- **Several units.** Install each unit as ace2k's
  [Several units](https://github.com/tobecwb/ace2k/blob/main/docs/features.md#several-units)
  describes. Then name in `lanes:` the unit and the lane that feed each head: `a:1, a:2, b:1, b:2`
  ([Several units](configuration.md#several-units)).
- **Several lanes on one head.** With a splitter in front of a head, join its lanes with `+`:
  `a:1+a:2, a:3, a:4`. A print can then use more colors than the U1 has heads
  ([Printing more colors than heads](#printing-more-colors-than-heads)).

> [!IMPORTANT]
> **ace2k-u1 is a beta.**
>
> - A print with a shared head (several lanes on one head, through a splitter) needs a prime tower.
>   Turn the prime tower on in the slicer.
> - The lane swap in a shared head is experimental. Stay near the printer during such a print.

Every step on this page was run on a U1. Problems found on the way are listed under
[Troubleshooting](#troubleshooting).

## Contents

- [What the adapter does](#what-the-adapter-does)
- [Installation](#installation)
  - [Mounting](#mounting)
  - [Installing with one command](#installing-with-one-command)
  - [Several units](#several-units)
  - [Prerequisites](#prerequisites)
  - [Remove another ACE driver first](#remove-another-ace-driver-first)
  - [Install and flash ace2k](#install-and-flash-ace2k)
  - [Copy](#copy)
  - [Configure](#configure)
  - [Restart](#restart)
  - [Check](#check)
  - [Keep the calibration file](#keep-the-calibration-file)
  - [Updating](#updating)
- [How a spool's tag sets the head's filament](#how-a-spools-tag-sets-the-heads-filament)
- [When a spool runs out during a print](#when-a-spool-runs-out-during-a-print)
- [When the head still holds a piece](#when-the-head-still-holds-a-piece)
- [When the filament does not move](#when-the-filament-does-not-move)
- [When a lane fails during a print](#when-a-lane-fails-during-a-print)
- [How the follow turns itself back on](#how-the-follow-turns-itself-back-on)
- [Taking a filament out of its bay](#taking-a-filament-out-of-its-bay)
- [When a piece of filament is stuck in the tube](#when-a-piece-of-filament-is-stuck-in-the-tube)
- [Printing more colors than heads](#printing-more-colors-than-heads)
  - [The splitter and its tubes](#the-splitter-and-its-tubes)
  - [The slicer profile](#the-slicer-profile)
  - [Starting the print](#starting-the-print)
  - [Why the U1's screen and Fluidd are refused](#why-the-u1s-screen-and-fluidd-are-refused)
  - [During the print](#during-the-print)
  - [When a spool runs out in a shared head](#when-a-spool-runs-out-in-a-shared-head)
- [The web page](#the-web-page)
- [Troubleshooting](#troubleshooting)
- [After a U1 firmware upgrade](#after-a-u1-firmware-upgrade)
- [Removing ace2k and the adapter](#removing-ace2k-and-the-adapter)

## What the adapter does

By default, lane n feeds head n (extruder n-1) through its own tube. `lanes:` in `[ace2k_u1]`
can change which lane feeds each head ([Configuration](configuration.md)). Below, "the lane" is
the lane in that head.

The U1 has a module for its side feeders, called `filament_feed`. It runs the U1's load, unload,
preload, the feed at print start, the unload at print end, and the runout and tangle detection.
The adapter attaches to this module while Klipper runs. It replaces no file on the printer. With
the adapter, these flows drive the unit instead of the U1's own side feeders:

- **You put a filament into a bay.** First the unit loads it by itself (ace2k's `auto_load`), up to
  the filament's parking point. About 2 s after that load ends, once the lane has settled, the
  adapter tells the U1 about the filament. The U1's preload then pushes the filament toward the
  head. The push stops at the head's filament sensor.
- **The U1 loads a head** (its load or its manual feed). From the U1's first extrusion on, the
  adapter turns on the lane's **follow**. The follow is an ace2k mode that keeps the lane's buffer
  at rest, whichever way the head moves the filament. It feeds when the extruder pulls, and takes
  the filament back when the extruder pushes it back
  ([`protocol.md`](https://github.com/tobecwb/ace2k/blob/main/docs/protocol.md), "The follow").
  The follow is needed because the unit's motor cannot be turned from outside. If the lane were
  idle, the extruder would grind the filament. The follow stays on through the flush, after the
  load has finished, and through the print with its retractions.
- **While the follow is on**, ace2k's **feed-forward**
  ([Feeding ahead of the extruder](https://github.com/tobecwb/ace2k/blob/main/docs/features.md#feeding-ahead-of-the-extruder-feed-forward))
  feeds, in small amounts, what the head's extruder is about to use. It reads this from the moves
  Klipper has planned. This keeps the filament from being stretched between the follow's short
  feeds. Each lane is matched to its extruder automatically. When the adapter attaches, it tells
  ace2k which extruder each lane feeds: its head's extruder. You do not need to configure anything.
  A `laneN_extruder` key in `ace2k.cfg` overrides this match, so leave those keys unset on the U1.
- **The U1 unloads a head.** The U1's unload shapes the filament's tip by pushing it forward and
  pulling it back, in turn. So the follow stays on during the unload. If the follow was off, it is
  turned on when the tip shaping starts. When the unload ends, the follow is stopped. The unit then
  takes the filament out of its bay (ace2k's unload). The adapter retries this for a few seconds
  while the lane comes to a stop. In short, the lane of a loaded head follows from the first
  extrusion to the end of the unload.
- **`ACEK_EJECT LANE=n`** takes a lane's filament out of its bay by hand, whatever its head holds
  ([Taking a filament out of its bay](#taking-a-filament-out-of-its-bay)).
- **Something goes wrong.** The adapter stops the lane when:
  - something fails in a U1 flow;
  - a head leaves its finished load in any way other than an unload;
  - the filament leaves the bay outside the follow's tail (the next item explains the tail).
- **A spool runs out at the bay** (or the filament is cut there) while the follow is on. The unit
  keeps feeding the rest of the filament, called the **tail**, until its end has left the unit's
  motor. The head prints with the filament left in the tube
  ([When a spool runs out during a print](#when-a-spool-runs-out-during-a-print)). The setting
  `runout_source` decides what pauses the print:
  - `head` (the default): the head's own sensor, when the end of the filament reaches it;
  - `unit`: the adapter, as soon as the bay's sensor sees the end. The head is unloaded and the
    rest of the filament rolls back out of the bay, as in a shared head
    ([When a spool runs out in a shared head](#when-a-spool-runs-out-in-a-shared-head)). There is
    no tail then. If the head's sensor is disabled, its pause on runout is off or the print is
    ending, the U1 would not pause: the lane keeps its tail, as with `head`.

  With `head`, insert a new filament only when the console says "the end is out of the unit — a
  new filament can go in". Until then, the old filament's end still holds the unit's gear. Then
  push the new filament in to the end, press the lever, and push a little more until it is past
  the drive gear ([Inserting the new filament](#inserting-the-new-filament)). With `unit`, wait
  instead for "… ran out — its rest is out of bay …: take it out, put a new spool in and RESUME".
  A spool put in before that line waits in the bay until `RESUME`.
- **You put in a filament while the head still holds a piece that is not this lane's current
  filament.** For example: the old piece after a runout, a filament left in the head after a
  tangle, or the head's filament after its bay was emptied. The unit then only **grips** the new
  filament. It pulls it about 40 mm (`grip_mm`), until its gear holds it, and does not search for
  a tag. The filament waits there. The old piece is never touched. The adapter does not tell the
  U1 about the new filament while the head's sensor sees filament
  ([When the head still holds a piece](#when-the-head-still-holds-a-piece)). On a head that one
  lane feeds, a unit with ace2k v0.13.0 or later pulls nothing: the filament **waits** in the bay
  until the U1 loads the head ([The wait](#the-wait)).
- **A lane error during a print.** While the follow is on, the filament gets stuck or tangled, the
  lane times out, or its motor stalls. The adapter then pauses the print at once, through the U1's
  own tangle pause for that head.
- **The follow turns itself back on for a loaded head.** After a lane error and `ACEK_CLEAR`, or
  after the U1 resumes, the lane follows again. If you switch the follow off by hand, it stays off
  until the head's next load.
- **You put a spool with a tag into a bay.** Once the unit has read the tag, the head's filament
  setting on the U1 takes the tag's values: vendor, type, subtype and color. When the spool leaves
  the bay, the head is set back to unknown, as the U1 does for any spool
  ([How a spool's tag sets the head's filament](#how-a-spools-tag-sets-the-heads-filament)).

The U1's own messages and error codes stay the same. A push that fails shows up as the U1's own
preload or load error.

In the commands below:

- `<printer>` is the printer's address on your network.
- `<serial>` is the serial number of the unit's USB adapter.
- Commands with `scp` run on your computer, in the directory that holds the `ace2k` and
  `ace2k-u1` trees side by side.
- The other commands run on the printer, logged in as `root`.

## Installation

### Mounting

Do this before you install the software.

**The tubes.** Use PTFE tubes with a 2 mm inner diameter between each unit and its head. When the
U1 unloads a head, it forms a thick tip at the end of the filament. A narrower tube can catch that
tip. The filament then cannot go back to the unit, and a new load cannot reach the head. 2 mm
works on the author's printer. With a splitter, the tube after it needs the same care
([The splitter and its tubes](#the-splitter-and-its-tubes)).

**The order you turn things on.** Each unit is a Klipper MCU, and each has its own power supply.
Klipper connects to every MCU while the U1 starts.

1. Turn the units on.
2. Turn the U1 on, after the units or at the same time.

If the U1 finishes its start before the units are online, Klipper stops with an error. To recover,
turn the units on, then run `FIRMWARE_RESTART` in the console (Fluidd's Firmware restart).

### Installing with one command

`install.sh` is an installer that runs on the printer. It installs ace2k and this adapter, updates
them, or removes them. Use it instead of the manual steps below.

Log into the printer with `ssh root@<printer>`, then run:

    curl -fsSL https://raw.githubusercontent.com/tobecwb/ace2k-u1/main/install.sh | sh

It asks before it changes anything. On a new install it:

1. checks the printer: the paxx firmware, `/oem/.debug`, no print running, no other ACE driver;
2. downloads the latest release of this adapter and the ace2k release it was tested with, and
   checks every file against the release's `SHA256SUMS`;
3. finds every unit's USB cable. With more than one unit, it blinks each unit's LEDs in turn and
   asks which name the unit gets (`a`, `b`, …). The last unit is not blinked: it takes the first
   free name, and the installer says which. A single unit is `a`;
4. asks which lanes feed each of the four heads ([Several units](#several-units) says how to
   answer);
5. shows the plan and asks once;
6. writes the host modules, the adapter and the web page;
7. stops Klipper and flashes the units one at a time. The flasher shows what it found and asks
   once more. It saves a copy of each unit's factory calibration in
   `/home/lava/printer_data/ace2k/calibration/`;
8. writes `ace2k.cfg` (one section per unit), `ace2k_tags.cfg` and `ace2k-u1.cfg` (with your
   `lanes:`);
9. starts Klipper and waits until it reports the new ace2k version on every unit;
10. writes each unit's `uid:` into `ace2k.cfg`, so that two swapped cables are refused at the next
    start;
11. runs `ACEK_HEALTH` and `ACEK_STATUS` for you, and says whether each one is OK.

The printer is never rebooted. Klipper is stopped once for the flashes and started once after the
config is written.

Klipper can also stop with an error right after one of these restarts. That happens when the unit
still holds the settings of an earlier Klipper: Klipper resets the unit and restarts inside its own
process, and on the U1 that restart fails ([Restart](#restart)). The installer then restarts Klipper
once more, at most once after each restart of its own.

If a step fails after Klipper was stopped, the installer starts Klipper again and says so. A cable
unplugged before its unit's turn is such a failure: the run stops with a message that names the
unit. If you interrupt the installer (Ctrl-C), Klipper stays stopped: it says how to start it. A
flasher that does not take `--config-follows` (an older ace2k release) is refused before any
change.

When ace2k is already installed, the installer asks: update or remove. An update keeps your
`ace2k.cfg` and `ace2k-u1.cfg`. When an earlier run stopped before the end, it says so and asks
whether to continue it instead. On a new install, the `.cfg` files are written only once the unit
has been flashed.
Before it replaces or deletes a file, it copies it into one directory for the run, under
`/home/lava/printer_data/ace2k/backup/`. It does not put files back by itself. If a step fails,
it stops and says what failed and where that copy is. Fix the cause and run the same command
again: it starts from what is on the printer, and the flasher also handles a unit left in recovery
mode.

Options go after `sh -s --`, for example `… | sh -s -- --remove`:

| Option | What it does |
|---|---|
| `--update` | update without asking |
| `--remove` | remove without asking |
| `--restore-stock FILE` | with `--remove`: first put the unit back on the original Anycubic firmware from FILE ([ace2k's flashing guide](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md#back-to-the-factory-firmware) says where to get it). The installer checks the file's SHA-256 and refuses any other file |
| `--force` | with `--restore-stock`: accept a file with another SHA-256, with a warning |
| `--yes` | answer yes to every question |
| `--port PATH` | The unit's USB adapter, on a new install of one unit. `--update` ignores it. `/dev/serial/by-id/usb-1a86_USB_Single_Serial_<serial>-if00` |
| `--pre` | include pre-releases |
| `--version TAG` | install this release of the adapter instead of the latest. A release whose ace2k has no `ace2k_flash.pyz` is too old for this installer. It is refused, and nothing changes |
| `--from DIR_OR_URL` | take the release files from a directory or a URL instead of GitHub |

The steps below do the same by hand.

### Several units

The installer names each unit after you see it. On a new install with more than one unit, and
when `--update` finds a cable that the config does not name, the unit on that cable blinks its
four LEDs together for a few seconds, and the installer asks:

    The unit whose LEDs blink now: which name? (a, b) [a]

On a new install, the last unit is not blinked: it takes the first name left, and the installer
says so (`The unit on <cable> is b.`). `--update` blinks every new unit. Two units never blink at
the same time. If you answer quickly, the installer waits a few seconds until the last unit has
stopped, then blinks the next one.

A unit that cannot blink is named another way. This is an older ace2k, or a unit in recovery,
whose LEDs already strobe fast. The installer asks you to unplug the cable of the unit that should take the
name, then press Enter. Exactly one cable must be missing; with none or several it asks again.
Plug the cable back in when it says so.

Then it asks which lanes feed each head, one head at a time:

    Head 1 (T0): which lanes feed it? [a:1]

Press Enter to take the suggestion in brackets. Write one lane as `a:1`. Two or more lanes on the
same head, through a splitter, are joined by `+`: `a:1+a:2`. A `-` leaves the head to the U1.

- **Adding a unit:** plug in its cable, turn it on, and run the installer with `--update`. It
  offers the new unit (`Add the unit whose LEDs blink now as b? [y/N]`). A config with one
  unnamed `[ace2k]` becomes unit `a` at the same time, with its settings and its calibration.
- **A new unit that cannot blink:** it is added by unplugging its cable. If any other cable is
  unplugged too, the installer asks for every cable back and leaves the new unit out of this run:
  run `--update` again.
- **A unit that is missing:** the update stops before it changes anything. Plug the unit in, or
  answer `y` to take it out of the config. If every unit's cable is missing, the update stops: use
  `--remove` to take everything out.
- **`--yes`** cannot name units: with two or more new units it stops, and a new unit on an update
  is left out. Run those from a terminal.
- **`--port`** is for a new install of one unit. `--update` ignores it, with a warning: the units
  come from `ace2k.cfg`.
- **`uid:`** is written for each unit once Klipper reports it. It is the unit's own identifier, so
  two swapped cables stop Klipper with a message instead of feeding the wrong head.

### Prerequisites

- **The paxx extended firmware `v2.0.0-paxx12-24`**, installed over the U1's stock firmware.
  This guide was tested on it. To install it, follow paxx's own guide,
  [`install.md`](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/main/docs/install.md).
  In short, you copy its upgrade file to a USB drive formatted as FAT32, and you start the upgrade
  from the printer's screen. Its Klipper is older than the one ace2k is built against. The one
  difference that matters is handled in [Install and flash ace2k](#install-and-flash-ace2k).
- **Root access over SSH.** paxx turns it on. The default root password is in paxx's
  [`ssh_access.md`](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/main/docs/ssh_access.md).
  Put your key in with `ssh-copy-id root@<printer>`. The rest of the steps then need no password.
- **`/oem/.debug` present.** Create it with `touch /oem/.debug` (the installer offers to). No
  reboot is needed: the files written now survive the next boot, because the U1 wipes its overlay
  at boot only when this flag is missing. Here is why it matters:
  - On the U1, Klipper loads extras only from `/home/lava/klipper/klippy/extras/`.
  - That directory keeps its changes across a reboot only while `/oem/.debug` exists. The same is
    true for `/etc` (paxx's
    [`data_persistence.md`](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/main/docs/data_persistence.md)).
  - Without it, ace2k's modules are gone at the next boot.
  - paxx `v1.6.0-paxx12-22` has no other user hook at boot that could restore them from
    `printer_data`. Its `/etc/hooks/klipper.d/*.sh` scripts run at every Klipper start, but they
    live in `/etc`, which needs `/oem/.debug` too.
- **The unit's USB cable** plugged into the printer
  ([ace2k's `docs/cable.md`](https://github.com/tobecwb/ace2k/blob/main/docs/cable.md)).
- **On Windows**, ace2k's guide covers the tools: Windows Terminal's `ssh` and `scp`, or WinSCP
  and PuTTY. The same tools work here. In PowerShell, a command split over lines with `\` must be
  written on one line.

### Remove another ACE driver first

If the U1 runs another ACE driver, remove it **with its own uninstaller** before you install
ace2k. Then restart Klipper once with no ACE configured. The reason: that driver opens every USB
adapter with the same vendor ID as the unit's converter, including ace2k's unit. With both
installed, two programs fight over one port.

Afterwards, check that none of its files is left in `/home/lava/klipper/klippy/extras/`. Backups
of its configuration can stay if they are not `.cfg` files, or if they are outside
`printer_data/config/extended/klipper/`. Klipper does not read them.

### Install and flash ace2k

Follow ace2k's [First install](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md#first-install)
from start to end: the release, the files, the unit's USB path, the flash and the check. On the
U1, three things are different:

- Klipper lives in `/home/lava/klipper`, so ace2k's host module goes to
  `/home/lava/klipper/klippy/extras/`.
- Do not add sections to `printer.cfg`. Instead, copy ace2k's `config/ace2k.cfg` and
  `config/ace2k_tags.cfg` to `/home/lava/printer_data/config/extended/klipper/`. paxx includes
  every `.cfg` in that directory. In that copy of `ace2k.cfg`, set the unit's adapter:

      [mcu ace2k]
      serial: /dev/serial/by-id/usb-1a86_USB_Single_Serial_<serial>-if00

  Then **comment out the MCU temperature section**:

      #[temperature_sensor ace_mcu]
      #sensor_type: temperature_mcu
      #sensor_mcu: ace2k

  That section uses Klipper's generic analog-in query. The U1's older Klipper uses a different
  format for this query than ace2k's image. If the section stays active, Klipper stops at its
  first start with `Unable to encode: query_analog_in`. Commenting it out loses only the MCU's
  chip temperature. The three temperatures above it (`ace_a_ptc_left`, `ace_a_ptc_right`,
  `ace_a_chamber`) come from ace2k's own report and still work. `ace2k_tags.cfg` (the spool brands)
  needs no change.
- Klipper is restarted with `/etc/init.d/S60klipper restart` ([Restart](#restart)).

When it is done, `ACEK_STATUS` reports `ace2k 0.14.0` and `ACEK_HEALTH` reports `OK`.

### Copy

This step copies the adapter's files and its web page. First get the release on your computer:
`git clone --branch v0.5.0 https://github.com/tobecwb/ace2k-u1`. Then, from the directory
that holds the `ace2k-u1` tree, run:

    scp ace2k-u1/klippy/extras/ace2k_u1*.py root@<printer>:/home/lava/klipper/klippy/extras/
    scp ace2k-u1/config/ace2k-u1.cfg root@<printer>:/home/lava/printer_data/config/extended/klipper/
    ssh root@<printer> 'rm -rf ace2k-web-src && mkdir ace2k-web-src'
    scp -r ace2k-u1/web/index.html ace2k-u1/web/main.js ace2k-u1/web/theme-boot.js \
        ace2k-u1/web/presets.json ace2k-u1/web/js ace2k-u1/web/css ace2k-u1/web/i18n \
        ace2k-u1/web/vendor ace2k-u1/web/nginx root@<printer>:ace2k-web-src/

The page is copied file by file and folder by folder. So the page's development tools never go to
the printer: `web/node_modules`, if you installed them, is large and the page does not use it.

The `ace2k_u1*.py` pattern copies every file of the adapter. Besides `ace2k_u1.py`, they are:

- `ace2k_u1_adapter.py`: the hooked lanes, their motions and the unit's events.
- `ace2k_u1_bench.py`: the bench commands `ACEK_HEAD_PROBE` and `ACEK_FREE_CHECK`.
- `ace2k_u1_common.py`: the records and small helpers that the other files share.
- `ace2k_u1_config.py`: reads the section's settings from `printer.cfg`.
- `ace2k_u1_eject.py`: `ACEK_EJECT`, which takes a lane's filament out of its bay.
- `ace2k_u1_eject_job.py`: the record of one `ACEK_EJECT` and its steps, and the log of the
  lane while the U1 unloads the head.
- `ace2k_u1_events.py`: what the unit's feed events end of the lanes' motions.
- `ace2k_u1_follow.py`: the follow, which keeps a loaded lane's buffer at rest.
- `ace2k_u1_grip.py`: the grip, the end of a spool (its tail) and the watch of each bay.
- `ace2k_u1_heads.py`: watches the heads that several lanes feed.
- `ace2k_u1_history.py`: records the unit's history for the web page.
- `ace2k_u1_hooks.py`: the hooks into the U1's side-feeder module, and the checks of them.
- `ace2k_u1_material.py`: knows a lane's material before its load, its eject and a print.
- `ace2k_u1_page.py`: the commands that the web page sends.
- `ace2k_u1_pause.py`: the U1's tangle and runout pauses.
- `ace2k_u1_plan.py`: reads a G-code file's tools and suggests a lane for each.
- `ace2k_u1_printer.py`: the one place that reads and writes the U1's own state.
- `ace2k_u1_printer_types.py`: describes the U1's objects for the type checker; nothing in it runs.
- `ace2k_u1_probe.py`: finds which lane is in a head that several lanes feed.
- `ace2k_u1_push.py`: pushes a lane's filament to its head, also for the U1's preload.
- `ace2k_u1_refill.py`: handles a spool that runs out in a head that several lanes feed.
- `ace2k_u1_rollback.py`: rolls a swap's old lane, or a refill's rest, back from the head.
- `ace2k_u1_screen_unload.py`: the Unload of the U1's screen, cold when the lane in the head
  is free.
- `ace2k_u1_splitter.py`: `ACEK_SPLITTER_MEASURE`.
- `ace2k_u1_spools.py`: knows each lane's spool and keeps the records of `ACEK_SPOOL`.
- `ace2k_u1_status.py`: the status that the web page reads.
- `ace2k_u1_swap.py`: swaps the lane of a head that several lanes feed.
- `ace2k_u1_tag_search.py`: reads a spool's tag with motion when the unit's own load left it
  unread at the parking point.
- `ace2k_u1_tags.py`: sets each head's filament setting on the U1 from the spool's tag.
- `ace2k_u1_temps.py`: warns when a head's temperature does not fit the material of the lane
  in it.
- `ace2k_u1_tracker.py`: follows a print through its plan.
- `ace2k_u1_unload_watch.py`: the log of the lane while the U1 unloads the head.
- `ace2k_u1_wraps.py`: the U1 commands that the adapter wraps, such as `T0`…`T31`.

The web page needs the recorder, `ace2k_u1_history.py`.
Then, on the printer, put the page in place and let nginx serve it:

    rm -rf /home/lava/ace2k-web && mkdir -p /home/lava/ace2k-web
    cp -r ace2k-web-src/. /home/lava/ace2k-web/
    rm -rf /home/lava/ace2k-web/nginx /home/lava/ace2k-web/js/types.js
    chmod -R a+rX /home/lava/ace2k-web
    cp ace2k-web-src/nginx/ace2k.conf /etc/nginx/fluidd.d/ && nginx -t && nginx -s reload
    rm -rf ace2k-web-src

The first line removes the old page. So a file that the new release no longer has does not stay
behind. `js/types.js` only describes types for the checks on a computer, and the page does not
load it. The `chmod` lets nginx read the page. Root on the printer has `umask 0077`, so the copies
are made readable by root only, and without it nginx answers 403. `nginx -t` reports that the
configuration is successful.

### Configure

Nothing needs to change to get started. `ace2k-u1.cfg` works as copied: every setting is commented
out at its default.

To change a setting later:

1. Open `/home/lava/printer_data/config/extended/klipper/ace2k-u1.cfg` on the printer. Each
   setting has a comment that says what it does.
2. Remove the `#` in front of the setting.
3. Set the value.
4. [Restart](#restart).

Every setting, with its default and range, is in [`configuration.md`](configuration.md).

### Restart

On the printer:

    /etc/init.d/S60klipper restart

This restarts the whole Klipper process, which loads the new modules and `.cfg` files. Use it now,
and every time you change a `.cfg` file or copy new `.py` files.

Without a terminal, the extended firmware's **Firmware Config** page does the same:
`http://<printer>/firmware-config/`, **Troubleshooting**, **Restart Klipper**. It runs the same
command. The page is there only with Advanced Mode on (on the touchscreen: **Settings >
Maintenance > Advanced Mode**).

**The Restart and Firmware restart buttons of Fluidd or Mainsail do not replace it** (nor the
`RESTART` and `FIRMWARE_RESTART` commands). They restart Klipper inside its running process. That
does not load new `.py` files, and right after a `SAVE_CONFIG` it can fail as described below. On
the U1 the web interface has no button that restarts the Klipper service itself. Firmware restart
is still the right command after an emergency stop ([Troubleshooting](#troubleshooting)).

**Use it after `SAVE_CONFIG` too.** Some commands store values with `SAVE_CONFIG`, for example
`ACEK_CALIBRATION_SAVE` after an encoder calibration. `SAVE_CONFIG` writes the values and then
restarts Klipper by itself. On the U1 that restart fails: Klipper stops with `Internal error
during connect`. The cause is the U1's own RFID reader module. It cannot take back a GPIO line it
still holds (`Device or resource busy`). Nothing is lost, because the values were already written.
Run the command above, and Klipper starts with them.

### Check

In Klipper's console:

- `ACEK_STATUS`: the firmware version (`0.14.0`), the four lanes (`idle`, with or without a
  filament), the temperatures, the mains frequency, and the dryer `idle`.
- `ACEK_HEALTH`: `OK`.
- About a second after Klipper is ready, the console line `ace2k_u1: hooked lanes [1, 2, 3, 4]`.
  "Hooked" means the adapter is attached to these lanes. They are the lanes of the `lanes` setting.
- `ACEK_FEED_FORWARD`: `ace2k: feed-forward lane 1 on (extruder) lane 2 on (extruder1) …`. Each
  hooked lane is on and matched to its head's extruder.
- `ACEK_ADAPTER_STATUS`: one line per hooked lane, with:
  - `lane n -> e<head>`;
  - whether the U1 sees a filament in its bay;
  - the U1's state of that channel;
  - the head's filament sensor;
  - the lane's wheel count;
  - `follow=`: `wanted` or `off`
    ([How the follow turns itself back on](#how-the-follow-turns-itself-back-on));
  - `grip=`: one of these:
    - `<grip_mm> mm` (`40 mm` by default) while the lane's grip is set on the unit;
    - `the park` while the bay of a head that one lane feeds waits for the new spool of a
      pending refill, which is loaded to the parking point with no tag search;
    - `the wait` while the bay of a head that one lane feeds waits for the U1's load
      ([The wait](#the-wait));
    - `off`;
    - `off (wanted, to be set again)` once its load or a start has used the grip while the head
      still holds the piece;
  - `tag=`: the lane's tag state (`read`, `no_tag`, …), with the tag's brand, name and color;
  - `applied=`: what the tag last did to the head. One of these:
    - the vendor, type, subtype and color written;
    - `<RRGGBBAA> only — material '<material>' unknown to the U1` when only the color was
      written;
    - the bare reason, `material '<material>' unknown to the U1`, when nothing could be written;
    - `not applied — <reason>` when the write was refused;
    - `none` after a clear, or before any write.

  With the tags off, a first line `ace2k_u1: tags off: <reason>` says why.

`klippy.log` shows `Loaded MCU 'ace2k' … commands` and `ace2k … connected, link proven: True`.

### Keep the calibration file

When you flashed the unit, the tool saved a copy of the unit's factory calibration to a file on
the printer, in `~/ace2k-flash`. If you ever go back to the factory firmware, the tool compares
the unit with this file to confirm the calibration is still the same. Files on the printer can be
lost (for example, in a U1 firmware upgrade), so copy it to your computer and keep it:

    scp 'root@<printer>:ace2k-flash/ace-calibration-*.txt' .

### Updating

Run the installer with `--update`:

    curl -fsSL https://raw.githubusercontent.com/tobecwb/ace2k-u1/main/install.sh | sh -s -- --update

The update flashes only the units that are not at the new version, one at a time, with Klipper
stopped once and started once. A config with no unit says to run `--remove`, then install again.

The installer keeps your `ace2k.cfg` and `ace2k-u1.cfg`. When the new release's `ace2k-u1.cfg`
has a section yours lacks (for example the two lane swap macros), it adds that section at the end
of yours, with the comment block above it, and says which sections it adds. Nothing else in your
file changes. A section you commented out (`#[gcode_macro …]`) counts as present and is not added
back. A new setting inside a section you already have is not added: compare by hand, as step 4
below says.

The temperature sensors are named after their unit since ace2k 0.13.0 (`ace_a_chamber`,
`ace_a_ptc_left`, `ace_a_ptc_right`). An `ace2k.cfg` written earlier keeps the old names
(`ace_chamber`, `ace_ptc_left`, `ace_ptc_right`): the update does not rename them and adds no
sensor. To use the new names, rename the three section headers in your `ace2k.cfg` by hand. The
graph of a renamed sensor starts empty.

By hand, the steps are below. To update from an earlier release:

> **Updating from v0.3.0-rc.1 or earlier.** Every G-code command is now named `ACEK_*`. For
> example, `ACE_EJECT` is now `ACEK_EJECT` and `ACE_FEED` is now `ACEK_FEED`. Klipper answers
> `Unknown command` to an old name and does nothing. So do all the steps below in one go: ace2k's
> host module, the adapter's `.py` files and the web page must all come from the new release.
> The old web page sends the old names. Then look for `ACE_` in your own macros and in your
> slicer's start and end G-code, and rename each command to its `ACEK_` name.

1. Flash the unit with the new image and copy ace2k's new host module, as ace2k's
   [Updating to a newer ace2k](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md#updating-to-a-newer-ace2k)
   says.
2. Copy the adapter's new `.py` files, as in [Copy](#copy).
3. Copy ace2k's new `ace2k_tags.cfg`.
4. Do **not** copy `ace2k.cfg` or `ace2k-u1.cfg` over the printer's copies. The printer's copies
   hold your own values: the unit's `serial`, the commented-out MCU temperature section, and the
   encoder scales `SAVE_CONFIG` wrote. Instead, compare them with the release's files. Add any new
   setting by hand, commented out at its default, as the release's files have it.
5. Update the web page with the same `scp -r` and copy as in [Copy](#copy).
6. [Restart](#restart), then reload the web page's browser tab.

## How a spool's tag sets the head's filament

The unit reads a spool's tag while it loads the filament (ace2k's
[spool tags](https://github.com/tobecwb/ace2k/blob/main/docs/features.md#spool-tags)). For each
hooked lane, the adapter writes what the tag says into its head's filament setting on the U1.
This is the setting the U1's screen shows for that head. The U1's filament table also takes the
head's settings from it. The adapter writes it with the U1's own `SET_PRINT_FILAMENT_CONFIG`, the
G-code the screen uses.

### A spool with a tag

As soon as the lane's tag is `read`, the head gets:

- **The vendor**: the tag's brand. Every character other than letters, digits, `_`, `+` and `-`
  is dropped, spaces included (`Bambu Lab` → `BambuLab`). With no brand, the vendor is `Generic`.
- **The type**: the tag's material, in upper case, as one of the U1 table's types. It is the same
  type, or else the longest type that the material starts with, followed by a space, `+`, `-` or
  `_` (`PLA+` → `PLA`, `PETG-HF` → `PETG`).
- **The subtype**: the words of the tag's name after the type, spaces kept (`PLA Matte` → `Matte`,
  `PLA Full Spectrum` → `Full Spectrum`, `PETG-HF Fast` → `HF Fast`). If the name does not start
  with the type, the whole name is the subtype (`Matte PLA` → `Matte PLA`). With no name, the
  subtype is what follows the type in the material (`PETG-HF` → `HF`, `PLA+` → none). Characters
  other than letters, digits, spaces, `_`, `+` and `-` are dropped, and repeated spaces become one.
- **The color**: the tag's color. It is opaque when the tag gives no alpha.

The U1's screen lists only the vendors and subtypes of its filament table (Snapmaker, Polymaker
and Generic, each with its own subtypes). A vendor and subtype it does not list are written as
`Generic` (`Bambu Lab PLA Lite` → `Generic PLA`). The subtype stays when the table has a
`Generic` entry with that subtype. The temperatures were never affected: for a name it does not
list, the U1 already falls back to its `Generic` entry of the type.

The tag wins over a setting you made by hand. The write happens at once, whether the printer is
idle, printing or paused. The one exception is the head that is printing at that moment. Its write
waits for a pause or the end of the print. It then writes the tag of the spool that is in the bay
by then, if any. If the adapter cannot tell which extruder is active during a print, the writes of
all hooked heads wait.

If the same tag is read again, and the spool has not left the bay, nothing is written again. So a
change you make by hand afterwards stays until you put the spool in again. **Read tag** on the web
page (or `ACEK_RFID_FORGET LANE=n`) puts the lane's tag back to pending. The unit reads it again at
the lane's next move. That read is written to the head again, even when it is the same tag.

After a Klipper restart, a lane whose spool stayed in its bay keeps the tag read before the
restart. Its material and color count for the lane, and the page shows them from the tag, until
the unit reads the tag again. Taking the spool out drops that read, and so does **Read tag**. A
spool swapped while the printer was off keeps the old read until its tag is read again: use
**Read tag** then.

### A tag not read at the parking point

The unit's own load of a spool you put in goes to the parking point and reads the tag on the way.
It searches past the parking point only when every other lane of the unit is idle. With another lane
following, the load stops at the parking point and the tag stays pending.

About 2 s after such a load ends with the tag still pending, the adapter starts the unit's read with
motion once, as `ACEK_RFID_READ LANE=n MOVE=1` does. The unit takes a follow at rest as idle,
searches up to `rfid_search_mm` from where the filament is or until it meets an obstacle, and comes
back. This holds for every lane, on a head that one lane feeds or on a shared head. On a shared
head the obstacle may be the other lane's filament past the splitter: the search stops there, so a
tag that sits farther along the spool stays unread. A tag read this way sets the head as any read
tag. A spool whose refill is pending on a head that one lane feeds keeps its park, with no search.

The read runs once per spool. A spool taken out and put in again gets its own, and so does a tag
that is forgotten (**Read tag**) or changes. It does not start while a print is printing or paused,
while a read of the tag runs, or while a move runs or is pending on the lane or its head: a swap,
an eject or a push. The lane's own follow does not count as a move. The adapter looks again every
3 s; after 60 s it gives up with the line below. When the bay has emptied or the tag got read
meanwhile, nothing runs. The console says when it starts and how it ended:

```text
ace2k_u1: lane n: the tag was not read at the parking point — reading it with motion
ace2k_u1: lane n tag read with motion: found — <brand> <material> <color>
```

The other outcomes are `no tag`, `tag not read`, `the bay emptied` and `no outcome within <n> s`,
where the wait is ace2k's own for `MOVE=1` (the search out and back at the load speed). If the unit
refuses the read because another lane moves or the lane is busy, the adapter tries once more 5 s
later. A
second refusal, or any other, is the line `ace2k_u1: lane n: tag read with motion given up —
<reason>`. Then send `ACEK_RFID_READ LANE=n MOVE=1` (with `UNIT=` when there are several units)
once the other lanes are idle.

### A material the U1's table does not know

For a material such as `PCTG` or `PAHT`, only the color is written. The console line is
`ace2k_u1: lane n -> e<head>: colour only — material '<material>' unknown to the U1`. If the tag
carries no color either, nothing is written, and the line is `ace2k_u1: lane n tag not applied:
material '<material>' unknown to the U1`.

The U1's own reader may reset that color moments later. When it cannot read a spool, it applies
the default setting to a head whose vendor is unset. Set such a head by hand.

### A spool without a tag

Nothing changes while the spool is in the bay. Set its head by hand on the screen. The adapter
never clears that setting, but the U1 does when the spool leaves (next section).

### When a spool leaves the bay

The U1 itself sets the head back to unknown as soon as the bay is empty, with a tag or not.
Unknown means vendor, type and subtype `NONE`, color white. This is the U1's own behavior, and
no setting turns it off.

The adapter also clears the setting itself, as a backup to the U1's reset:

- It clears only what this lane's tags wrote since the last clear. After a color-only write, it
  clears only the color.
- It clears only if the head still shows that value.
- While the printer is printing or paused, the clear waits until it is neither.
- The clear is dropped if a spool was put back into the bay in the meantime.

### When a write is refused

Before each write or clear, the adapter checks what the U1's command would refuse: a vendor or
subtype it cannot carry, or a color that is not 8 hex digits. Such a write is not sent, and the
console says `ace2k_u1: lane n -> e<head>: not sent — <reason>`. A refusal the adapter did not
expect shows up like any other G-code error of the U1. Neither kind of refusal ever pauses a print.
`ACEK_ADAPTER_STATUS`'s `applied=` keeps the last result for each lane.

When the adapter attaches, it checks two things:

1. The U1's `print_task_config` accepts the command with every parameter the adapter writes, and
   reports each head's setting.
2. `filament_parameters` lists the filament types.

If either check fails, the tags are off, with the console line `ace2k_u1: tags not applied:
<reason>`. This usually happens after a U1 firmware update. Loading and feeding work as before.
`apply_tags: False` in `[ace2k_u1]` turns the tags off on purpose.

## When a spool runs out during a print

The sensor that detects filament sits at the entrance of each bay. The motor that moves the
filament sits further inside the unit. So when a spool runs out, or the filament is cut at the
bay, the end of the filament is still inside the unit, in the motor.

ace2k keeps feeding that last piece, the **tail**, to the print head. It feeds when the extruder
pulls, and never takes the filament back. It stops when the end has passed the motor, or after
2000 mm of motor travel at most (setting `follow_tail_mm`; see ace2k's
[When a spool runs out during a print](https://github.com/tobecwb/ace2k/blob/main/docs/features.md#when-a-spool-runs-out-during-a-print)). The print continues
with the filament left in the tube.

### What you see in the console

1. When the spool runs out: `ace2k_u1: lane n ran out at the bay — its tail feeds on to e<head>`.
2. When the end has left the unit (the unit reports this as `tail_out`), one of these lines:
   - `ace2k_u1: lane n: the end is out of the unit — a new filament can go in`. The bay is empty
     and the unit's gear is free. This line comes only once at least 100 mm of filament has been
     pulled since the bay emptied, so it often comes a little after the `tail_out`. If the print
     pauses before that, the line comes once the print has pulled the rest. If the head is
     unloaded, the lane rolls back or the print ends first, the line does not come. Insert the new
     filament now ([Inserting the new filament](#inserting-the-new-filament)). If the old piece
     is still in the head, the unit only grips the new filament
     ([When the head still holds a piece](#when-the-head-still-holds-a-piece)). If the head is
     already empty, the unit loads the filament to its parking point, as usual.
   - `ace2k_u1: lane n: the end is out of the unit — pull lane n's new filament back out of the
     bay and push it in again until the gear takes it`. You inserted a filament during the tail.
     This line waits for the same 100 mm.
     It sits behind the old end, where the gear cannot reach it, and the unit loads nothing it
     cannot grip. Pull it out and push it in again. The unit's load then takes it.
   - `ace2k_u1: lane n: the end is out of the unit — lane n's new filament waits in the bay until
     e<head>'s piece runs out; push it in until it stops`. You inserted a filament during the
     tail, on a head that this lane feeds alone, and the unit holds the wait
     ([The wait](#the-wait)). Push the filament in until it stops. Nothing moves it until the
     U1 loads the head.

**Wait for the second message before you insert a new spool.** Until then, the old filament is
still in the motor, and the new one cannot get past it. If you insert it too early, the unit may
think the filament is tangled and pause the print.

### Inserting the new filament

1. Wait for the line `the end is out of the unit — a new filament can go in`.
2. Push the new filament into the bay until it stops.
3. Press the lever and push a little more, until the filament is past the drive gear. Resting
   against the gear is not enough for the unit to catch it, unless the tip is cut thin.

Never push a new filament against the old one before the old end has left the bay's sensor. The
bay then never reads empty, so there is no tail. The follow sees the pushed filament as a tangle
and pauses the print. Your print is safe, but it stops.

### What pauses the print

The setting `runout_source` in `[ace2k_u1]` decides what pauses the print. It has two values.

**`head`** (the default) works like the U1's stock side feeders:

1. The print continues.
2. When the end of the filament passes the head's filament sensor, the U1 pauses with its own
   runout. If you have the U1's auto replenish on, it runs too.
3. If the tail was still running at that moment, the adapter stops the lane once, as a safety
   stop: `ace2k_u1: lane n: the tail passed e<head>'s sensor — stopped`. The end is then out of
   the unit as well, and a new filament can go in. In this case no `tail_out` line comes.

What to do with `head`: insert the new filament at the `tail_out` line, while the print continues.
The unit only grips it (or, with [the wait](#the-wait), leaves it in the bay), and it waits there
until the old piece has run out. The U1 then pauses
with its own runout. It pushes the new filament to the head, with its replenish or with a load from
its screen. Then resume the print. With auto replenish on, the U1 resumes by itself.

**`unit`**: during a print, the print pauses as soon as the bay's sensor sees the end, and the
head is refilled as a shared head is
([When a spool runs out in a shared head](#when-a-spool-runs-out-in-a-shared-head)). The lane stops
at once, so there is no tail. The head is unloaded hot with `ACEK_SWAP_UNLOAD`, its heater ends
off, and the rest of the filament rolls back out of the bay. This needs the macros
`ACEK_SWAP_UNLOAD` and `ACEK_SWAP_LOAD` from `ace2k-u1.cfg`. Without them a print start is refused.

What to do with `unit`: wait for the line `… ran out — its rest is out of bay n: take it out, put a
new spool in and RESUME`. Take the piece out of the bay, put the new spool in and press
**Resume**. The adapter reads the new spool's tag. A spool without a tag must be set with Edit
spool before you resume. The new spool is then pushed to the head and loaded, and the print goes
on. Outside a print, or when the U1 would not pause (the head's sensor disabled, its pause on
runout off, or the print ending), the lane keeps its tail, as with `head`.

### What the U1 is told during the tail

While a lane is in its tail, the adapter does **not** tell the U1 that the bay is empty. Otherwise
the U1 would start its preload of a new filament while the follow is still running. The adapter
tells the U1 once, when the tail ends: at the unit's `tail_out`, or when the lane is stopped at
the head's sensor. A filament put into the bay during the tail changes nothing. The tail runs to
its end.

## When the head still holds a piece

The adapter does **not** tell the U1 about a filament put into a bay while that head's filament
sensor sees filament. This applies after a tail-out, after a tangle, or at any other time. The
reason: the U1's preload would push against the loaded head (its `residual_filament` error). The
U1 shows no error. Its channel shows it waiting for a filament until the head is clear.

What happens next:

- **During a print**, the U1's runout takes care of the filament, as described above. When the
  end of the old piece passes the head's sensor, the U1 pauses. With auto replenish on, it pushes
  the new filament to the head and the print resumes. Otherwise, load the filament from the screen
  and resume.
- **Outside a print**, the adapter tells the U1 once the head's sensor is clear, that is, once the
  old piece has run out by printing or extruding. The U1's preload then pushes the filament to the
  head.
- If you unload that head from the U1's screen, the unit also ejects the lane's filament from the
  bay, including the waiting one. Put it in again afterwards.
- If the U1 loads that head, the wait ends.
- If you take a loaded head's filament out of its bay and push it straight back in, it is the same
  case. The U1 already left its loaded state when the filament came out. Its channel shows waiting
  until the head is clear.

### The grip

While the head's sensor sees filament that is not this lane's own, the adapter sets the lane's
**grip** (ace2k's
[Inserting a new spool while the head still holds the old filament](https://github.com/tobecwb/ace2k/blob/main/docs/features.md#inserting-a-new-spool-while-the-head-still-holds-the-old-filament)). A filament you insert then is pulled only `grip_mm` (40 mm by default), until the
unit's gear holds it, with no tag search. It waits there. The old piece is never touched.

Without the grip, a filament inserted after a tail-out during a print would get a full load. It
would catch up with the old piece, which still moves at print speed, and push it. The U1's own
tangle detection would then pause the print. With the grip, the new filament waits, and the U1's
runout brings it to the head.

The adapter treats what the head holds as the lane's own filament, and sets no grip, in these
cases:

- while the lane's follow is on;
- during the lane's tail;
- while the U1 holds that head loaded or preloaded;
- during a load or a manual feed of that head by the U1, from the push to the extrusion, or to its
  failure.

After a tail-out, what the head holds is not the lane's own until the U1 loads that head again.
This is true even while the U1's channel still shows it as loaded, so the grip applies. The grip
is removed when the head's sensor is clear. If the adapter cannot read a head's sensor, it counts
the sensor as seeing filament, so that lane's loads grip. `klippy.log` says so once.

### The wait

On a head that one lane feeds, a grip pulls the new filament into the old piece's end at the gear,
and the U1's load after that piece runs out can then fail. So on such a head the adapter sets the
lane's **wait** instead, when the unit has ace2k v0.13.0 or later. A filament you insert then is
not pulled at all. It waits in the bay. Push it in until it stops. Its tag is read when the U1's
load moves it ([The tag of a gripped spool](#the-tag-of-a-gripped-spool)).

When the old piece has run out, the U1's load first runs the unit's own load from the bay. That
load lets the gear catch the tip. The push to the head then goes on from there, and the U1 purges
as always. `ACEK_ADAPTER_STATUS` shows `grip=the wait`.

On a unit with an older firmware the lane is gripped at `grip_mm`, and the console says once:
`ace2k_u1: the unit's firmware has no wait; flash v0.13.0 or later — a lone head's new filament is
gripped`. A head that several lanes feed uses neither the wait nor the park: see
[During the print](#during-the-print) for what its bays do.

### The tag of a gripped spool

The unit reads a tag only while its lane moves. It does not turn a gripped spool to find its
tag, because that would move the filament toward the old piece. So the lane stays `pending`, and
its tag is read when the U1's load moves it.

If the grip's own move read the tag, the head takes its filament setting like any read tag
([How a spool's tag sets the head's filament](#how-a-spools-tag-sets-the-heads-filament)). The head
that is printing at that moment waits for a pause or the end of the print.

If not, 5 s after the grip's load ends the console says `ace2k_u1: lane n: the gripped spool's tag
is read when it moves — e<head> keeps its filament setting until then`. The head keeps the old
spool's setting until that move, or until you set it by hand on the screen.

Under [the wait](#the-wait) no load runs, so nothing reads the tag yet. When the filament goes in,
the console says at once `ace2k_u1: lane n: the new spool's tag is read when it moves — e<head>
keeps its filament setting until then`.

## When the filament does not move

The unit stops a push toward the head when the filament cannot move. It is stuck inside the unit,
or held in the tube ahead. The unit stops without an error. ace2k calls this `blocked`
([`features.md`](https://github.com/tobecwb/ace2k/blob/main/docs/features.md)). The filament waits
where it stopped. What happens next depends on the U1's flow:

- **During the U1's preload**, the channel goes back to waiting for a filament. During a print, the
  console line is
  `ace2k_u1: lane n: the filament does not move — something in the tube ahead or at the spool;
  it waits and goes to the head when the head's piece has run out`.
  Outside a print, the line ends with `…; clear it, then pull the filament out and push it in
  again` instead.
- **During a load of the head** (the U1's load, or its replenish), you get the U1's own load
  error, as for any failed push. The head cannot be loaded.

A loose piece left in the tube, for example after a power cycle, is not a problem. The push carries
it to the head ahead of the new filament. The extruder takes it, and the new filament follows.

## When a lane fails during a print

While the follow is on, a lane can end in one of these states: `stuck`, `tangled`, `timeout` or
`motor_stalled`. Typical causes are a spool that is held, a tangle, or a blocked tube.

If this happens while the U1 is printing, the adapter pauses the print at once, through the U1's
own tangle pause for that head. You get the same error and message as the U1's own
(`detect filament tangled!`), the U1's tangle event, and `PAUSE`. The console says
`ace2k_u1: lane n <kind> in the follow — pausing the print (e<head>)`. Outside a print, you only
get a console line that ends with `… — ACEK_CLEAR LANE=n`.

To recover:

1. Fix the cause.
2. Resume the print from the U1.

The adapter clears the lane's error (`ace2k_u1: lane n cleared on the resume; its follow is armed
again`), and the follow turns itself back on.

## How the follow turns itself back on

At Klipper's start, ace2k itself turns on ("arms") the follow on every lane with filament in its
bay (its `follow_default`, on by default). The head does not matter. The console says
`ace2k: lane n follow on (follow_default)`, and the adapter takes that follow as its own. The
adapter asks ace2k to arm its lanes at the adapter's `feed_speed`. It also tells ace2k to leave a
new insertion to the adapter: a filament you put in later is pushed to the head by the U1's
preload, and the adapter arms the follow once the filament is there.

The adapter also arms a lane's follow in these cases:

- when the U1 extrudes or flushes during a load or a manual feed;
- when the U1 finishes a load;
- when the U1's unload shapes the tip.

From then on, the follow is *wanted* on that lane. The adapter turns it on again whenever it finds
the lane idle, with no error and a filament in its bay, on a head the U1 holds loaded. For example,
this happens after an `ACEK_CLEAR` or after the U1's resume. It happens once each time the lane
becomes idle. If the unit refuses, you see the `follow not armed` line
([Troubleshooting](#troubleshooting)). The follow is not turned on while the U1 preloads a fresh
filament toward the head, only once the preload is done. A push toward the head (a load whose
filament is not yet there) stops the follow first; the load turns it on again.

The follow stops being wanted:

- when one of the U1's feed flows fails;
- at the end of the U1's unload;
- when the filament leaves its bay outside the tail;
- at the end of the tail (`tail_out`), because a filament put in during the tail is not at the
  gear;
- when you stop it by hand with `ACEK_ASSIST LANE=n OFF=1` or `ACEK_STOP`
  (`ace2k_u1: lane n follow stopped by hand; left off`). It then stays off until the head's next
  load. A stop the adapter or ace2k sends for its own reasons is never read as one by hand.

`ACEK_ADAPTER_STATUS` shows it for each lane: `follow=wanted` or `follow=off`.

## Taking a filament out of its bay

`ACEK_EJECT LANE=n` takes lane n's filament out of its bay. It works at any time, except while a
print uses that lane. The Eject button on the web page sends it with `WAIT=0`. The Print tab's
"Eject N out of place" sends it with `WAIT=1`, one bay at a time, and starts the next only once
the bay before reads empty; a refusal, an `eject failed` line or a lane left in error stops the run
at that bay.

An eject that ends with the bay empty also forgets the lane's Edit spool setting (and a forgotten
tag), as when you take the spool out by hand. A spool you put back is then read again, or set again
with Edit spool. An eject that leaves its tail at the bay forgets it once you pull the tail out.
An eject that fails or is canceled keeps the setting. The adapter's own roll backs (a swap, a refill, the probe, a load that is refused) keep it too.

### When the eject is refused

The eject is refused, and nothing moves:

- while a print (printing or paused) uses the lane, that is, its lane is moving or its head's
  sensor sees filament;
- while the lane runs anything other than its follow;
- while the lane is in its error state (run `ACEK_CLEAR LANE=n` first);
- when there is no filament in its bay;
- while an eject of the lane is still running, including the unit's unload;
- while the U1 runs one of its own flows on that head: a load, an unload, a manual feed or a
  preload (`the U1 is loading/unloading this head`).

### What the eject does

**The head's sensor sees no filament.** The follow is stopped, and the unit unloads the filament
out of the bay. This is the eject after a U1 unload. The console says
`ace2k_u1: lane n eject: head empty — unloading`.

**The head's sensor sees filament.** The adapter must first find out whether the extruder's gear
holds the filament. To do this, it:

1. rolls the filament back, up to `eject_probe_mm` (80 mm) at `eject_probe_speed` (10 mm/s). The
   lane's buffer plunger is watched as it goes. If it stays pulled tight, the gear holds the
   filament: the rollback stops there. Who watches depends on the unit's firmware:
   - with ace2k v0.13.0 or later, the unit stops the rollback itself. It stops when the plunger
     stays pulled tight while the filament moves less than `held_mm` (3 mm) for its held window.
     The adapter sets the window from `eject_probe_speed`: about 6 mm of motor, never so short
     that friction could end the rollback (600 ms at the default `eject_probe_speed` of
     10 mm/s; see `commands.md`, "The held window"). The motor then turns about 6 mm against the
     held tip. A thick tip that drags in the tube pulls the plunger
     tight too, but the filament still comes, so the rollback goes on. If the link to the unit is
     lost, the unit stops the rollback, and the eject fails with a line;
   - with an older firmware, the adapter stops it when two reports in a row read the plunger
     pulled tight. Only reports that arrive after the rollback starts count, and none from its
     first 0.2 s, because they may be older than the rollback. If the link to the unit is lost
     during the rollback, it stops at once, and the filament counts as held;
2. at the rollback's end, waits `eject_settle_ms` (500 ms) for the plunger to settle and be
   reported. The unit reports its switches at 10 Hz, so this wait covers two reports or more;
3. reads the plunger.

Why 80 mm: while the follow is on, the tube and the buffer between the unit and the head can hold
slack. A rollback first takes up that slack, and the plunger stays at rest while it does, even
with the tip held in the gear. The slack measured about 50 mm on the U1. A rollback of 10 mm, as
in earlier versions, could take a held filament for a free one.

The rollback goes 80 mm only on a proven link. If the link is not proven at the start, the
rollback is only 10 mm long, whatever the firmware (also with an ace2k host module too old to
count its reports). The reading at its end may be older than the probe, so it counts as "moved".
The unit's encoder cannot answer the question. With the tip held in the head's gear, the slack and
the buffer absorb the probe, and the encoder reads the full length either way.

- **The plunger is at rest.** The filament came back whole, so the extruder's gear does not hold
  it. The console says `ace2k_u1: lane n eject: filament free (plunger at rest) — unloading`, and
  the unit unloads it.
- **The plunger was pulled tight, or moved** (pushed, or off its rest), or the rollback ended in a
  lane error (the tip held in the hot end). The gear holds the filament. The console says
  `ace2k_u1: lane n eject: held by the head (plunger taut) — unloading the head on the U1` (or
  `(plunger moved)`, or `(the probe met resistance)`). Then, in order:
  1. The lane's error is cleared.
  2. The filament the probe pulled out is fed back at the same speed. This is the encoder's
     figure, not the motor's: the motor slips on a held filament. If a stopped probe reports no
     filament figure, the motor's travel is used. The plunger is read again, after the
     same wait. If it is still off its rest, the console says
     `… plunger not at rest after the feed back — going on, the U1's unload settles it`. The
     follow turned on at the tip shaping takes up the buffer.
  3. The U1's own unload of that head starts. It is the one the U1's screen starts
     (`AUTO_FEEDING EXTRUDER=n-1 UNLOAD=1`, prepare then doing): heat, tip shaping, cut.
     While it runs, `klippy.log` gets one line a second for the lane, such as
     `ace2k_u1: lane n eject, U1 unload 3 s: following, motor fwd 10.0 mm back 0.0 mm (unit),
     filament -3.0 mm, buffer taut, …`, and a summary at its end: `… U1 unload over after <s> s:
     motor forward <mm> mm in <s> s, back <mm> mm in <s> s, …; forward while the filament did not
     go forward: <mm> mm in <s> s; direction from the unit in <s> s, from the follow's corrections
     in <s> s`. With ace2k v0.13.0 or later the unit reports its motor's travel each way, and the
     line says `(unit)`. Each such line is one report of the unit, logged when it arrives, since
     the report and a timer of the adapter's own would drift apart; a line that holds more than
     one says `(unit, 2 reports)` and counts that many seconds, and after 2 s with no report the
     follow's corrections give the motor of that time. With an older ace2k the direction is the one of the follow's corrections
     that the unit counted in that second, and the line says `counted`: a taut catch-up or a
     forward dose is `fwd`, a full take-up or a reverse dose is `back`, and both are `unsure`. A
     second with none carries the last direction. These lines only log, and change nothing.
  4. When it finishes, the unit takes the filament out of the bay, as after any U1 unload
     ([What the adapter does](#what-the-adapter-does), "The U1 unloads a head").

**The safety net.** Sometimes the filament is found free, but the unit's unload then stops
`stuck`, `motor_stalled` or `tangled` within the first 150 mm of filament, with the head's sensor
still seeing filament. The net does not apply if the head's sensor cannot be read, during a
print, or while the U1 runs one of its own flows on that head. Then
the gear holds the filament after all. The adapter does not stop there. The console says
`ace2k_u1: lane n eject: the unload met the head's gear <mm> mm in — the lane cleared, held by the
head after all — unloading the head on the U1`. Then, in order:

1. The adapter clears the lane's error itself, as `ACEK_CLEAR LANE=n` does.
2. It feeds back the filament that left the unit: the probe's and the unload's, by the encoder.
3. It runs the U1's unload of the head and then the unit's unload, as for a filament held by the
   head (steps 2 to 4 above).

This happens once per eject. If that last unload stops too, the eject fails as usual. Any other
end is a real failure, but for an `unload_incomplete` short of a whole move with the head's sensor
not on that filament (clear, or seeing another lane's): the filament's end is then past the unit's
drive gear, at the bay, and the eject is done ([`ACEK_EJECT`](commands.md#acek_eject),
"A short filament"). A `stuck` after more than 150 mm is a real failure too: that is far from
the head (the path to the head is about 1900 mm on the U1). It has its usual line
(`… eject failed: stuck — ACEK_CLEAR LANE=n`).

### The Unload of the U1's screen

The Unload button on the U1's screen always homes the printer, picks the head and heats it before
it pulls the filament out. When the lane's filament only waits at the head's sensor, short of the
extruder's gear (the unit brought it there, and the U1 has not loaded it), the adapter takes it
back cold instead:

1. The screen's preheat of the head is turned off.
2. The lane is judged as the eject judges it ([What the eject does](#what-the-eject-does)): with
   the head's sensor set, a short rollback and the lane's buffer plunger tell whether the gear
   holds the filament.
3. Free, or with the head's sensor empty, the unit takes the filament back to its bay, cold. The
   console says `ace2k_u1: lane n: the screen's unload is cold — the unit takes it back to its
   bay`. The screen goes through the unload's steps to its end at once: no home, no head picked,
   no heating. The unit's unload goes on after the screen shows the unload done. If the unit's
   unload cannot start, the screen shows the unload failed, and the console says why.
4. Held by the gear after all, the filament that the rollback pulled is fed back, and the U1's
   own unload runs as always, with its heating.

A lane that the U1 loaded (its gear holds the filament) always gets the U1's own unload. So does
everything the eject refuses ([When the eject is refused](#when-the-eject-is-refused)), and a head
that a lane swap is on.

### When the eject stops early

The adapter checks again at four points: before the probe, after the probe's wait, before the
feed back, and before the U1's unload. If a print has started in the meantime, the eject ends
(`… eject failed: a print started`). The same happens if a U1 flow has started on the head. If
the head's sensor has cleared in the meantime, the unit does a plain unload instead
(`ace2k_u1: lane n eject: head now empty — unloading`).

Anything that stops the eject early gives one line, `ace2k_u1: lane n eject failed: <reason>`.
The reasons are:

- the probe or the feed back was refused, did not end, or was ended by a stop;
- the probe reported no filament travel;
- a print started;
- the U1's unload was refused.

What happens to the follow:

- If the cause was a print or a U1 flow found at one of those checks, the follow that the eject
  turned off is turned on again. The line then ends with `; its follow armed again`. This happens
  only if all of these are true: the follow was on before the eject, the U1 holds the head loaded,
  its sensor still sees filament, and the lane is at rest with no error.
- Otherwise, with the head still loaded, the line ends with
  `; the follow stays off until e<n-1>'s next load`. After a failed probe or feed back, the
  lane's state is not known well enough to pull on the filament. The head's next load turns the
  follow on again, as any load does.

The U1 counts as running a flow on the head in every channel state except its resting ones. The
resting ones are none, inited, wait_insert, and the finish and fail states of the preload, the
load, the unload and the manual feed. A state the adapter cannot read counts as a flow.

### When the command returns (`WAIT`)

`WAIT=1` is the default, for the console. The command returns once the eject has decided what to
do. For a filament held by the head, that is after the feed back, when the U1's unload starts. It
never waits for the U1's unload itself. After 60 s without a decision, it returns with one console
line, and the eject goes on. `WAIT=0` returns at once. Either way, the steps run in the
background, from a Klipper reactor timer.

## When a piece of filament is stuck in the tube

Sometimes a piece of filament stays in the tube between the unit and the head, and its end has
already passed the bay's insert sensor. `ACEK_EJECT` refuses it, because there is no filament in the
bay. The piece is held at both ends: the head's extruder gears grip it, and the unit's motor cannot
be turned from outside.

A rollback can start without a filament at the insert sensor. So take the piece out with the
rollback and the extruder moving together. Releasing the extruder
(`SET_STEPPER_ENABLE STEPPER=extruder ENABLE=0`) and running a plain rollback does not work. The
rollback stalls after about 20 mm, because the gears still hold the piece.

The steps for lane n. `<head>` is its head's index, n-1 (lane 1: `T0`):

1. Home X and Y: `G28 X Y`
2. Make the head active: `T<head> A0`
3. Move it over the purge spot: `MOVE_TO_DISCARD_FILAMENT_POSITION`
4. Heat it: `M109 S220 T<head>`
5. Set relative extrusion: `M83`. Do not rely on the mode a canceled print leaves.
6. Start a slow rollback of the lane, without waiting for it. Start the extruder's retract at
   once. Then wait for the retract to end:

       ACEK_ROLLBACK LANE=n LENGTH=1700 SPEED=10 WAIT=0
       G1 E-80 F600
       M400

7. Bring the rest back into the bay: `ACEK_ROLLBACK LANE=n LENGTH=1700`. The adapter stops a lane
   whenever one of the U1's feed flows fails, even for a motion you started by hand. If such a
   failure stops the lane on the way, repeat this step.
8. Cool the head: `M104 S0 T<head>`

## Printing more colors than heads

A splitter joins several tubes into one. Put a splitter in front of a head, and several lanes feed
that head. A print can then use more colors than the U1 has heads, for example eight colors on
four heads. Each color is one tool of the file, and each tool prints from one lane.

Only one lane is in a head at a time. When the print asks for a color whose lane is not in its
head, the adapter changes the lane in that head. This is a **lane swap**. The old filament comes
out of the head and rolls back behind the splitter, and the new one goes in. A swap takes about
two minutes.

A head that several lanes feed is a **shared head**. To set one up, join its lanes with `+` in
`lanes` ([Several spools in one head](configuration.md#several-spools-in-one-head)). Then measure the
splitter: load two lanes of each unit on the head, unload the head, run
`ACEK_SPLITTER_MEASURE HEAD=<n>`, then `SAVE_CONFIG`
([`ACEK_SPLITTER_MEASURE`](commands.md#acek_splitter_measure)).

### The splitter and its tubes

When the U1 unloads a head, it forms a thick tip at the end of the filament. At every swap this tip
travels back through the splitter, and the new lane's tip travels forward through it.

- After a splitter, use a tube with a wide bore (2 mm inner diameter, as in [Mounting](#mounting)).
- Lay that tube in a straight run, with no tight bend.

A thick tip catches in a narrow tube or in a tight bend. The new lane then cannot reach the head,
and the swap fails.

### The slicer profile

1. In OrcaSlicer, make a copy of the U1's printer profile.
2. Set its number of extruders to the number of lanes you print with: one extruder per lane. The
   file's tools `T0`, `T1`, … are these extruders.
3. For each extruder, choose a filament of the same material as its spool, and set its color to
   the spool's color. The web page matches each tool to a spool by its material and its color.
4. Slice the model and upload the file to the U1 as usual.

The page reads the tools from the slicer's settings in the file, so the file must come from
OrcaSlicer. The U1 knows the tools `T0` to `T31`.

OrcaSlicer plans no color flush for these tools, because it treats each one as its own nozzle.
None is needed: a swap takes the whole old filament out of the head, and `ACEK_SWAP_LOAD` purges
30 mm of the new one.

### Starting the print

Start the print from the web page, `http://<printer>/ace2k/`. The **Print…** button is in its top
bar when a head is fed by several lanes.

Before you start, give each spool without a tag its material and color: **Edit spool** on its
lane's card (`ACEK_SPOOL`). A spool with a tag needs nothing.

On a shared head, Edit spool (or **Define**) writes the head's filament setting on the U1 only for
the lane that is in the head; for another lane of the head it saves the lane's spool alone. A
print started from **Print…** then writes each head it uses for the lane the head starts with,
before the U1 loads it. A load from the U1's screen, or a print started without a plan, on a shared
head whose setting is not that lane's is refused once: the adapter writes the lane's spool on the
head and rolls the lane back behind the splitter. Load again and it loads.

1. Press **Print…** and choose the file. The panel opens at once. When the slicer wrote a
   thumbnail in the file, the panel shows it beside the file's name, as the U1's screen does. A
   file with none shows only its name. While the U1 reads a big file, a bar says "The U1 is
   reading the file" with about how long is left, estimated from the file's size. If the file takes
   longer, the bar says "Still reading the file…". If another device plans another file after your
   plan came, the page says so: press **Plan … again** to plan yours again.
2. Each tool has a row: the file's color and material, and the spool the page suggests (its
   unit, lane and head). When the page cannot suggest a spool, the row says why. Choose a spool in
   the row's menu for every tool that has none. The menu names each lane with its head, its
   spool's material and the rough name of its color, for example "a:1 · head 1 — PLA Lite,
   beige", "b:1 · head 4 — no color set" or "a:3 · head 2 — empty bay". A spool of another
   material is refused. A spool the page chose by itself shows **suggested** in orange, beside the
   menu and on the head's card, until you choose one.
   The page plans the file again when you open **Print…** again or when the file changed. Your
   choices stay. A tool goes back to the suggestion when its spool is no longer offered, or when
   the file now gives that tool another color or material. A message names each one.
3. Under the rows, the block **Where to put each spool** says how to place the spools so that
   the print stops the fewest times for a lane swap. Tools of the same color and material share
   one line. Each line says "already in place", "move the spool from a:2 to here", or "put a PLA
   spool of this color here". On a lane whose spool no tool picks, the line says "check the PLA
   spool here: if it is this color, pick it in the table above; if not, put one here". A suggested lane that holds a spool of another material says "Take out the spool in
   a:4: the file does not use it." The block also shows the lane swaps and the time stopped, with
   the spools as they are and for the best placement. The block is not shown while a print is on.
4. To use the block, move the spools as listed. Give each spool without a tag its material and
   color with **Edit spool**. Then open **Print…** again. The page plans the file again and
   reads the bays. Every line should then say "already in place".
5. Under the block, each head shows its tools and how many lane swaps the file will cause.
6. Choose the start options, as on the U1's screen: bed leveling, flow calibration, time-lapse,
   and **Unload the filament at the end**.
7. Press **Print**.

The start can take up to a minute. Before anything heats, the adapter checks each shared head the
print uses. When the head's sensor sees filament, each lane of the head rolls back a little, one at
a time, to find which lane is in it. The head must be cold for this: the adapter turns its heater
off and waits until it is below 60 °C (`probe_max_temp`), with the head's fan on. When the lane in
the head is known since its last load or swap, and nothing has moved since, there is no probe.

With **Unload the filament at the end**, the U1 unloads the lane that is in each head at the end
of the print. When the print completes, the adapter also takes out of its bay each other lane of a
shared head that the print used. Those lanes wait behind the splitter, where the U1's unload does
not reach. After a Klipper restart the adapter may not know which lane is in a shared head. It
then leaves that head's lanes as they are and says so: take them out with `ACEK_EJECT`.

During the print the button reads **Printing**. A click asks for a confirmation, then cancels the
print. While the print is paused, a **Resume** button is in the top bar.

### Why the U1's screen and Fluidd are refused

The U1's screen and Fluidd cannot tell the adapter which lane each tool prints from. The screen
shows one color per head. Fluidd starts with the U1's tool table as it stands, one tool per head.
Started from either, a file made for shared heads would print the wrong colors, or print on an
empty head.

So the adapter refuses such a start. Nothing heats and nothing moves. The U1's screen shows only
its general error ("system anomaly"). The console says why:

- `ace2k_u1: this file shares heads between tools: start it from the ace2k page`: the file puts two
  of its tools on a shared head;
- `ace2k_u1: T1 prints on head 2, which has no filament and no lane: start it from the ace2k
  page`: a tool of the file would print on an empty head.

What to do: start the file from the web page. Nothing else needs to be undone.

A file with one tool per head, or with several tools on a head that one lane feeds, starts from
the U1's screen and from Fluidd as before. The other refusals at a start are listed in
[`docs/commands.md`](commands.md#sdcard_print_file-and-sdcard_print_file_with_parameters).

### During the print

You see two lines in the console for each swap:

1. When it starts: `ace2k_u1: head 1: a:1 → a:2 (T1)`.
2. When the new lane is in: `ace2k_u1: head 1: a:2 in (113 s)`.

The slicer heats the next tools early. On a shared head, a temperature for a tool whose lane is not
in the head is kept for its swap, not applied. The console says so once per tool:
`ace2k_u1: T2's temperature kept for its swap: head 1 prints a:1`.

**When a swap fails**, the print pauses. The head goes to its idle position, never over the part.
The last console line and the page's top bar say which step failed and why, for example
`ace2k_u1: head 1: a:1 → a:2 (T1) failed at free: a:1 is still held by head 1 after a second
unload`.

What to do: fix the cause, then press **Resume** (or send `RESUME`). The adapter reads the head
again and finishes the swap. Then the print goes on. When something is still missing, a line that
ends in `still paused` says what to do. For example, set the new lane's spool with Edit spool when
it has no tag. Then resume again.

A spool you put into the bay of a shared head is pulled in by the unit's own load, as on a head
that one lane feeds. The load goes to the parking point (about 300 mm, ace2k's `load_park_mm`) and
reads the tag on the way. When the tag is not read yet, it goes on searching for it, up to
ace2k's `rfid_search_mm` (750 mm from the bay by default). When the filament meets an obstacle
first, such as another lane's filament at the splitter, the search stops there. Either way the
filament comes back to the parking point. The lane waits there until a swap or a start pushes it
to the head. When a lane of the unit follows, the load stops at the parking point without the
search, and the adapter then reads the tag with motion once
([A tag not read at the parking point](#a-tag-not-read-at-the-parking-point)). The probe's roll
back of 100 mm leaves it in the unit. Keep `load_park_mm` inside the unit (its default, 300 mm).

### When a spool runs out in a shared head

In a shared head, the print pauses as soon as the bay's sensor sees the end of a spool, whatever
`runout_source` says. A head that one lane feeds does the same with `runout_source: unit`. If the end went on to the head, the rest of the filament would stay in the
shared tube. The next lane could not get past it, and only opening a tube would take it out.

1. The lane stops at once, and the print pauses. The console says
   `ace2k_u1: head 1: a:2 ran out at its bay`.
2. The head is unloaded with `ACEK_SWAP_UNLOAD`, and its heater ends off. The rest of the
   filament rolls back out of the bay. The console says `ace2k_u1: head 1: a:2 ran out — its rest
   is out of bay a:2: take it out, put a new spool in and RESUME`.
3. Take the piece out of the bay. It is lost. Put a new spool of the same material in that bay.
4. Press **Resume**. The adapter reads the new spool's tag first. A spool without a tag must be set
   with Edit spool before you resume, or `RESUME` asks for it and the print stays paused.
5. The new spool is pushed to the head and loaded, as in a swap. Then the print goes on.

## The web page

`http://<printer>/ace2k/` shows the unit and controls it with the same G-codes as the console. It
has four tabs, and a top bar on every one:

- **Units**: the printer's heads, each unit's bays, the rail from each bay to its head, and the
  cards of the bays, heads and units with their actions. A lane in error shows on its bay, and its
  card opens by itself.
- **Print**: plans a print from a file in the printer, lists the spool steps and starts the print.
  During a print it shows the progress, Pause, Resume and Stop, the color printing now, the next
  spool swap and the timeline. On a wide screen the plan has two columns: the colors and the
  guided step on the left, the units on the right. The timeline has a zoom strip, and on a phone the
  start options are in an **Options ▾** sheet.
- **Dryer**: the dryer's presets, modes and cycle.
- **History**: the chamber's temperature and humidity over the last days, and the unit's events.

The top bar has **Stop all** and a health dot. The dot opens the status panel with the health
lights, the adapter's version and the link's details. During a print the bar shows "Printing N %",
or "Paused" with the reason.

Color planning reads OrcaSlicer files and its derivatives (Snapmaker Orca, Bambu Studio). Files
from other slicers print from the U1's screen, without colors or a plan: the page cannot plan
them, so its Print button stays off.

Open it in a browser from 2022 or later: Safari 15.4 or later (iOS 15.4 on an iPhone or an iPad),
or a current Chrome, Edge or Firefox. An older browser blocks the page's link to the printer, and
the page stays without data.

It is a static page served by the U1's nginx, and it talks only to Moonraker. It needs no internet,
because the libraries it uses are in its own `vendor/` folder. Its content policy allows only the
page's own files and its link to the printer, and it pins the checksum of the page's import map.
The history it shows is written by `[ace2k_u1_history]` (in `ace2k-u1.cfg`). It records a sample
every 30 s and every ace2k console line, in one file per day under `printer_data/logs/ace2k/`.
Seven days are kept.

It is installed with the adapter ([Copy](#copy)). The recorder starts with Klipper.

**Check:** open `http://<printer>/ace2k/`. The top bar shows the ace2k version and "Link ok". After
a minute, `ls /home/lava/printer_data/logs/ace2k/` on the printer shows today's file. If the top bar
shows "The history is not being recorded: …" instead, the recorder failed. The reason after the
colon tells you what to look for. The history stays empty until it is fixed. `/ace2k/?demo=1` is not
installed; the demo data stays in the source tree. On the printer it shows "Demo data is not
installed on the printer."

**Update:** [Updating](#updating).

**During a print**, you cannot load or eject a lane whose head is printing, or change or stop its
follow, from the page. To stop it, pause the print on the U1. While the print is printing (not paused),
Edit spool, Read tag and Forget tag are disabled on every lane: pause the print to change a spool.
Clear stays available. Stop all then stops only the lanes that the print is not using, and its label says so:
`Stop idle lanes`, or `Stop idle lanes of unit a` when the page shows one unit's lanes. It is
disabled when there are no such lanes. Stop all is a G-code like any other: it waits behind a command that is still running. The page's own moves return at once, so Stop all reaches them; a move typed in the console with `WAIT=1` (the default) holds the queue until it ends, so start such a move with `WAIT=0` if you may need to stop it. Outside a print, Eject works whatever the head holds: a loaded head is unloaded on the U1
first ([Taking a filament out of its bay](#taking-a-filament-out-of-its-bay)).

## Troubleshooting

- **`REMOTE HOST IDENTIFICATION HAS CHANGED`** after a firmware upgrade: the upgrade creates new
  host keys for the printer. Check that the new fingerprint is the one the warning shows. Then run
  `ssh-keygen -R <printer>` on your computer and connect again.
- **SSH asks for the password again** after a firmware upgrade: the upgrade resets root's
  authorized keys. Run `ssh-copy-id root@<printer>` from your own terminal. The password is in
  paxx's
  [`ssh_access.md`](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/main/docs/ssh_access.md).
- **The printer left the Wi-Fi** after a reboot following the upgrade: set up the network again on
  the printer's screen.
- **`MCU Protocol error … mcu 'ace2k': Unable to encode: query_analog_in`**: the
  `[temperature_sensor ace_mcu]` section is still active in `ace2k.cfg`. Comment it out
  ([Install and flash ace2k](#install-and-flash-ace2k)) and restart.
- **`MCU shutdown`, or `Can not update MCU config as it is shutdown`, when Klipper starts**: a
  board is still stopped from an earlier emergency stop (`M112`, the screen's stop) or from a unit
  that was lost. A board keeps that stop while it has power. The init script's restart clears
  none: the printer's own boards stay stopped too. Turning the printer off and on clears the
  printer's boards but not the ace2k units, which have their own power supply. Send
  `FIRMWARE_RESTART` (Fluidd's Firmware restart). It resets every board and starts Klipper again
  (measured on the U1, 2026-10-17: `M112`, the init script's restart still `shutdown`,
  `FIRMWARE_RESTART` ready). ace2k sends that `FIRMWARE_RESTART` by itself once, three seconds
  into a start of Klipper's service when every stopped board is stopped from an emergency stop or
  a lost unit (`klippy.log`: `ace2k: MCU … still stopped from an earlier shutdown`). This line is
  for when it did not: a board stopped by a fault (`klippy.log` names it), or a stop left after
  that one restart.
- **`OSError: [Errno 16] Device or resource busy`** in the U1's own RFID reader module
  (`fm175xx_reader.py`): a GPIO is still held by the previous Klipper. After a `SAVE_CONFIG`, the
  restart runs inside Klipper's own process. The init script's restart frees the GPIO
  ([Restart](#restart)). If the error is still there after the init script's restart, **reboot
  the printer**. This can happen when the previous Klipper had ended in error. Since ace2k
  v0.12.0, editing or removing an ace2k key (`laneN_insert_mv`, `laneN_encoder_scale`, the
  supervisors' thresholds, `rfid_search_mm`) and restarting the service no longer brings this
  restart: those values reach the unit at every connect without touching its configuration's
  checksum. `SAVE_CONFIG`'s own restart still runs inside Klipper's process.
- **`ace2k_u1: not hooked: …`** in the console instead of `hooked lanes`: the adapter checks
  everything before it touches the U1. It attaches to all of it or to nothing. With nothing
  hooked, the U1 behaves exactly as without the adapter, and ace2k's own G-codes still work. The
  reasons:
  - `[ace2k] or the U1 feed modules are absent`: ace2k is not configured (or did not load), or
    Klipper has no `filament_feed` objects.
  - `the ace2k firmware has no feed support`: the unit runs an ace2k image built without
    `CONFIG_ACE2K_FEED`. `klippy.log` says so at connect. Flash an image built with it
    ([`flashing.md`](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md)).
  - `ace2k host interface older than 12`: the ace2k extras on the printer are older than the
    adapter. The follow's tail and the `blocked` result came with version 5 of the interface, the
    grip with version 6, the `ACEK_*` command names and the tag read the adapter calls with
    version 7, the follow on by default with version 8, several units with version 9, the
    feed-forward's reverse with version 10 and the lane swap's choice of a head's lane with
    version 11, the free tip of a push toward a head with version 12. Copy both from the same release
    ([Copy](#copy)).
  - `the unit's firmware has no follow tail; flash v0.11.0`: the extras are current, but the unit
    runs an older ace2k image. Flash `v0.11.0` or later
    ([`flashing.md`](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md)). Until then,
    ace2k's own G-codes say `ace2k: the unit's firmware has no follow tail (or no follow); flash
    v0.11.0` for the follow. Its plain feeds work.
  - `the unit's firmware has no grip; flash v0.11.0`: the same, for the grip. The unit runs an
    image without it. Flash `v0.11.0` or later.
  - `grip_mm <mm> is outside the unit's 10..45 mm`: `grip_mm` in `[ace2k_u1]` is outside the
    limits the unit's firmware has. Set it within them and restart.
  - `eject_probe_speed <mm/s> is outside the unit's 9..80 mm/s`, the same for `swap_speed` and
    `probe_speed`, or `probe_mm <mm> is longer than the unit's longest move, <mm> mm` (only on a
    unit whose longest move is under 300 mm): that key in `[ace2k_u1]` is outside the unit's own
    limits for a move
    ([Speeds and lengths the unit refuses](configuration.md#speeds-and-lengths-the-unit-refuses)).
    Set it within them and restart.
  - `constant …`, `method …`, `… is not a two-channel list`, `attribute … missing`,
    `config['auto_mode'] missing`: the U1's `filament_feed` is not the one the adapter was built
    for. This usually happens after a U1 firmware update. You need an adapter release that matches
    it. Until then, remove `ace2k-u1.cfg`, or leave it in place: nothing is hooked.
  - `head sensor e<n>_filament missing`: that head's filament sensor is missing from the U1's
    configuration. The push toward the head stops at that sensor, so no lane is hooked without
    it.

  `ACEK_ADAPTER_STATUS` repeats the same reasons. Within the first second after Klipper is ready,
  before the adapter has checked, it says `ace2k_u1: not hooked yet`.
- **`ace2k_u1: tags not applied: …`** after `hooked lanes`: the U1's `print_task_config` or
  `filament_parameters` is not the one the adapter was built for. The reason is one of these:
  - `[print_task_config] absent`
  - `print_task_config has no SET_PRINT_FILAMENT_CONFIG`
  - `SET_PRINT_FILAMENT_CONFIG's source unreadable`
  - `SET_PRINT_FILAMENT_CONFIG does not read …`
  - `print_task_config status: <error>`
  - `print_task_config status has no list …`
  - `no filament types in [filament_parameters]`

  The lanes are hooked and feed as usual. Only the tags are off
  ([How a spool's tag sets the head's filament](#how-a-spools-tag-sets-the-heads-filament)).
- **`ace2k_u1: lane n: no extruder <head> on the printer; no feed-forward`**: the U1 lists no
  extruder for that head. The lane is hooked and follows, without the feed-forward.
- **`ace2k: lane n: N taut corrections since the last notice — check the encoder scale
  (ACEK_CALIBRATE_ENCODER)`**
  (or `full corrections`), during a print: the lane measures a wrong length. A `taut` correction
  is the follow feeding because the filament was pulled tight. A `full` correction is the follow
  taking filament back because the lane's buffer was full. Many in one direction mean the follow
  keeps correcting the same way. Nothing fails in the meantime. To fix it, calibrate that lane's
  encoder, run `SAVE_CONFIG`, then restart Klipper with its init script ([Restart](#restart)).

  Between prints, `ACEK_CALIBRATE_ENCODER LANE=n AUTO=1` reads the scale from the head's extruder.
  It needs the lane loaded to its head, that head selected and hot, and the head over a place
  where it may extrude. The command never moves or heats the head
  ([`features.md`](https://github.com/tobecwb/ace2k/blob/main/docs/features.md), "Calibrating the
  encoder", which also describes the manual mode).
- **`ace2k_u1: lane n follow not armed — ace2k: lane n assist_both refused: <reason>`**: the U1
  entered a state that turns the follow on (an extrusion, a finished load, the unload's tip
  shaping), and the unit refused it. The U1's flow goes on, with nothing feeding or taking up
  behind the extruder. The usual reasons:
  - `bounds`: `feed_speed` in `[ace2k_u1]` is outside the unit's speed range, 9–80 mm/s. The default
    is 70.
  - `in_error`: the lane is in its error state (`ACEK_CLEAR LANE=n`).
  - `no_filament`: the unit sees no filament at the bay's insert sensor.
  - `no_link`: the unit's link is down.
  - `busy`: the lane is still in another mode, such as the unit's own load.

  Once the cause is fixed, the next such state tries again.
- **`ace2k_u1: lane n eject failed: … — ACEK_CLEAR LANE=n`**: the unit's unload after the U1's
  unload ended in error. The lane is in its error state. Clear it with `ACEK_CLEAR LANE=n`. Then run
  `ACEK_EJECT LANE=n`, or take the filament out by hand.
- **`ace2k_u1: lane n: the unit's load ended in error — ACEK_CLEAR LANE=n, then re-insert`** or
  **`ace2k_u1: lane n eject not started — …`**: the lane needs your attention.
  - In the first case, the unit's own load of a filament put into the bay failed. The U1 is not
    told about the filament.
  - In the second case, the eject after the U1's unload was refused. The reason follows the dash:
    for example, a lane in its error state, the link down, or a lane still busy five seconds after
    the unload ended.

  `ACEK_STATUS` shows the lane. `ACEK_CLEAR LANE=n` takes it out of its error state. Then take the
  filament out and put it back (or run `ACEK_EJECT LANE=n`).
- **`ace2k_u1: lane n grip not set — <reason>`** (or `not cleared`): ace2k refused the lane's
  grip. This is said once. The adapter tries again when what the head holds changes. Until then, a
  filament put into that bay while its head holds a piece gets the unit's full load. Wait for the
  head to be clear before you put it in.
- **The printer does not accept a USB drive for an upgrade**: try another drive. A drive that a
  computer lists but never shows as a disk will not work for the printer either.

## After a U1 firmware upgrade

An upgrade removes ace2k's extras, `/oem/.debug`, `/etc/hooks` and root's authorized key. The
configuration files in `printer_data` stay. Redo these steps, in order:

1. SSH: the host key and the authorized key ([Troubleshooting](#troubleshooting)).
2. Run the installer with `--update` ([Installing with one command](#installing-with-one-command)).
   It creates `/oem/.debug` (no reboot is needed), writes the extras and the web page again, and
   restarts Klipper. If the unit's firmware is older than the release, it flashes it too.

## Removing ace2k and the adapter

Run the installer with `--remove`. It asks whether to put the unit back on the original Anycubic
firmware first (`--restore-stock FILE`). It keeps the web page's history in
`printer_data/logs/ace2k/`, the calibration copies and the backups, and says where. The flasher
stays in the state directory too.

`--restore-stock FILE` puts every unit back on the original firmware, one at a time, with Klipper
stopped once. If a unit's cable is missing, it refuses before any change. If the restore stops
half way, Klipper stays stopped, because a unit may be on the original firmware or in recovery:
run the same command again. It restores every unit, then removes. With an older flasher in the
state directory, one unit is restored the old way and two or more are refused (run `--update`
first).

By hand, on the printer:

    rm -f /home/lava/klipper/klippy/extras/ace2k*.py   # every ace2k_u1*.py of the adapter included
    rm -rf /home/lava/klipper/klippy/extras/ace2k_tags
    rm -f /home/lava/printer_data/config/extended/klipper/ace2k.cfg \
       /home/lava/printer_data/config/extended/klipper/ace2k_tags.cfg \
       /home/lava/printer_data/config/extended/klipper/ace2k-u1.cfg
    rm -rf /home/lava/ace2k-web
    rm -f /etc/nginx/fluidd.d/ace2k.conf
    nginx -s reload
    /etc/init.d/S60klipper restart

The web page's history files in `printer_data/logs/ace2k/` are yours to keep or delete. To put
the unit back on its factory firmware, see [`flashing.md`](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md).
