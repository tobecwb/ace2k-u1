# Commands

This page lists what the adapter adds to ace2k's own G-code commands
([ace2k's `docs/commands.md`](https://github.com/tobecwb/ace2k/blob/main/docs/commands.md)):

- two commands of its own;
- one wrapper around ace2k's `ACE_CLEAR`;
- the macros of `config/ace2k-u1.cfg` (none today);
- the web page.

The commands are registered when Klipper loads `[ace2k_u1]`. The adapter hooks the U1's feed
module about a second after Klipper is ready. Until then, `ACE_ADAPTER_STATUS` says
`ace2k_u1: not hooked yet`. Lanes are numbered 1–4; lane n feeds head n.

## G-code commands

### `ACE_ADAPTER_STATUS`

    ACE_ADAPTER_STATUS

Shows what the adapter hooked. No parameters.

- Without a hook, it prints `ace2k_u1: not hooked: <reasons>`. The reasons are listed under
  [Troubleshooting](install.md#troubleshooting).
- In the first second after Klipper is ready, it prints `ace2k_u1: not hooked yet`.
- Once hooked, it prints one line per hooked lane. When the tags are off, the first line is
  `ace2k_u1: tags off: <reason>`.

    lane 1 -> e0: bay=yes state=… head_sensor=… wheel=… follow=wanted behind=no grip=off tag=… applied=none

| Field | Meaning |
|---|---|
| `lane n -> e<head>` | the lane and the head (extruder) it feeds |
| `bay` | `yes` when the U1 has been told of a filament in the lane's bay |
| `state` | the U1's state of that channel |
| `head_sensor` | the head's filament sensor (`True` when it sees filament) |
| `wheel` | the lane's wheel count: the encoder stand-in the U1's tangle check reads |
| `follow` | `wanted` or `off`: whether the adapter keeps the lane's follow armed |
| `behind` | `e<head>` while the lane's filament waits behind a piece the head holds, else `no` |
| `grip` | `<grip_mm> mm` while the lane's grip is set, `off`, or `off (wanted, to be set again)` |
| `tag` | the lane's tag state (`read`, `no_tag`, …) with the record's brand, name and colour |
| `applied` | what the tag last did to the head (vendor, type, subtype, colour), or `none` |

### `ACE_EJECT`

    ACE_EJECT LANE=<1-4> [WAIT=<0|1>]

Takes lane n's filament out of its bay, whatever its head holds.

| Parameter | Meaning |
|---|---|
| `LANE` | required, 1–4 |
| `WAIT` | `1` (the default): return once the eject has decided what to do; `0`: return at once. Neither waits for the U1's own unload, and `WAIT=1` gives up after 60 s with a console line (the eject runs on) |

What happens depends on the head's filament sensor:

- **The sensor is clear.** The follow is stopped, and the unit unloads the filament out of the bay.
- **The sensor sees filament.** The adapter first rolls the filament back a few millimetres
  (`eject_probe_mm`, `eject_probe_speed`). After `eject_settle_ms`, it reads the lane's buffer
  plunger:
  - at rest: the filament is free, and the unit unloads it;
  - moved: the head's gear holds the filament. The probe's length is fed back, and the U1's own
    unload of that head runs (heat, tip forming, cut). Then the unit takes the filament out of
    the bay.

The steps and their console lines are under [Taking a filament out of its bay](install.md#taking-a-filament-out-of-its-bay).

**Refusals.** A refusal is a G-code error, `ace2k_u1: lane n eject refused: <reason>`, and nothing
moves. The reasons:

- `the adapter is not hooked`, or `the lane is not hooked`;
- `an eject is already running` (the unit's unload included);
- `in use by the print`: a print (printing or paused) uses the lane. That is, the lane is moving,
  or its head's sensor sees filament;
- `the lane is in error — ACE_CLEAR LANE=n first`;
- `the lane is busy (<mode>)`: the lane is doing something other than its follow;
- `no filament in the bay`;
- `the U1 is loading/unloading this head`: one of the U1's own flows (load, unload, manual feed,
  preload) is running on the head;
- `the lane's status cannot be read — <error>`.

If an eject stops before it is done, the console says why: `ace2k_u1: lane n eject
failed: <reason>` (for example `a print started`).

The adapter's `[ace2k_u1]` keys `eject_probe_mm` (5–30, 10), `eject_probe_speed` (5–30 mm/s, 10)
and `eject_settle_ms` (200–2000, 500) tune the probe. There is no G-code to change them at run
time.

### `ACE_CLEAR`

    ACE_CLEAR [LANE=<1-4>]

This is ace2k's command. It takes a lane out of its error state (see
[ace2k's `docs/commands.md`](https://github.com/tobecwb/ace2k/blob/main/docs/commands.md)). Once
the adapter has hooked, it registers the command again, wrapped. The parameters, the help text and
when the command is ready are ace2k's.

What the wrapper adds: after ace2k's `ACE_CLEAR` has run, with `LANE` given, the adapter releases
that lane if it was counted as **waiting behind a piece** in its head. It does this even if ace2k's
`ACE_CLEAR` raised an error; that error is raised afterwards. Released means:

- the lane no longer waits;
- its holds end, as a U1 load's would;
- its next load takes the head.

Outside a print, with the head reading clear and a filament in the bay, the U1 is told of the
filament once (its preload). The console says
`ace2k_u1: lane n: ACE_CLEAR — no longer counted as waiting behind e<head>'s piece; its next load
takes the head`.

For a lane not waiting behind a piece, and for `ACE_CLEAR` without `LANE`, the wrapper does nothing
more. Use it when a head sensor is stuck reading filament.

After a lane error and `ACE_CLEAR`, or after the U1 resumes, the lane's follow is armed again on
its own
([When a spool runs out during a print](install.md#when-a-spool-runs-out-during-a-print)).

## Macros

`config/ace2k-u1.cfg` carries no `[gcode_macro …]` section. Its only sections are `[ace2k_u1]`
(the adapter) and `[ace2k_u1_history]` (the web page's recorder).

Older releases carried `ACE_EJECT` as a macro, and the burn-in's `_ACE_ONE_TO_ONE`, `ACE_TO_HEAD`,
`ACE_FROM_HEAD` and the `_PRINT_START_ACE_LANES`, `_PRINT_END_ACE_LANES`,
`_CANCEL_PRINT_ACE_LANES` hooks. When you update, delete them from the printer's copy
([Copy](install.md#copy) and [Configure](install.md#configure)).

If you add a macro to the file, its name must contain no digit: Klipper reads a digit as the start
of a parameter.

## The web page

`http://<printer>/ace2k/` (install it as described under
[The web page](install.md#the-web-page)). It is a static page. It talks only to Moonraker, and it
sends the same G-codes as the console. It is in English, and works on a phone and on a desktop.

**Top bar.** It shows:

- the ace2k version;
- `link ok` or `link down`;
- `health ok` or `health: <what fails>`;
- `history not recorded: <why>` when the recorder failed.

**Stop all** stops every lane of the unit (`ACE_STOP`), after a confirmation. During a print it
stops only the lanes the print is not using (`ACE_STOP LANE=n` for each), and it is disabled when
there are none. A banner shows `disconnected — retrying` when the page cannot reach Moonraker, or
Klipper's state when Klipper is not ready.

**Dryer strip.** It shows the dryer's state (`idle`, `starting`, `heating`, `cooldown` or `fault`)
and a summary. **Open** unfolds its panel.
- Idle: presets (PLA 50 °C 6 h, PETG 60 °C 6 h, ABS 65 °C 6 h, TPU 60 °C 6 h), editable
  temperature (15–65 °C) and time (up to 24 h), and **Start drying** (`ACE_DRY`). A **Manual**
  fold: **Fans on** for 60 s and **Fans off** (`ACE_FAN`), and **Open / Close** for the bottom and
  rear exhaust flaps (`ACE_FLAP`).
- Starting or heating: the time left with a progress bar, the target, the chamber's temperature
  and humidity, the two heater outlets, fans and flaps, and **Stop** (`ACE_DRY_STOP`).
- Cooldown: the fans run until both heater outlets are at most 45 °C and have stopped cooling.
  This takes at most 10 min.
- Fault: the reason in words, the outlets, **Clear fault** (`ACE_DRY_CLEAR`, accepted once the
  heater is cool) and **Dryer log** (`ACE_DRYER_LOG`). A thermal-cutout fault clears only with a
  power cycle.

**The unit.** It shows the four bays, each with its spool's colour and type and a state label
(`following`, `loaded`, `in bay`, `moving`, `empty` or the error's name). Click a bay to select its
lane.

**The lane card.** Lane n → head n. It shows:

- the spool (vendor, type, subtype, from the head's setting on the U1; `(tag)` when it came from
  the tag);
- the encoder's travel;
- the tag's state, in words;
- the buffer (`at rest`, `pushed`, `taut`);
- the last event;
- whether feed-forward is on.

The actions shown depend on the lane's state:
- **Load** (`ACE_LOAD LANE=n WAIT=0`) — a filament in the bay, lane idle;
- **Eject** (`ACE_EJECT LANE=n WAIT=0`) — a filament in the bay, after a confirmation;
- **Stop** (`ACE_STOP LANE=n`) — the lane is moving;
- **Clear** (`ACE_CLEAR LANE=n`) — the lane is in error;
- **Read tag** (`ACE_RFID_FORGET LANE=n`) — a tag was read or none was found: the lane's tag goes
  back to pending and the unit reads it on the lane's next move, the same tag included;
- **Edit spool** — a lane whose tag was not read: type (the U1's filament table), vendor and
  colour set the head's filament setting on the U1 (`SET_PRINT_FILAMENT_CONFIG`); the U1 resets it
  when the bay empties;
- **Advanced** fold: **Feed** and **Rollback** by a length of 1–500 mm at 20 mm/s (`ACE_FEED`,
  `ACE_ROLLBACK`, lane idle with a filament), and **Feed-forward on / off** (`ACE_FEED_FORWARD`).

During a print, Load, Eject, Stop, Feed, Rollback, Feed-forward and Edit spool are disabled on a
lane the print uses. To stop that lane, pause the print on the U1. Clear and Read tag stay
available. The state is checked again when you click a button.

**Chart.** It shows the chamber's temperature and humidity, the two heater outlets and the dryer's
target, over 1, 6 or 24 hours. The legend buttons hide a series. A tap or a hover shows the time
and every value.

**Events.** It lists the console's ace2k lines and the dryer's and lanes' transitions, newest
first. It has filters (All, Errors, Lane 1–4, Dryer) and **Show more…**.

`[ace2k_u1_history]` records the history behind the chart and the events: a sample every 30 s,
seven days kept, under `printer_data/logs/ace2k/`.
