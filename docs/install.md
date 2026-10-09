# Installing on a Snapmaker U1

This guide shows how to install ace2k on a **Snapmaker U1 running the paxx extended firmware**,
together with the adapter `[ace2k_u1]` (`klippy/extras/ace2k_u1.py`, `config/ace2k-u1.cfg`). With
the adapter, the U1's own filament handling (load, unload, runout and the rest) drives the unit.
Each lane feeds one head. The guide also explains what the adapter does once it is installed.

You need ace2k **`v0.12.0-rc.1`**, on the unit and on the printer. Its host interface must be at
`API_VERSION` 6 or later. With an older version, the adapter does not attach to the U1
([Troubleshooting](#troubleshooting)).

The guide covers **one** ACE 2 Pro unit. The adapter supports exactly one unit. Lane n feeds
head n.

Every step on this page was run on a U1. Problems found on the way are listed under
[Troubleshooting](#troubleshooting).

## Contents

- [What the adapter does](#what-the-adapter-does)
- [Installation](#installation)
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
- [The web page](#the-web-page)
- [Troubleshooting](#troubleshooting)
- [The burn-in logger (optional)](#the-burn-in-logger-optional)
- [After a U1 firmware upgrade](#after-a-u1-firmware-upgrade)
- [Removing ace2k and the adapter](#removing-ace2k-and-the-adapter)

## What the adapter does

Lane n feeds head n (extruder n-1) through its own tube. This never changes.

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
- **`ACE_EJECT LANE=n`** takes a lane's filament out of its bay by hand, whatever its head holds
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
  - `unit`: the adapter, once the end of the filament is out of the unit.

  Insert a new filament only when the console says the end is out of the unit. Until then, the old
  filament's end still holds the unit's gear.
- **You put in a filament while the head still holds a piece that is not this lane's current
  filament.** For example: the old piece after a runout, a filament left in the head after a
  tangle, or the head's filament after its bay was emptied. The unit then only **grips** the new
  filament. It pulls it about 40 mm (`grip_mm`), until its gear holds it, and does not search for
  a tag. The filament waits there. The old piece is never touched. The adapter does not tell the
  U1 about the new filament while the head's sensor sees filament
  ([When the head still holds a piece](#when-the-head-still-holds-a-piece)).
- **A lane error during a print.** While the follow is on, the filament gets stuck or tangled, the
  lane times out, or its motor stalls. The adapter then pauses the print at once, through the U1's
  own tangle pause for that head.
- **The follow turns itself back on for a loaded head.** After a lane error and `ACE_CLEAR`, or
  after the U1 resumes, the lane follows again. If you switch the follow off by hand, it stays off
  until the head's next load.
- **You put a spool with a tag into a bay.** Once the unit has read the tag, the head's filament
  setting on the U1 takes the tag's values: vendor, type, subtype and colour. When the spool leaves
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

### Prerequisites

- **The paxx extended firmware `v1.6.0-paxx12-22`**, installed over the U1's stock firmware
  1.6.0. This guide was tested on it. To install it, follow paxx's own guide,
  [`install.md`](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/main/docs/install.md).
  In short, you copy its upgrade file to a USB drive formatted as FAT32, and you start the upgrade
  from the printer's screen. Its Klipper is older than the one ace2k is built against. The one
  difference that matters is handled in [Install and flash ace2k](#install-and-flash-ace2k).
- **Root access over SSH.** paxx turns it on. The default root password is in paxx's
  [`ssh_access.md`](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/blob/main/docs/ssh_access.md).
  Put your key in with `ssh-copy-id root@<printer>`. The rest of the steps then need no password.
- **`/oem/.debug` present.** Create it with `touch /oem/.debug`, then reboot. Here is why:
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
  chip temperature. The three temperatures above it (`ace_ptc_left`, `ace_ptc_right`,
  `ace_chamber`) come from ace2k's own report and still work. `ace2k_tags.cfg` (the spool brands)
  needs no change.
- Klipper is restarted with `/etc/init.d/S60klipper restart` ([Restart](#restart)).

When it is done, `ACE_STATUS` reports `ace2k 0.12.0-rc.1` and `ACE_HEALTH` reports `OK`.

### Copy

This step copies the adapter's files and its web page. First get the release on your computer:
`git clone --branch v0.3.0-rc.1 https://github.com/tobecwb/ace2k-u1`. Then, from the directory
that holds the `ace2k-u1` tree, run:

    scp ace2k-u1/klippy/extras/ace2k_u1*.py root@<printer>:/home/lava/klipper/klippy/extras/
    scp ace2k-u1/config/ace2k-u1.cfg root@<printer>:/home/lava/printer_data/config/extended/klipper/
    scp -r ace2k-u1/web root@<printer>:ace2k-web-src

The `ace2k_u1*.py` pattern also copies the recorder, `ace2k_u1_history.py`. The web page needs it.
Then, on the printer, put the page in place and let nginx serve it:

    rm -rf ace2k-web-src/tests ace2k-web-src/dev ace2k-web-src/package.json
    mkdir -p /home/lava/ace2k-web && cp -r ace2k-web-src/. /home/lava/ace2k-web/
    cp ace2k-web-src/nginx/ace2k.conf /etc/nginx/fluidd.d/ && nginx -t && nginx -s reload
    rm -rf /home/lava/ace2k-web/nginx ace2k-web-src

`nginx -t` reports that the configuration is successful.

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

**Use it after `SAVE_CONFIG` too.** Some commands store values with `SAVE_CONFIG`, for example
`ACE_CALIBRATION_SAVE` after an encoder calibration. `SAVE_CONFIG` writes the values and then
restarts Klipper by itself. On the U1 that restart fails: Klipper stops with `Internal error
during connect`. The cause is the U1's own RFID reader module. It cannot take back a GPIO line it
still holds (`Device or resource busy`). Nothing is lost, because the values were already written.
Run the command above, and Klipper starts with them.

### Check

In Klipper's console:

- `ACE_STATUS`: the firmware version (`0.12.0-rc.1`), the four lanes (`idle`, with or without a
  filament), the temperatures, the mains frequency, and the dryer `idle`.
- `ACE_HEALTH`: `OK`.
- About a second after Klipper is ready, the console line `ace2k_u1: hooked lanes [1, 2, 3, 4]`.
  "Hooked" means the adapter is attached to these lanes. They are the lanes of the `lanes` setting.
- `ACE_FEED_FORWARD`: `ace2k: feed-forward lane 1 on (extruder) lane 2 on (extruder1) …`. Each
  hooked lane is on and matched to its head's extruder.
- `ACE_ADAPTER_STATUS`: one line per hooked lane, with:
  - `lane n -> e<head>`;
  - whether the U1 sees a filament in its bay;
  - the U1's state of that channel;
  - the head's filament sensor;
  - the lane's wheel count;
  - `follow=`: `wanted` or `off`
    ([How the follow turns itself back on](#how-the-follow-turns-itself-back-on));
  - `grip=`: one of these:
    - `<grip_mm> mm` (`40 mm` by default) while the lane's grip is set on the unit;
    - `off`;
    - `off (wanted, to be set again)` once its load or a start has used the grip while the head
      still holds the piece;
  - `tag=`: the lane's tag state (`read`, `no_tag`, …), with the tag's brand, name and colour;
  - `applied=`: what the tag last did to the head. One of these:
    - the vendor, type, subtype and colour written;
    - `<RRGGBBAA> only — material '<material>' unknown to the U1` when only the colour was
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

To update from an earlier release:

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
- **The colour**: the tag's colour. It is opaque when the tag gives no alpha.

The tag wins over a setting you made by hand. The write happens at once, whether the printer is
idle, printing or paused. The one exception is the head that is printing at that moment. Its write
waits for a pause or the end of the print. It then writes the tag of the spool that is in the bay
by then, if any. If the adapter cannot tell which extruder is active during a print, the writes of
all hooked heads wait.

If the same tag is read again, and the spool has not left the bay, nothing is written again. So a
change you make by hand afterwards stays until you put the spool in again. **Read tag** on the web
page (or `ACE_RFID_FORGET LANE=n`) puts the lane's tag back to pending. The unit reads it again at
the lane's next move. That read is written to the head again, even when it is the same tag.

### A material the U1's table does not know

For a material such as `PCTG` or `PAHT`, only the colour is written. The console line is
`ace2k_u1: lane n -> e<head>: colour only — material '<material>' unknown to the U1`. If the tag
carries no colour either, nothing is written, and the line is `ace2k_u1: lane n tag not applied:
material '<material>' unknown to the U1`.

The U1's own reader may reset that colour moments later. When it cannot read a spool, it applies
the default setting to a head whose vendor is unset. Set such a head by hand.

### A spool without a tag

Nothing changes while the spool is in the bay. Set its head by hand on the screen. The adapter
never clears that setting, but the U1 does when the spool leaves (next section).

### When a spool leaves the bay

The U1 itself sets the head back to unknown as soon as the bay is empty, with a tag or not.
Unknown means vendor, type and subtype `NONE`, colour white. This is the U1's own behaviour, and
no setting turns it off.

The adapter also clears the setting itself, as a backup to the U1's reset:

- It clears only what this lane's tags wrote since the last clear. After a colour-only write, it
  clears only the colour.
- It clears only if the head still shows that value.
- While the printer is printing or paused, the clear waits until it is neither.
- The clear is dropped if a spool was put back into the bay in the meantime.

### When a write is refused

Before each write or clear, the adapter checks what the U1's command would refuse: a vendor or
subtype it cannot carry, or a colour that is not 8 hex digits. Such a write is not sent, and the
console says `ace2k_u1: lane n -> e<head>: not sent — <reason>`. A refusal the adapter did not
expect shows up like any other G-code error of the U1. Neither kind of refusal ever pauses a print.
`ACE_ADAPTER_STATUS`'s `applied=` keeps the last result for each lane.

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
2. When the end has left the unit (the unit reports this as `tail_out`), one of two lines:
   - `ace2k_u1: lane n: the end is out of the unit — a new filament can go in`. The bay is empty
     and the unit's gear is free. Insert the new filament now. If the old piece is still in the
     head, the unit only grips the new filament
     ([When the head still holds a piece](#when-the-head-still-holds-a-piece)). If the head is
     already empty, the unit loads the filament to its parking point, as usual.
   - `ace2k_u1: lane n: the end is out of the unit — pull lane n's new filament back out of the
     bay and push it in again until the gear takes it`. You inserted a filament during the tail.
     It sits behind the old end, where the gear cannot reach it, and the unit loads nothing it
     cannot grip. Pull it out and push it in again. The unit's load then takes it.

**Wait for the second message before you insert a new spool.** Until then, the old filament is
still in the motor, and the new one cannot get past it. If you insert it too early, the unit may
think the filament is tangled and pause the print.

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
The unit only grips it, and it waits there until the old piece has run out. The U1 then pauses
with its own runout. It pushes the new filament to the head, with its replenish or with a load from
its screen. Then resume the print. With auto replenish on, the U1 resumes by itself.

**`unit`**: during a print, the adapter pauses the print when the end is out of the unit. The
console line above then ends with `; pausing the print (e<head>)`. The pause goes through the U1's
own runout pause for that head, as if the head's sensor had triggered it:

1. The head's filament setting is backed up.
2. The U1's runout message appears.
3. `PAUSE` runs.
4. The U1's auto replenish runs, if you have it on.

The head sensor's own switches are respected. The print is not paused:

- when the sensor is disabled (`… e<head>'s sensor is disabled: not paused`);
- when the sensor's pause on runout is off (`… e<head>'s pause on runout is off`);
- while the U1 runs the end of its print (`ace2k_u1: lane n ran out during the print's end; not
  paused`).

What to do with `unit`: insert the new filament during the pause. The gear is free, and the unit
grips the new filament. Then resume. The head keeps printing with the old piece still in the tube.
When the end of the old piece passes the head's sensor, the U1's own runout brings the new
filament to the head, as with `head`. Then resume again. Outside a print there is no pause, only
the console line.

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

### The tag of a gripped spool

The unit does not turn a gripped spool to find its tag, because that would move the filament
toward the old piece. When the grip's load ends, the adapter asks for a read in front of the
antenna (`ACE_RFID_READ LANE=n MOVE=0`). It does not wait for that read. If the tag is read, it
sets the head's filament setting like any read tag
([How a spool's tag sets the head's filament](#how-a-spools-tag-sets-the-heads-filament)). The head
that is printing at that moment waits for a pause or the end of the print.

If the tag is not read within 5 s, the console says `ace2k_u1: lane n: the gripped spool's tag was
not read (<why>) — e<head> keeps its filament setting until a load at idle or ACE_RFID_READ LANE=n
MOVE=1`. The head keeps the old spool's setting until then, or until you set it by hand on the
screen.

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
get a console line that ends with `… — ACE_CLEAR LANE=n`.

To recover:

1. Fix the cause.
2. Resume the print from the U1.

The adapter clears the lane's error (`ace2k_u1: lane n cleared on the resume; its follow is armed
again`), and the follow turns itself back on.

## How the follow turns itself back on

The adapter turns on ("arms") a lane's follow in these cases:

- when the U1 extrudes or flushes during a load or a manual feed;
- when the U1 finishes a load;
- when the U1's unload shapes the tip.

From then on, the follow is *wanted* on that lane. The adapter turns it on again whenever it finds
the lane idle, with no error and a filament in its bay, on a head the U1 holds loaded. For example,
this happens after an `ACE_CLEAR` or after the U1's resume. It happens once each time the lane
becomes idle. If the unit refuses, you see the `follow not armed` line
([Troubleshooting](#troubleshooting)). The follow is not turned on while the U1 preloads a fresh
filament toward the head.

The follow stops being wanted:

- when one of the U1's feed flows fails;
- at the end of the U1's unload;
- when the filament leaves its bay outside the tail;
- at the end of the tail (`tail_out`), because a filament put in during the tail is not at the
  gear;
- when you stop it by hand with `ACE_ASSIST LANE=n OFF=1` or `ACE_STOP`
  (`ace2k_u1: lane n follow stopped by hand; left off`). It then stays off until the head's next
  load.

`ACE_ADAPTER_STATUS` shows it for each lane: `follow=wanted` or `follow=off`.

## Taking a filament out of its bay

`ACE_EJECT LANE=n` takes lane n's filament out of its bay. It works at any time, except while a
print uses that lane. The Eject button on the web page sends it with `WAIT=0`.

### When the eject is refused

The eject is refused, and nothing moves:

- while a print (printing or paused) uses the lane, that is, its lane is moving or its head's
  sensor sees filament;
- while the lane runs anything other than its follow;
- while the lane is in its error state (run `ACE_CLEAR LANE=n` first);
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

1. rolls the filament back `eject_probe_mm` (10 mm) at `eject_probe_speed` (10 mm/s);
2. waits `eject_settle_ms` (500 ms) for the lane's buffer plunger to settle and be reported. The
   unit reports its switches at 10 Hz, so this wait covers two reports or more;
3. reads the plunger.

The reading is used only on a proven link. If the link is not proven, the reading may be older
than the probe, so it counts as "moved". The unit's encoder cannot answer the question. With the
tip held in the head's gear, the buffer between the unit and the head absorbs the whole probe, and
the encoder reads the full length either way.

- **The plunger is at rest.** The filament came back whole, so the extruder's gear does not hold
  it. The console says `ace2k_u1: lane n eject: filament free (plunger at rest) — unloading`, and
  the unit unloads it.
- **The plunger moved** (pushed, or off its rest), or the rollback ended in a lane error (the tip
  held in the hot end). The gear holds the filament. The console says
  `ace2k_u1: lane n eject: held by the head (plunger moved) — unloading the head on the U1` (or
  `(the probe met resistance)`). Then, in order:
  1. The lane's error is cleared.
  2. The probe's motor travel is fed back at the same speed. The plunger is read again, after the
     same wait. If it is still off its rest, the console says
     `… plunger not at rest after the feed back — going on, the U1's unload settles it`. The
     follow turned on at the tip shaping takes up the buffer.
  3. The U1's own unload of that head starts. It is the one the U1's screen starts
     (`AUTO_FEEDING EXTRUDER=n-1 UNLOAD=1`, prepare then doing): heat, tip shaping, cut.
  4. When it finishes, the unit takes the filament out of the bay, as after any U1 unload
     ([What the adapter does](#what-the-adapter-does), "The U1 unloads a head").

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
already passed the bay's insert sensor. `ACE_EJECT` refuses it, because there is no filament in the
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
5. Set relative extrusion: `M83`. Do not rely on the mode a cancelled print leaves.
6. Start a slow rollback of the lane, without waiting for it. Start the extruder's retract at
   once. Then wait for the retract to end:

       ACE_ROLLBACK LANE=n LENGTH=1700 SPEED=10 WAIT=0
       G1 E-80 F600
       M400

7. Bring the rest back into the bay: `ACE_ROLLBACK LANE=n LENGTH=1700`. The adapter stops a lane
   whenever one of the U1's feed flows fails, even for a motion you started by hand. If such a
   failure stops the lane on the way, repeat this step.
8. Cool the head: `M104 S0 T<head>`

## The web page

`http://<printer>/ace2k/` shows the unit and controls it with the same G-codes as the console. It
shows:

- the four bays, with their spools' colours;
- each lane's card and actions;
- the dryer;
- the chamber's temperature and humidity over the last days;
- the unit's events.

It is a static page served by the U1's nginx, and it talks only to Moonraker. The history it shows
is written by `[ace2k_u1_history]` (in `ace2k-u1.cfg`). It records a sample every 30 s and every
ace2k console line, in one file per day under `printer_data/logs/ace2k/`. Seven days are kept.

It is installed with the adapter ([Copy](#copy)). The recorder starts with Klipper.

**Check:** open `http://<printer>/ace2k/`. The top bar shows the ace2k version and "link ok".
After a minute, `ls /home/lava/printer_data/logs/ace2k/` on the printer shows today's file. If the
top bar shows "history not recorded: …" instead, the recorder failed. The reason after the colon
tells you what to look for. The history stays empty until it is fixed. `/ace2k/?demo=1` is not
installed; the demo data stays in the source tree.

**Update:** [Updating](#updating).

**During a print**, you cannot load or eject a lane whose head is printing, or change or stop its
follow, from the page. To stop it, pause the print on the U1. Clear and Read tag stay available on
that lane. Stop all then stops only the lanes the print is not using. It is disabled when there
are none. Outside a print, Eject works whatever the head holds: a loaded head is unloaded on the U1
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
- **`OSError: [Errno 16] Device or resource busy`** in the U1's own RFID reader module
  (`fm175xx_reader.py`): a GPIO is still held by the previous Klipper. After a `SAVE_CONFIG`, the
  restart runs inside Klipper's own process. The init script's restart frees the GPIO
  ([Restart](#restart)). If the error is still there after the init script's restart, **reboot
  the printer**. This can happen when the previous Klipper had ended in error.
- **`ace2k_u1: not hooked: …`** in the console instead of `hooked lanes`: the adapter checks
  everything before it touches the U1. It attaches to all of it or to nothing. With nothing
  hooked, the U1 behaves exactly as without the adapter, and ace2k's own G-codes still work. The
  reasons:
  - `[ace2k] or the U1 feed modules are absent`: ace2k is not configured (or did not load), or
    Klipper has no `filament_feed` objects.
  - `the ace2k firmware has no feed support`: the unit runs an ace2k image built without
    `CONFIG_ACE2K_FEED`. `klippy.log` says so at connect. Flash an image built with it
    ([`flashing.md`](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md)).
  - `ace2k host interface older than 6`: the ace2k extras on the printer are older than the
    adapter. The follow's tail and the `blocked` result came with version 5 of the interface, the
    grip with version 6. Copy both from the same release ([Copy](#copy)).
  - `the unit's firmware has no follow tail; flash v0.11.0`: the extras are current, but the unit
    runs an older ace2k image. Flash `v0.11.0` or later
    ([`flashing.md`](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md)). Until then,
    ace2k's own G-codes say `ace2k: the unit's firmware has no follow tail (or no follow); flash
    v0.11.0` for the follow. Its plain feeds work.
  - `the unit's firmware has no grip; flash v0.11.0`: the same, for the grip. The unit runs an
    image without it. Flash `v0.11.0` or later.
  - `grip_mm <mm> is outside the unit's 10..45 mm`: `grip_mm` in `[ace2k_u1]` is outside the
    limits the unit's firmware has. Set it within them and restart.
  - `constant …`, `method …`, `… is not a two-channel list`, `attribute … missing`,
    `config['auto_mode'] missing`: the U1's `filament_feed` is not the one the adapter was built
    for. This usually happens after a U1 firmware update. You need an adapter release that matches
    it. Until then, remove `ace2k-u1.cfg`, or leave it in place: nothing is hooked.
  - `head sensor e<n>_filament missing`: that head's filament sensor is missing from the U1's
    configuration. The push toward the head stops at that sensor, so no lane is hooked without
    it.

  `ACE_ADAPTER_STATUS` repeats the same reasons. Within the first second after Klipper is ready,
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
  (ACE_CALIBRATE_ENCODER)`**
  (or `full corrections`), during a print: the lane measures a wrong length. A `taut` correction
  is the follow feeding because the filament was pulled tight. A `full` correction is the follow
  taking filament back because the lane's buffer was full. Many in one direction mean the follow
  keeps correcting the same way. Nothing fails in the meantime. To fix it, calibrate that lane's
  encoder, run `SAVE_CONFIG`, then restart Klipper with its init script ([Restart](#restart)).

  Between prints, `ACE_CALIBRATE_ENCODER LANE=n AUTO=1` reads the scale from the head's extruder.
  It needs the lane loaded to its head, that head selected and hot, and the head over a place
  where it may extrude. The command never moves or heats the head
  ([`features.md`](https://github.com/tobecwb/ace2k/blob/main/docs/features.md), "Calibrating the
  encoder", which also describes the manual mode).
- **`ace2k_u1: lane n follow not armed — ace2k: lane n assist_both refused: <reason>`**: the U1
  entered a state that turns the follow on (an extrusion, a finished load, the unload's tip
  shaping), and the unit refused it. The U1's flow goes on, with nothing feeding or taking up
  behind the extruder. The usual reasons:
  - `bounds`: `feed_speed` in `[ace2k_u1]` is outside the unit's speed range, 9–70 mm/s. 70, the
    default, is the top of the range.
  - `in_error`: the lane is in its error state (`ACE_CLEAR LANE=n`).
  - `no_filament`: the unit sees no filament at the bay's insert sensor.
  - `no_link`: the unit's link is down.
  - `busy`: the lane is still in another mode, such as the unit's own load.

  Once the cause is fixed, the next such state tries again.
- **`ace2k_u1: lane n eject failed: … — ACE_CLEAR LANE=n`**: the unit's unload after the U1's
  unload ended in error. The lane is in its error state. Clear it with `ACE_CLEAR LANE=n`. Then run
  `ACE_EJECT LANE=n`, or take the filament out by hand.
- **`ace2k_u1: lane n: the unit's load ended in error — ACE_CLEAR LANE=n, then re-insert`** or
  **`ace2k_u1: lane n eject not started — …`**: the lane needs your attention.
  - In the first case, the unit's own load of a filament put into the bay failed. The U1 is not
    told about the filament.
  - In the second case, the eject after the U1's unload was refused. The reason follows the dash:
    for example, a lane in its error state, the link down, or a lane still busy five seconds after
    the unload ended.

  `ACE_STATUS` shows the lane. `ACE_CLEAR LANE=n` takes it out of its error state. Then take the
  filament out and put it back (or run `ACE_EJECT LANE=n`).
- **`ace2k_u1: lane n grip not set — <reason>`** (or `not cleared`): ace2k refused the lane's
  grip. This is said once. The adapter tries again when what the head holds changes. Until then, a
  filament put into that bay while its head holds a piece gets the unit's full load. Wait for the
  head to be clear before you put it in.
- **The printer does not accept a USB drive for an upgrade**: try another drive. A drive that a
  computer lists but never shows as a disk will not work for the printer either.

## The burn-in logger (optional)

[`scripts/ace2k_logger.py`](https://github.com/tobecwb/ace2k/blob/main/scripts/ace2k_logger.py)
records the unit's state through Moonraker's API. It writes a CSV row every 10 s, and the unit's
console lines. A row holds the print state, the unit's version, health, temperatures, lanes, dryer
and Klipper's link statistics of the unit's MCU. The logger never opens the unit's port. It needs
nothing but the printer's own Python.

    ssh root@<printer> mkdir -p /home/lava/printer_data/ace2k-burnin
    scp ace2k/scripts/ace2k_logger.py root@<printer>:/home/lava/printer_data/ace2k-burnin/

To start it with every Klipper start, create `/etc/hooks/klipper.d/50-ace2k-logger.sh` on the
printer. With `/oem/.debug`, it is kept across reboots:

    case "$1" in
      start|restart)
        if ! pgrep -f ace2k_logger.py >/dev/null 2>&1; then
          nohup /usr/bin/python3 /home/lava/printer_data/ace2k-burnin/ace2k_logger.py \
            --out /home/lava/printer_data/ace2k-burnin/data >/dev/null 2>&1 &
        fi
        ;;
    esac

The files land in `/home/lava/printer_data/ace2k-burnin/data/`, one CSV and one events file per
day. ace2k's [`docs/logging.md`](https://github.com/tobecwb/ace2k/blob/main/docs/logging.md)
explains what they hold. It also shows how to turn them into a report with
`scripts/ace2k_burnin_report.py`, and what to attach when you report a problem.

## After a U1 firmware upgrade

An upgrade removes ace2k's extras, `/oem/.debug`, `/etc/hooks` and root's authorized key. The
configuration files in `printer_data` stay. Redo these steps, in order:

1. SSH: the host key and the authorized key ([Troubleshooting](#troubleshooting)).
2. `touch /oem/.debug`, reboot.
3. The extras: the first two `scp` commands of [Copy](#copy).
4. The logger's hook, if you use it.
5. Restart Klipper and check.

## Removing ace2k and the adapter

On the printer:

    rm -f /home/lava/klipper/klippy/extras/ace2k*.py   # ace2k_u1.py and ace2k_u1_tags.py included
    rm -rf /home/lava/klipper/klippy/extras/ace2k_tags
    rm -f /home/lava/printer_data/config/extended/klipper/ace2k.cfg \
       /home/lava/printer_data/config/extended/klipper/ace2k_tags.cfg \
       /home/lava/printer_data/config/extended/klipper/ace2k-u1.cfg
    rm -f /etc/hooks/klipper.d/50-ace2k-logger.sh
    rm -rf /home/lava/ace2k-web
    rm -f /etc/nginx/fluidd.d/ace2k.conf
    nginx -s reload
    /etc/init.d/S60klipper restart

The logger's data in `printer_data/ace2k-burnin/` is yours to keep or delete. So are the web
page's history files in `printer_data/logs/ace2k/`. To put the unit back on its factory firmware,
see [`flashing.md`](https://github.com/tobecwb/ace2k/blob/main/docs/flashing.md).
