# Commands

This page lists what the adapter adds to ace2k's own G-code commands
([ace2k's `docs/commands.md`](https://github.com/tobecwb/ace2k/blob/main/docs/commands.md)):

- commands of its own;
- one wrapper around ace2k's `ACEK_CLEAR`;
- with a head that several lanes feed, wrappers around some of the U1's own commands
  ([The U1's commands with a shared head](#the-u1s-commands-with-a-shared-head));
- the two lane-swap macros of `config/ace2k-u1.cfg`;
- the web page.

The commands are registered when Klipper loads `[ace2k_u1]`. The adapter hooks the U1's feed
module about a second after Klipper is ready. Until then, `ACEK_ADAPTER_STATUS` says
`ace2k_u1: not hooked yet`. Lanes are numbered 1–4. With one unit, lane n feeds head n. With
several units a lane is named `<unit>:<lane>` (`b:1`), and `lanes` in `[ace2k_u1]` places it on a
head ([Several units](configuration.md#several-units)).

## G-code commands

### `ACEK_ADAPTER_STATUS`

    ACEK_ADAPTER_STATUS

Shows what the adapter hooked. No parameters.

- Without a hook, it prints `ace2k_u1: not hooked: <reasons>`. The reasons are listed under
  [Troubleshooting](install.md#troubleshooting).
- In the first second after Klipper is ready, it prints `ace2k_u1: not hooked yet`.
- Once hooked, it prints one line per hooked lane. When the tags are off, the first line is
  `ace2k_u1: tags off: <reason>`.
- A unit whose status cannot be read gives each of its lanes one line,
  `lane a:1 -> e0: ace2k a's status could not be read`; the other units' lanes print as usual.

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
| `grip` | `<grip_mm> mm` while the lane's grip is set, `the park`, `the wait` (a lone head's bay waits for the U1's load), `off`, or `off (wanted, to be set again)` |
| `tag` | the lane's tag state (`read`, `no_tag`, …) with the record's brand, name and color |
| `applied` | what the tag last did to the head (vendor, type, subtype, color), or `none` |

### `ACEK_EJECT`

    ACEK_EJECT [UNIT=<name>] LANE=<1-4> [WAIT=<0|1>]

Takes lane n's filament out of its bay, whatever its head holds.

| Parameter | Meaning |
|---|---|
| `UNIT` | the unit that owns the lane. Optional with one unit, and then it must name that unit; required with several units |
| `LANE` | required, 1–4 |
| `WAIT` | `1` (the default): return once the eject has decided what to do; `0`: return at once. Neither waits for the U1's own unload, and `WAIT=1` gives up after 60 s with a console line (the eject runs on) |

What happens depends on the head's filament sensor:

- **The sensor is clear.** The follow is stopped, and the unit unloads the filament out of the bay.
- **The sensor sees filament.** The adapter first rolls the filament back, up to
  `eject_probe_mm` (80 mm) at `eject_probe_speed`. This is longer than the slack the follow can
  leave in the tube and the buffer. The lane's buffer plunger is watched as it goes:
  - the plunger is pulled tight: the head's gear holds the filament. The rollback stops;
  - the rollback ends with the plunger at rest (read after `eject_settle_ms`): the filament is
    free, and the unit unloads it;
  - the rollback ends with the plunger moved: the head's gear holds the filament.

  What stops the rollback on a plunger pulled tight depends on the unit's firmware:
  - ace2k v0.13.0 or later: the unit stops it itself, when the plunger stays pulled tight while
    the filament moves less than `held_mm` (3 mm, an `[ace2k]` key of ace2k's `commands.md`) for
    the rollback's held window (see "The held window" below: 600 ms at 10 mm/s). A filament that
    still comes, such as a thick tip dragged through the tube, does not stop it. The adapter reads
    no status while the rollback runs. If the link is lost, the unit stops the rollback, and the
    eject fails;
  - an older firmware: the adapter stops it when two reports in a row read the plunger pulled
    tight. If the link is lost during the rollback, it stops and counts as held.

  Without a proven link at the start the rollback is only 10 mm, and its end counts as held. When
  the gear holds the filament, the filament the probe pulled out is fed back, and the U1's own
  unload of that head runs (heat, tip forming, cut). Then the unit takes the filament out of the
  bay. After the unit's own stop, the motor turned for the held window with the filament still,
  so when the unit gives no filament figure, that window's motor is not fed back.

  **The held window.** With ace2k v0.13.0 or later, each of the adapter's roll backs that the unit
  stops on a held tip (the eject's probe, the probe of a shared head, the free check and the ease
  to the plunger's rest) carries its own held window. Against a held tip the motor turns on for
  the whole window, so the window is set from the move's speed: about 6 mm of motor. The window is
  never so short that friction could end the move: the friction floor, ace2k's
  `feed.held_restart_mm` over the window, stays at or under 75 % of the move's speed. A thick tip
  dragged through the tube comes at more than that, so it runs on. The window stays within the
  unit's bounds (50 to 2 000 ms). With `held_mm` at 3 mm the windows are 600 ms at 10 mm/s, 200 ms
  at 30 mm/s and 120 ms at 50 mm/s, so a held tip is pulled about 6 to 7.5 mm at every speed. A
  larger `held_mm` makes the 75 % rule set a longer window: at 6 mm and 10 mm/s it is 988 ms.
  The window cannot pass the unit's longest, 2 000 ms, so `held_mm` has a limit: about 13.3 mm
  keeps the 75 % at the unit's least speed of 9 mm/s. A larger `held_mm` is refused when the
  adapter hooks (`ace2k_u1: not hooked: … held_mm: … keep held_mm at 13.3 mm or less`), because
  friction could then end a slow move held.
- **The safety net.** If the filament was found free but the unit's unload stops `stuck`,
  `motor_stalled` or `tangled` within the first 150 mm, with the head's sensor still seeing
  filament and no print or U1 flow on the head, the gear holds it after all. The adapter clears the lane's error itself, feeds back
  the filament the probe and the unload pulled out, and runs the U1's unload of the head, as above. This happens once
  per eject. A `stuck` further out, or any other end, is a real failure.

**A short filament.** When the lane holds only a short length (a few hundred millimeters), its
end can leave the unit's drive gear while it still lies under the bay's sensor. The unit cannot
pull it further, and its unload ends `unload_incomplete`. With the head's sensor not seeing that
filament (it is clear, or it sees another lane's), the eject is done all the same: the
adapter clears the lane's error itself and says `ace2k_u1: lane n ejected (<mm> mm back): its tail
is past the unit's drive gear, at its bay: take it out by hand`, with what the whole eject brought
back (every unload of it). Pull the filament out of the bay.
An eject run again before that turns the motor about 100 mm with nothing to drive and says
`ace2k_u1: lane n: nothing came back: its tail is already past the unit's drive gear, at its bay:
take it out by hand`. It stays a failure, with its usual line, when the unload spent its whole
length (the filament was still coming back), during one of the U1's own flows on the head, and
when the head's sensor still sees that filament. During a print or a pause it stays a failure
too, with one exception: the unit's unload that follows the U1's own end unload of a print (a
head that the print used, with "Unload the filament at the end") ends the same way as
`ACEK_EJECT`, and a head farther than one unload is unloaded again. Once the print pauses, that
exception ends. An unload run again is not started while the U1 loads or unloads that head: the
console says
`ace2k_u1: lane n eject not started — the U1 is loading/unloading this head — ACEK_CLEAR …`.

**The spool record.** An eject that ends with the bay empty drops the lane's
[`ACEK_SPOOL`](#acek_spool) record and its forgotten tag ([`ACEK_TAG_FORGET`](#acek_tag_forget)),
as when you take the spool out by hand: you eject to take the spool out. This holds on every path,
with the head empty or held, on a shared head or on a head the lane feeds alone. An eject that
leaves its tail at the bay drops them once you pull the tail out. So does an eject whose unit
unload ends in any error (`unload_incomplete`, `stuck` or any other), once the bay reads empty:
you pull the tip out by hand, seconds or minutes later. The drop waits, however long that takes,
until a real move of the lane: a move the adapter starts (the follow among them), the lane's mode
out of idle and error in its status (a move of the unit), or a load of the head that the U1
starts. A push or a pull by hand never ends it. A filament put into the empty bay, or a new record
from Edit spool, ends it too. These keep both: a failed eject
whose bay stays full, an eject that ends before the unit's unload (it does not start, the U1's
unload of the head fails, no material set), an unload failure while the U1 runs a flow on the
head or a print runs, and an eject that a new pull of the lane cancels.

The steps and their console lines are under [Taking a filament out of its bay](install.md#taking-a-filament-out-of-its-bay).

**Several units.** The rule is ace2k's own: without `UNIT=` the command is a G-code error that
sends nothing, `ace2k: ACEK_EJECT needs UNIT=<a|b>: this printer has several units`, and an
unknown name is `ace2k: no unit <x>; units: a, b`
([ace2k's `docs/commands.md`](https://github.com/tobecwb/ace2k/blob/main/docs/commands.md#several-units)).

**Refusals.** A refusal is a G-code error, `ace2k_u1: lane n eject refused: <reason>`, and nothing
moves. With several units the lane reads `b:1`. The reasons:

- `the adapter is not hooked`, or `the lane is not hooked`;
- `an eject is already running` (the unit's unload included);
- `in use by the print`: a print (printing or paused) uses the lane. That is, the lane is moving,
  or its head's sensor sees filament;
- `the lane is in error — ACEK_CLEAR LANE=n first` (with several units, `ACEK_CLEAR LANE=n UNIT=b first`);
- `the lane is busy (<mode>)`: the lane is doing something other than its follow;
- `no filament in the bay`;
- `no material set — set it with Edit spool`: the lane is loaded in its head and its material is
  not known: no tag read, no Edit spool record, and no head setting that belongs to its spool (see
  "A lane's material" below);
- `the U1 is loading/unloading this head`: one of the U1's own flows (load, unload, manual feed,
  preload) is running on the head;
- `the lane's status cannot be read — <error>`.

If an eject stops before it is done, the console says why: `ace2k_u1: lane n eject
failed: <reason>` (for example `a print started`).

The adapter's `[ace2k_u1]` keys `eject_probe_mm` (20–150, 80; a value below 20 is raised to 20 with a warning), `eject_probe_speed` (5–30 mm/s, 10)
and `eject_settle_ms` (200–2000, 500) tune the probe. There is no G-code to change them at run
time.

### `ACEK_HEAD_PROBE`

    ACEK_HEAD_PROBE HEAD=<1-4> [UNIT=<name>] [LANE=<1-4>]

Runs, outside a print, what a print's start or a lane swap does to a head that several lanes feed.
It is a bench tool.

- **The head's sensor sees filament** (no `LANE`): the probe a print's start runs when no lane is
  known in the head. The head must be cold. Each lane in turn rolls back up to `probe_mm` at
  `probe_speed`: the one whose plunger goes taut, or whose roll back empties the head's sensor, is
  the lane in the head. With ace2k v0.13.0 or later, the unit stops a roll back itself when the
  plunger stays taut while the filament stands still for its held window (120 ms at 50 mm/s, see
  "The held window" under `ACEK_EJECT`), so a thick tip dragged through the tube is not taken for
  the lane in the head. With an older firmware, the adapter stops it when two reports in a row
  read the plunger taut. The adapter always watches the head's sensor itself, because the unit
  cannot see it. A lane whose bay is empty is not moved, nor is one just put in its bay
  and loaded to its grip only: neither can be in the head, and a roll back would take a gripped
  lane out of its unit. Every lane is fed back; a feed back that ends `blocked`, the tip back
  against the head's gear, is the lane re-seated, and any end but `done` or `blocked` fails the
  probe. The lane found is named the head's, as the start
  names it, and it follows: `ace2k_u1: head 1: a:2 named the head's, following` (`was the head's
  already` when it was).
- **The head's sensor is empty** (`LANE` given): the head's other lanes are stopped, and the lane
  is named the head's and pushed to its
  sensor at `swap_speed`, as a swap pushes the new lane. If the push stops blocked before the
  sensor, the head's other lanes are pushed in turn at `probe_speed` to find the one past the
  splitter; it is rolled back and the lane is pushed again. A push that stops blocked is pushed
  again from where it stopped, up to 3 times (the unit forgives one tip snag per move); one that
  gains under 5 mm is a hard stop. Still blocked, it rolls back all it pushed, at `swap_speed`. At the sensor, a plunger the push
  left pressed (not taut) is eased slowly back to rest; an ease that cannot finish leaves it
  pressed, with a line, and the push stands. With ace2k v0.13.0 or later, the unit itself stops
  the ease at the plunger's rest, or on a held tip. Then the lane follows:
  `ace2k_u1: head 1: a:2 pushed to its sensor, following`. A push that fails gives the head back
  to the lane it had, following again if it followed; with no lane before, the pushed lane stays
  named the head's. A line says which.

**Refusals.** A G-code error, nothing moved: `ace2k_u1: head n probe refused: <reason>`. The
reasons:

- `no head fed by several lanes is hooked`, or `its head is fed by one lane alone`;
- `a print is printing or paused`, `a swap is pending`, or `an eject is running on one of its
  lanes`;
- `head n's sensor cannot be read`, or `its temperature cannot be read`;
- with filament at the sensor: `its sensor sees filament: probe it without LANE`, or
  `it is heating or at <probe_max_temp> °C or more: let it cool`;
- with the sensor empty: `its sensor sees no filament: name the LANE to push`, or
  `LANE is not one of its lanes`, or `its bay is empty`.

A probe or a push that fails is the command's G-code error, with the probe's reason. A `UNIT=` that
names no unit is ace2k's own error, before anything else is checked.

### `ACEK_FREE_CHECK`

    ACEK_FREE_CHECK [UNIT=<name>] LANE=<1-4> [LENGTH=<mm>] [SPEED=<mm/s>]

Runs a lane swap's free check alone, outside a print. It is a bench tool: it tells how fast the
check can go and still read right. The lane must be the one in its head, a head that several lanes
feed. It rolls back up to `LENGTH` at `SPEED`, and the adapter watches its buffer plunger as the
swap does. The head's other lanes are stopped and kept still meanwhile. The check stops at the
first of these:

- the plunger reads taut: the head's gear still holds the filament. With ace2k v0.13.0 or later,
  the unit stops the roll back itself when the plunger stays taut while the filament moves less
  than `held_mm` for the move's held window, set from `SPEED` (see "The held window" under
  `ACEK_EJECT`: 200 ms at 30 mm/s, 600 ms at 10 mm/s). With an older firmware, the adapter stops
  it when the plunger reads taut twice in a row;
- the head's sensor goes empty: the tip has left it, nothing holds the filament;
- the roll back ends.

Then the lane is fed back what it rolled back, at `probe_speed`, so it is where it was with its
plunger at rest, and a follow the check stopped is armed again. A roll back that ran past its
deadline goes on with the unit's tail and can report more than `LENGTH`: the lane is fed back all
of it, up to `LENGTH` plus the unit's tail (a distance past that is the motor's, not the
filament's). A line says what it found:

    ace2k_u1: a:1 free check, 100 mm at 30 mm/s: held by head 1, taut after 31.5 mm; fed back
    ace2k_u1: a:1 free check, 100 mm at 30 mm/s: free, 4.0 mm rolled back; fed back

When the unit gives no distance for the move, the line says so and nothing is fed back.

| Parameter | Meaning |
|---|---|
| `UNIT` | the unit that owns the lane; required with several units |
| `LANE` | required, 1–4 |
| `LENGTH` | 10–300 mm; default `probe_mm` (100) |
| `SPEED` | 5–80 mm/s, kept within the unit's own speed range; default `probe_speed` (50). The line shows the speed used |

**Refusals.** A G-code error, nothing moved: `ace2k_u1: lane n free check refused: <reason>`. The
reasons:

- `no head fed by several lanes is hooked`, or `the lane is not hooked`;
- `its head is fed by one lane alone`;
- `a print is printing or paused`, `a swap is pending`, or `an eject is running on one of its
  lanes`;
- `it is not the lane in head n: name it with ACEK_LANE_ACTIVE first`;
- `head n's sensor sees no filament: nothing to check`, or `head n's sensor cannot be read`;
- `its bay is empty`, or `the lane's state cannot be read`.

A check that ends any other way (the lane `stuck`, its link lost, its move past its time) is the
command's G-code error.

### `ACEK_SPLITTER_MEASURE`

    ACEK_SPLITTER_MEASURE HEAD=<1-4>

Measures how far a lane of a shared head must roll back to leave the splitter, and stages
`headN_splitter_mm` for `SAVE_CONFIG`.

At a swap, the old lane rolls back this far, so the new lane can pass the splitter. The command
measures it with your tubes, so you do not need a tape.

Before you run it:

- No print is running.
- The head is empty. Unload it from the U1's screen first.
- Each unit on the head has at least one lane with filament in its bay, and the head has at least
  two lanes with filament in all. The command measures each unit with its first lane with filament
  and a second one: the unit's next lane with filament when it has one, else a lane of another
  unit.
- Every lane is behind the splitter, at its parking point. A lane parked too close to the splitter
  stops the command with an error: unload it to its bay and measure again.
- No lane is left in the shared tube. The command pushes the first lane to the head's sensor, and
  if it cannot reach the sensor, it stops with an error and nothing more moves.

What it does, for each unit on the head:

1. It pushes the first lane (A) to the head's sensor, at the probe speed. Then A backs off until
   the sensor is clear, at most 40 mm, and 10 mm more. If the sensor stays set, the command stops with an error.
2. It pushes the second lane (B) slowly until it meets A's filament at the splitter.
3. It rolls A back a little at a time, and B tries to pass each time. The smallest roll back that
   lets B pass is where A's tip leaves the splitter.
4. A rolls back that far plus 50 mm, and B goes back to where it was.

It takes about 4 minutes for each unit on the head. Then it prints the value and stages it:

    ace2k_u1: head 1: head1_splitter_mm 890 staged (was 900); run SAVE_CONFIG (if head1_splitter_mm is set in an included file such as ace2k-u1.cfg, delete or comment out that line first: Klipper refuses to save a key that an included file sets)

When no value was set, the line says `(was the default 1000); run SAVE_CONFIG` and ends there.

If you set the value by hand in an included file such as `ace2k-u1.cfg`, delete or comment out that
line before `SAVE_CONFIG`, because Klipper refuses to save a key that an included file sets. After
`SAVE_CONFIG` the value is at the end of `printer.cfg`.

With several units on the head, it stages the largest value, plus the 50 mm. The value is rounded
up to the next 10 mm.

Run `SAVE_CONFIG` to keep it. Klipper restarts and the swap uses the new value.

A value more than 150 mm from the one in effect is staged with a warning. Check the tubes before
you run `SAVE_CONFIG`.

The command is refused, and nothing moves, when a print is running, when the head's sensor sees
filament, when a lane of the head is moving, when a unit on the head has no lane with filament, or
when the head has only one lane with filament.
If a move fails, the command stops, stages nothing, and says where each lane is. The first lane
stays the head's lane, because its filament may still be in the shared tube:

    ace2k_u1: head 1: the measure failed; a:1 stays the head's lane

### `ACEK_SPOOL`

    ACEK_SPOOL [UNIT=<name>] LANE=<1-4> TYPE=<material> COLOR=<rrggbb> [VENDOR=<name>]

Sets the spool in a lane's bay. Use it for a spool without a tag. The web page's **Edit spool**
sends it.

| Parameter | Meaning |
|---|---|
| `UNIT` | the unit that owns the lane; required with several units |
| `LANE` | required, 1–4 |
| `TYPE` | required: the material (`PLA`, `PETG`) |
| `COLOR` | required: the color as six hex digits, `ff8800` (a `#` in front is accepted) |
| `VENDOR` | optional; `Generic` by default |

The console says `ace2k_u1: lane a:2 spool: PLA #ff8800`.

The record is saved in `ace2k_u1_spools.json`, beside `printer.cfg`, so it stays after a restart.
It is dropped when you take the spool out of its bay by hand, when an
[`ACEK_EJECT`](#acek_eject) ends with the bay empty or the bay empties after it (a failed eject
included), and when the spool runs out in a shared head.
The adapter's own roll backs (a swap, a refill, the probe, a load that is refused) keep it.

Who reads it:

- [`ACEK_PLAN`](#acek_plan) matches the file's colors to the spools with it.
- A lane swap sets the head's filament setting on the U1 from it, when its lane goes into the
  head. [`ACEK_HEAD_SPOOL`](#acek_head_spool) does the same on request.

A tag read on the lane always comes first. Then, on a head that only this lane feeds, the head's
filament setting on the U1 comes before the record. On a shared head, the record comes first.

**Refusals.** A G-code error, nothing saved:

- `ace2k_u1: COLOR must be 6 hex digits (rrggbb)`;
- `ace2k_u1: TYPE= is the material (PLA, PETG, ...)`;
- `ace2k_u1: spool not saved: <error>`: the file could not be written;
- `ace2k_u1: no ace2k unit loaded`;
- `ace2k_u1: lane 2: its unit is not loaded yet`: with several units, the config declares the
  unit `UNIT=` names, but Klipper has not loaded it.

A `UNIT=` that names no unit is ace2k's own error.

### `ACEK_TAG_FORGET`

    ACEK_TAG_FORGET [UNIT=<name>] LANE=<1-4> [READ=<0|1>]

Forgets the tag read on a lane, so that you can set the spool yourself. Use it when a tag's color
or material is wrong. The web page's **Forget tag** sends it, and **Read tag again** sends it with
`READ=1`.

| Parameter | Meaning |
|---|---|
| `UNIT` | the unit that owns the lane; required with several units |
| `LANE` | required, 1–4 |
| `READ` | `1`: undo the forget instead (below). `0` by default |

What happens:

1. The unit forgets the tag (ace2k's `ACEK_RFID_FORGET`).
2. The adapter no longer writes anything from that tag to the head's filament setting. It does not
   take the tag again until the spool leaves its bay, even when the unit reads it again.
3. The lane's spool is now its `ACEK_SPOOL` record. Set it with **Edit spool**.

The console says `ace2k_u1: lane a:2 tag forgotten: set its spool with Edit spool`.

The forget is kept in memory only: a Klipper restart drops it. The unit's forget also drops the
read it keeps for the lane across a restart, so after a restart the lane has no tag until the
unit reads it again. The forget also ends when you take the spool
out of its bay by hand, when an `ACEK_EJECT` ends with the bay empty, or when you put a filament
into the empty bay. It does not end when the adapter's own roll back empties the bay.

**`READ=1`** undoes it. The unit reads the tag again on the lane's next move, the same tag included.
The console says `ace2k_u1: lane a:2 will read its tag again`.

**Refusals.** A G-code error, nothing forgotten:

- `ace2k_u1: the adapter is not hooked`;
- `ace2k_u1: lane a:2 feeds no head: nothing to forget`;
- `ace2k_u1: lane a:2 has no tag read: nothing to forget` (without `READ=1`);
- `ace2k_u1: no ace2k unit loaded`, or `ace2k_u1: lane 2: its unit is not loaded yet`
  (as for `ACEK_SPOOL`).

### `ACEK_HEAD_PREPARE`

    ACEK_HEAD_PREPARE [UNIT=<name>] LANE=<1-4>

Gets a lane ready for the page's **Load into head N**, before the U1's load. The U1 takes the
load's temperature from the head's setting when its load starts, so the lane's material must be
known by then. A lane whose material is set needs nothing: the console says
`ace2k_u1: lane a:2: its material is set`. Otherwise the unit pushes the lane to the head's
sensor, and the tag is read on the way (`ace2k_u1: lane a:2: its tag read on the way to e0`).
When its material is still not set, the lane is rolled back only as far as needed, and the
command is an error. The lane goes back until the head's sensor clears, then 50 mm more on a
head that one lane feeds, so its tip leaves the head. On a shared head it goes back
`headN_splitter_mm` more, at `swap_speed`, so its tip is behind the splitter, where a swap leaves
a lane. A sensor that never reads clear, or cannot be read, stops the roll back at the farthest
the push may have gone. It is not taken back to its parking point. A shared head then has no
lane known in it, as after a restart: the console says `ace2k_u1: head 1: a load was refused and
rolled back behind the splitter: its lane is not known now — a print or a swap finds it, or name
it with ACEK_LANE_ACTIVE`. The page's **Load into head N** names its lane first, so either lane
can be loaded next. The error is:
`ace2k_u1: lane a:2: no material set — set it with Edit spool: its load refused`.

**Refusals.** A G-code error, nothing moved: the adapter is not hooked, a print is printing or
paused, the lane feeds no head, the lane is not the head's lane (`ACEK_LANE_ACTIVE` first on a
shared head), or the head's sensor already sees filament.

### A lane's material

A lane's material is set when its tag was read, or when Edit spool gave it a record
([`ACEK_SPOOL`](#acek_spool)). On a head that one lane feeds, the head's setting on the U1 counts
too, and comes first, when it is set (its type is not `NONE`) and belongs to the spool in the bay:
it was written for that spool (a tag read applied, the head set from the lane's spool, Edit spool
or Define even with the same value, or a type or color changed on the U1's screen, with the bay
full; a command that writes the color alone or clears the type does not count), or Klipper
started with the bay full. A setting left from the spool before, or set before the new spool went
in, does not count for it. It stops counting once the bay is seen empty and the lane then reads out of
its head for 2 s, when a new spool goes in, and after `ACEK_TAG_FORGET`. A read of another tag or a record
that came after it, and was not written to the head, speaks instead; the same tag read again does
not, nor does the first read after a restart with the bay full since. So the adapter and the
page take the same material: the head's setting, then the tag, then the record. On a shared head
the setting counts only when it was written for the lane, until that lane's bay is seen empty. A
lane whose bay reads empty has no material set, unless it is still loaded in its head (its spool
ran out): its setting, tag or record then tells what its rest is. A lane with no material set is
refused:

- by `ACEK_EJECT` and the page's **Eject** (`no material set — set it with Edit spool`), up
  front, only when the lane is loaded in its head: the U1 holds the head loaded, the lane is the
  head's lane, and the head's sensor sees filament. That eject needs the U1's unload of the head,
  which heats it. Any other lane is ejected: a lane parked in the tube or only at the unit, and a
  tip waiting at the head's sensor, are rolled back to the bay with no heat. An eject whose probe
  finds the filament held by the head's gear feeds back what it pulled, so the filament is not left
  taut, then stops before the U1's unload, with nothing heated:
  `ace2k_u1: lane a:2 eject failed: no material set — set it with Edit spool`.
  The screen's Unload is not stopped this way: its unload is the U1's own;
- by `ACEK_PLAN_SET` and a start of the planned file (`T0: a:2 has no material set: set it with
  Edit spool`), and the page's **Print** is disabled;
- by the U1's own load, from its screen or from the page. Once the push has brought the lane to
  the head's sensor (its tag read on the way), a lane with no material set is rolled back out of
  the head (as for `ACEK_HEAD_PREPARE`: 50 mm past the head's sensor, or behind the splitter of
  a shared head, whose lane is then not known) and the load fails: `ace2k_u1: lane a:2: no material set — set it with Edit spool: its
  load refused`. A lane whose type differs from the type the head was set for is written on the
  head, rolled back, and the load fails: `ace2k_u1: head 1: a:2 is PETG, the head was set for PLA:
  set now — load it again`. The next load heats for it. A lane that the head's sensor already saw
  before the load is refused the same way but not pulled back, because its filament may be in
  the head's gear. Only a lane that the preload or `ACEK_HEAD_PREPARE` left at the sensor, and
  that nothing moved since, is known to wait short of the gear: that one is rolled back.

A tag counts when it gives a material, with or without a color. A lane whose status cannot be
read at that moment is not refused.

### `ACEK_HEAD_SPOOL`

    ACEK_HEAD_SPOOL [UNIT=<name>] LANE=<1-4>

Sets the filament setting of the lane's head from the lane's spool. The lane must be the head's
lane: the one lane of its head, or the one named with `ACEK_LANE_ACTIVE`. A lane swap does the
same when its lane goes into the head, and this command runs that same step. The web page's
**Load into head N** sends it before the load, because the U1 takes the load temperature from
the head's setting. The head keeps that setting even when the load fails or never starts.

| Parameter | Meaning |
|---|---|
| `UNIT` | the unit that owns the lane; required with several units |
| `LANE` | required, 1–4 |

The spool is the lane's tag, written in the names of the U1's filament table, or else its
`ACEK_SPOOL` record. The command sends the U1's `SET_PRINT_FILAMENT_CONFIG … FORCE=1` for the
head. A type that the U1 does not know is left out, and only the color is written. The tag is
written whether `apply_tags` is on or off, as a swap writes it.

The console says `ace2k_u1: head 1: a:2's spool set on the head`.

A lane with neither a tag nor a record is not an error. The console says
`ace2k_u1: head 1: a:2 has no spool set: set it with Edit spool`, the web page shows that line as
a warning in its events, and nothing is written.

**Refusals.** A G-code error, nothing written:

- `ace2k_u1: the adapter is not hooked`;
- `ace2k_u1: lane a:2 feeds no head`;
- `ace2k_u1: lane a:1 is not in head 1: a:2 is the head's lane`, or `… no lane is the head's
  lane`: on a shared head, name the lane with `ACEK_LANE_ACTIVE` first;
- `ace2k_u1: head 1: a:1 → a:2 (T2) is pending at push — RESUME it first`: a swap that a failure
  paused sets its head's spool itself when it goes on;
- `ace2k_u1: head 1: a:2's spool not set on the head — <problems>`: a value the G-code cannot
  carry;
- `ace2k_u1: head 1: spool setting refused: <error>`: the U1 refused the setting;
- `ace2k_u1: no ace2k unit loaded`, or `ace2k_u1: lane 2: its unit is not loaded yet`
  (as for `ACEK_SPOOL`).

When nothing is written, the head's setting keeps speaking for the lane it was written for: the
command does not change which lane is in the head. A swap is different: its head has just taken a
new lane, so its setting speaks for no lane until the swap writes it.

### `ACEK_PLAN`

    ACEK_PLAN FILE=<name>

Reads a G-code file and suggests a spool for each of its tools. The web page's Print tab
sends it when you choose a file. The file is only read, never changed.

`FILE` is the file's name in the U1's G-code folder. Put a name with a space in quotes:
`FILE="my part.gcode"`.

From the slicer's config block in the file, the plan reads each tool's color, material, printing
temperature and first-layer temperature. The file must come from OrcaSlicer. From the file's body,
it reads the order of the `T` commands.

For each tool it suggests a lane with a spool of the same material and the closest color, as the
eye sees it. A spool's color and material come from its tag, else from its `ACEK_SPOOL` record or
the head's filament setting. A lane whose bay is empty is never suggested. No lane is suggested,
and you choose one, when:

| Reason (status) | Meaning |
|---|---|
| `no_material` | no lane has a spool of the tool's material, or its material is not known |
| `no_colour` | a lane of that material has no color set: set it with Edit spool |
| `too_far` | the closest spool is farther than `colour_max_distance` (25) |
| `two_close` | the second spool is within `colour_min_margin` (8) of the closest |

It also counts, for each head, the lane swaps the file will cause with the suggested spools.

The console says `ace2k_u1: plan of <name>: <n> tools`. The plan is published in the adapter's
status (`ace2k_u1`: `plan`), where the page reads it. A new `ACEK_PLAN` replaces it.

**Refusals.** A G-code error; the plan before stays as it was:

- `ace2k_u1: the adapter is not hooked`;
- `ace2k_u1: a print is on: plan the next file after it`;
- `ace2k_u1: '<name>' is not a file of the G-code folder`, or `ace2k_u1: no file <name>`;
- `ace2k_u1: <name>: no slicer config block: slice the file with OrcaSlicer`;
- `ace2k_u1: <name>: the slicer config block has no filament_colour`.

### `ACEK_PLAN_SET`

    ACEK_PLAN_SET FILE=<name> T0=<lane> T1=<lane> ... [END_UNLOAD=<0|1>]

Keeps your choice of a lane for each tool of the planned file, for its print. The page's **Print**
button sends it right before it starts the file.

| Parameter | Meaning |
|---|---|
| `FILE` | the file `ACEK_PLAN` read |
| `T<n>` | the lane for tool n, as the console names it: `a:2` with several units, `2` with one. Every tool the file prints needs one; a tool it never prints needs none |
| `END_UNLOAD` | `1`: at the print's end, also take out a shared head's other lanes the print used (below). `0` by default |

The console says `ace2k_u1: plan of <name> set: T0=<lane> (head <n>), T1=...`, one entry per
tool that has a lane. The line also goes to `klippy.log`, so the log keeps the choice of each print.

**`END_UNLOAD=1`.** The page sends it when **Unload the filament at the end** is ticked. At the
end of the print, the U1's own end unload takes out the lane that is in each head. The other lanes
of a shared head wait behind its splitter, out of the U1's reach. With `END_UNLOAD=1`, the adapter
ejects out of its bay each of those lanes the print used, as `ACEK_EJECT` does. This happens only
when the print completes, not when it is canceled.

- Only a lane known to wait behind the splitter is ejected. The console says
  `ace2k_u1: lane a:2 unloading at the print's end (parked, used)`.
- A shared head with no lane known in it (after a Klipper restart) is left as it is. Any of its
  lanes may be the one the U1 unloads. The console says
  `ace2k_u1: head 1: no lane known in it at the print's end: its other lanes stay — ACEK_EJECT
  each`.
- An eject refused for a moment is tried again every 5 s, for up to 120 s: while the U1 is still
  unloading that head, while the lane is busy, or while an eject is running. A try is skipped once
  a new print is on, or once the lane no longer waits behind the splitter.
- Any other refusal is said at once, as `ACEK_EJECT` says it.

The plan is dropped when its print ends (it completes or is canceled), when another `ACEK_PLAN`
runs, and when Klipper restarts.

**Refusals.** A G-code error; nothing is kept:

- `ace2k_u1: the adapter is not hooked`;
- `ace2k_u1: a print is on: plan the next file after it`;
- `ace2k_u1: no plan of <name>: run ACEK_PLAN FILE=<name> first`;
- `ace2k_u1: <name> changed since its plan: plan it again`. The page then plans the file again by
  itself and asks you to check the spools. It keeps the spools you chose that the new plan still
  offers. The page recognizes this refusal by its text, so the text does not change;
- `ace2k_u1: T<n> has no lane`: the file prints tool n and no lane was given;
- `ace2k_u1: T<n>: no hooked lane <lane>`;
- `ace2k_u1: T<n>: <lane> has no spool in its bay`;
- `ace2k_u1: T<n>: <lane> holds <material>, the file wants <material>`. A lane whose material is
  not known is accepted.

### `ACEK_CLEAR`

    ACEK_CLEAR [UNIT=<name>] [LANE=<1-4>]

This is ace2k's command. It takes a lane out of its error state (see
[ace2k's `docs/commands.md`](https://github.com/tobecwb/ace2k/blob/main/docs/commands.md)). Once
the adapter has hooked, it registers the command again, wrapped. The parameters, the help text and
when the command is ready are ace2k's.

What the wrapper adds: after ace2k's `ACEK_CLEAR` has run, with `LANE` given, the adapter releases
that lane if it was counted as **waiting behind a piece** in its head. It does this even if ace2k's
`ACEK_CLEAR` raised an error; that error is raised afterwards. Released means:

- the lane no longer waits;
- its holds end, as a U1 load's would;
- its next load takes the head.

Outside a print, with the head reading clear and a filament in the bay, the U1 is told of the
filament once (its preload). The console says
`ace2k_u1: lane n: ACEK_CLEAR — no longer counted as waiting behind e<head>'s piece; its next load
takes the head`.

With several units, `ACEK_CLEAR` needs `UNIT=`, as ace2k's own command does; the wrapper releases the
lane of the unit `UNIT=` names. Without it, ace2k refuses the command, and nothing is released.

For a lane not waiting behind a piece, and for `ACEK_CLEAR` without `LANE`, the wrapper does nothing
more. Use it when a head sensor is stuck reading filament.

After a lane error and `ACEK_CLEAR`, or after the U1 resumes, the lane's follow is armed again on
its own
([When a spool runs out during a print](install.md#when-a-spool-runs-out-during-a-print)).

## The U1's commands with a shared head

A **shared head** is a head that several lanes feed: its lanes are joined by `+` in `lanes`
([Several spools in one head](configuration.md#several-spools-in-one-head)). With a shared head,
the adapter wraps the commands below once it has hooked. Each keeps its name, its parameters and
its help text. The same wraps apply with `runout_source: unit` and no `+` group. The end-of-print
unload and `SET_PRINT_FILAMENT_CONFIG` are wrapped on every installation. Otherwise nothing below
is wrapped, and every command reaches the U1 or ace2k unchanged.

A **planned print** is a print started from the web page: its file has a plan set with
`ACEK_PLAN_SET`.

### `T0`–`T31`

`T0` to `T3` are the U1's commands, one per head. `T4` to `T31` are macros of the U1's config
that pick the head the U1's tool table maps the tool to.

**What changes.** In a planned print, a `T<n>` whose lane is not in its head first swaps the
head's lane. Then the U1's own `T<n>` runs. The swap goes through these steps:

1. **unload**: when the head's sensor sees filament, `ACEK_SWAP_UNLOAD` forms the old filament's
   tip and takes it out of the gear.
2. **free**: the old lane rolls back up to `probe_mm` at `probe_speed`, while the adapter watches
   its buffer plunger. A plunger pulled tight means the head still holds the filament. The adapter
   then runs the unload and this check once more.
3. **roll back**: the old lane rolls back the rest of `headN_splitter_mm` at `swap_speed`, behind
   the splitter. The head's sensor must then read empty.
4. **hand over** and **push**: the new lane becomes the head's lane and is pushed to the head's
   sensor at `swap_speed`. Its follow starts there.
5. **spool**: the head's filament setting on the U1 becomes the new lane's spool.
6. **load**: `ACEK_SWAP_LOAD` takes the filament through the gear and purges, both at the new
   tool's printing temperature.
7. **loaded**: the head's sensor must see filament.
8. **return**: the U1's own pre-extrusion runs for the tool with no purge
   (`SM_PRINT_PREEXTRUDE_FILAMENT INDEX=<tool> FORCE=1 LENGTH=0`). The load macro has purged and
   cleaned once, so this cleans the nozzle the second time, with no purge, and leaves the head at
   the U1's idle position. A refill with no tool of the plan runs the U1's
   `INNER_PREEXTRUDE_FILAMENT … LENGTH=0` instead. The next print move takes the head to the part.
   The pre-extrusion's retraction matches the slicer's. The slicer retracts a tool before its `T`
   (10 mm in the files seen) and pushes that much back when the same tool returns. So when the
   tool returns to the head, the step first retracts the rest at 30 mm/s, at the cleaning area
   where the load left the head, in one script: `SAVE_GCODE_STATE NAME=ACEK_SWAP_RETRACT`,
   `M83`, `G1 E-<mm − 0.5> F1800`, `RESTORE_GCODE_STATE NAME=ACEK_SWAP_RETRACT`. The print's E
   mode and E position stay as they were. The print's flow factor scales this retraction as it
   scales the slicer's push, so the two stay equal. The U1's pre-extrusion then retracts its own
   0.5 mm, runs its fan and cleans. The tool's value is spent as the retraction starts: a swap
   that fails there may miss it once, but never retracts it twice. The U1's macro cannot take the length itself: it fails on a
   `RETRACT_LENGTH`. The nozzle leaves the bin retracted by what the slicer will push back.
   If the retraction is the U1's 0.5 mm or less, nothing is added, and the console says
   `…, the U1's own 0.5 mm retraction covers the slicer's 0.3 mm`. Without this, the push lands
   on a nozzle the purge left full and leaves a blob on the prime tower. A tool's first use and a
   refill keep the U1's own 0.5 mm. So does a swap that runs while the print is paused, such as a
   swap finished by `RESUME`: the U1 would not count a longer retraction then. The tool's value
   stays for its next return. A reading that is not a retraction (more than 15 mm, or read during
   a pause) never replaces a tool's value. The values are kept per print.

X and Y must be homed. The console says `ace2k_u1: head 1: a:1 → a:2 (T1)` at the start and
`ace2k_u1: head 1: a:2 in (113 s)` at the end. A swap takes about two minutes.

The swap's `return` step already pre-extruded the tool, so the file's own
`SM_PRINT_PREEXTRUDE_FILAMENT` right after that `T` is skipped, even if it is forced. The console
says `ace2k_u1: head 1: pre-extrusion after the swap (T1)` when the step runs it (with
`, retracting 10 mm as the slicer did` when it matches the slicer's retraction), and
`ace2k_u1: head 1: pre-extrusion after the refill` after a refill.

The first `M109` for the swapped tool after a swap does not wait when its temperature is within
1.5 °C of the one the swap ended at. The pre-extrusion has just waited for that temperature, and a
second wait would hold the head parked and hot. An `M109` for another temperature waits as always,
for example after a swap that kept the load temperature because no printing temperature was known.
The first `M109` for the tool, the next `T` of the file, a print's start or end, or a failed swap
ends this. An `M109` for another tool waits as always.

**Pre-heat.** A printing temperature that the slicer sends for a tool whose lane is not in its
head reaches the U1 when that head is not the one printing, so a docked head is hot when its swap
starts. The head heats at most to the tip temperature of the filament now in it, because the swap
first forms that filament's tip. With PLA in the head (tip at 220 °C) and PETG next at 245 °C, the
head heats to 220 °C. The swap also keeps the slicer's value and prints at it. If the lane in the
head is not known, or the head is printing another tool, the value is only kept, as before. A
cool-down that the slicer then sends for the tool leaving that head is applied, but once the next
T docks the head it is set back to the kept value, with the same cap, when the next tool on it
needs a swap.

**The end of the print.** The U1 unloads the heads it used, one after another. The adapter first
tells every head it will unload to heat, in order, to the temperature the U1's unload heats it to.
The U1 heats two at a time, so a head is ready or nearly ready when its turn comes.

**When a swap fails**, the print pauses with a plain `PAUSE`. The head goes to its idle position,
never over the part. The last console line, and the page's top bar, say which step failed:
`ace2k_u1: head 1: a:1 → a:2 (T1) failed at free: a:1 is still held by head 1 after a second
unload`. Fix the cause, then `RESUME` (below).

**A head whose lane is not known** (after a restart, nothing remembers it; or, outside a print,
after its sensor saw filament come in with no lane of the head moving — a filament pushed up the
tube by hand, or by an `ACEK_FEED` of another lane; the console says `ace2k_u1: head 1: a filament
came into the head with no lane of it moving (by hand?): its lane is not known now — a
print, an unload or an eject probes it first`, and the head's active lane is no longer chosen): when its sensor sees
filament during the print, the print pauses with `ace2k_u1: head 1: its lane is not known: the
print pauses to probe it`, and `RESUME` probes it. When its sensor is empty, the new lane is pushed
in.

**The U1's unload or load of a head whose lane is not known** (from its screen), outside a print
or in a paused one, with the
head's sensor set: before it heats, the adapter probes the head when it is below
`probe_max_temp` (the plungers move for about 16 s), hands the head to the lane it finds, and the
unload goes on and ejects that lane, or the load goes on with that lane following. A load with the
head's sensor empty and no lane known is refused: `ace2k_u1: head 1 is shared by lanes a:1, a:2:
load refused with no lane known in it and its sensor empty — choose the lane with
ACEK_LANE_ACTIVE, then load`. When the head is hotter, the probe fails, a swap is pending
on that head or an eject runs on its lanes, the unload or the load is refused: the screen shows its
failure, the preheat is turned off, and the console says why. The screen's unload of several heads
preheats the next head while it unloads one, so such a head later in the list comes hot: unload it
alone. In a paused print the head keeps the print's heat, and a hot one is refused with `RESUME`
as the way out (it probes the head); the batch puts every head's temperature back at its end. The
console line reads, for example, as in `ace2k_u1: head 1: unload refused — no
lane known in it: the head is hot: let it cool below 60 °C and unload it again, alone (the screen
preheats the next head of a batch). Find the head's lane (ACEK_HEAD_PROBE, the head cold), or eject
the lane from the /ace2k page`. While printing (the print's end) the unload goes on as before.

**While a swap waits for its `RESUME`**, a `T` of another head is refused, and the print pauses
again: `ace2k_u1: T3 refused: head 1: a:1 → a:2 (T1) is pending at push — RESUME it first`.
`RESUME` then runs that `T` after the swap.

**What does not change.** These reach the U1's `T` unchanged:

- a `T` outside a planned print;
- a `T` whose lane is already in its head;
- a `T` of a head that one lane feeds;
- the U1's own calls with `A0`, from its load and unload among others.

### `M104` and `M109`

**What changes.** In a planned print, a temperature for a tool whose lane is not in its head is not
applied. The slicer sends these to heat the next tool early. Applied, they would change the
temperature of the color being printed.

- A printing temperature is kept for that tool's swap, which ends at it. The console says once per
  tool: `ace2k_u1: T2's temperature kept for its swap: head 1 prints a:1`. A value is a printing
  temperature when it is at most 40 °C below the file's temperature for the tool, or at least
  150 °C when the file gives none. A lower value, a standby or a cool-down, is dropped.
- A heater off (`S0`) for such a tool is held until the print ends. Then the head is turned off:
  `ace2k_u1: head 1: turned off at the print's end (T2's M104 S0 was held)`.
- The slicer's cool-down of the tool in the head (`M104 S70 T0`) is not applied when the very next
  `T` of the file swaps that head: the head is about to swap, not to rest. The console says
  `ace2k_u1: T0's cool-down kept: head 1 is about to swap`.

Without a kept value, a swap ends at the tool's printing temperature from the file: its first-layer
temperature on the first layer.

**What does not change.** A call without `T`, a call with `A0`, a call outside a planned print, a
call for a tool whose lane is in its head, and a call for a head that one lane feeds reach the U1
unchanged. A `T` that moves the print to another head leaves the old head to cool as the slicer
says.

### `SDCARD_PRINT_FILE` and `SDCARD_PRINT_FILE_WITH_PARAMETERS`

Before the U1 starts a file, the adapter checks it against the U1's tool table: the table the
start's `MAP_TABLE=` sets, or the one in place for `SDCARD_PRINT_FILE`.

**The planned file** (started from the page, its plan set and the file unchanged since): each shared
head the print uses takes the lane of its first tool, before anything heats. When the head's sensor
sees filament, the probe finds which lane is in it; the head's first `T` swaps it if needed. When
the sensor is empty, the U1's start load pushes the first tool's lane. The probe needs a cold head:
the adapter turns its heater off and waits until it is below `probe_max_temp`, with the head's
print fan at full speed meanwhile; the fan then gets its speed back. The console says
`ace2k_u1: head 1: probe waits for the head below 60 °C, its fan on`.

No probe runs when the lane in the head is known since its last load: the U1's load (from its
screen or the page's **Load into head N**) or a swap put it there, and since then no lane of the
head has moved (a follow aside), the head's sensor has not read empty, the U1 has run no other
flow on the head, and no other lane was named in it; and no lane of the head is moving at the
start. The console says `ace2k_u1: head 1: a:2 in it since its last load: no probe`. A Klipper restart
forgets this, and the next start probes.

**Refusals.** The start is refused before anything heats or moves. The U1's screen shows only its
general error; the console says why:

| When | Message |
|---|---|
| a file that puts two of its tools on a shared head, with no plan set (started from the U1's screen or Fluidd) | `ace2k_u1: this file shares heads between tools: start it from the ace2k page` |
| a file with no plan set, one of whose tools lands on a head with no filament and no lane | `ace2k_u1: T1 prints on head 2, which has no filament and no lane: start it from the ace2k page` |
| a file that uses a shared head, while a swap macro is missing | `ace2k_u1: ACEK_SWAP_LOAD is not defined: copy it from the release's config/ace2k-u1.cfg — a print that uses a shared head is refused` |
| a file with no plan set, on a shared head whose sensor is empty and whose lane is not known | `ace2k_u1: head 1: no lane is known for it: choose one with ACEK_LANE_ACTIVE` |
| the planned file, when the tool table sends a tool to another head than its lane feeds | `ace2k_u1: T2 goes to head 1 but its lane b:1 feeds head 2: start it from the ace2k page` |
| the probe of a head fails | `ace2k_u1: head 1: no lane of the head holds its filament` (or the probe's other reason) |

**What does not change.** These start as before:

- a file with one tool per head, or with several tools on a head that one lane feeds;
- a file the plan cannot read, such as one from another slicer;
- a start while a print is printing (the U1 refuses it itself).

With no plan set, a shared head the print uses whose lane is not known and whose sensor sees
filament is probed at the start, as for the planned file.

### `RESUME`

**What changes.** Before the U1's own `RESUME`, the adapter finishes what its pause left:

1. A swap that failed goes on. The adapter reads the head first:
   - the swap failed before the head was emptied: the unload and the free check run again, with no
     probe;
   - the head's sensor sees filament: the probe finds which lane is in it, on a cold head;
   - the head's sensor is empty: the swap goes on with the push of the new lane.
2. A spool that ran out in a shared head is loaded again: its new spool is pushed to the head and
   loaded
   ([When a spool runs out in a shared head](install.md#when-a-spool-runs-out-in-a-shared-head)).
3. The `T` the swap was for runs, then any `T` refused while the swap was pending.
4. The U1's `RESUME` runs.

The new lane's spool must be known first: its tag, its `ACEK_SPOOL` record, or the head's filament
setting. When it is not, or when it is another material than the file's tool, nothing moves and
the print stays paused. A line ending in `still paused` says what to do, for example
`ace2k_u1: head 1: a:2 has no spool set and the head no filament setting: the U1's RESUME would
refuse it — set it (Edit spool, or ACEK_SPOOL), then RESUME; still paused`. Each step that cannot
go on says so the same way. Fix it and `RESUME` again.

A spool can also run out in a shared head while a swap waits for its `RESUME`. That lane stays
stopped, and `RESUME` waits until you have unloaded the head and loaded it again by hand:
`ace2k_u1: head 1: a:2 ran out — RESUME waits for head 1: unload head 1, roll a:2 back out of its
bay, put a new spool in and load head 1 from the U1 screen; still paused`.

**What does not change.** With nothing pending, the U1's `RESUME` runs as before.

### `ACEK_LANE_ACTIVE` and `ACEK_FEED`

These are ace2k's commands.

- `ACEK_LANE_ACTIVE` runs as ace2k's own. Then each shared head takes the lane ace2k chose for its
  extruder. During a planned print it is refused for a lane of a shared head:
  `ace2k_u1: head 1: ACEK_LANE_ACTIVE refused: the planned print's swaps choose the head's lane
  until it ends`.
- `ACEK_FEED` of a lane waiting behind its splitter is refused while the head's sensor sees
  filament: the push would meet the other lane's filament in the shared tube.
  `ace2k_u1: head 1 holds another lane's filament: unload it first`.

## The U1's channel lock

The U1's feed module drives two heads, one per channel, and lets one channel at a time run a
flow (a preload, a load, an unload). The adapter takes the same lock for the preload it runs and
for the probe before the U1's unload or load of a head whose lane is not known. It waits at most
300 s for the lock while the other channel's flow holds it. That is longer than any normal flow
of the U1, heating included, so the wait only ends a lock that is never released. When the 300 s
pass:

- a preload is given up: nothing is pushed, the filament stays in the bay, and the console says
  `ace2k_u1: lane 1: the U1 kept its channel busy for 300 s; the preload given up`. Pull the
  filament out and push it in again once the U1's other flow has ended.
- a probe before the U1's unload or load is given up, and so is the unload or the load: the
  screen shows its failure, and the console says
  `ace2k_u1: head 1: the U1 kept its channel busy for 300 s; the unload given up` (`the load given
  up` for a load). Run it again once the U1's other flow has ended.

A preload on a shared head with no lane in it, which pushes nothing, waits the same 300 s; then it
leaves the channel as it is, with a line in the log only.

## Macros

`config/ace2k-u1.cfg` carries two macros, `ACEK_SWAP_UNLOAD` and `ACEK_SWAP_LOAD`. The adapter
runs them when a shared head changes its lane during a print; do not run them by hand
([The lane swap's macros](configuration.md#the-lane-swaps-macros)). Its other sections are
`[ace2k_u1]` (the adapter) and `[ace2k_u1_history]` (the web page's recorder).

Older releases carried `ACE_EJECT` (now `ACEK_EJECT`) as a macro, and the burn-in's
`_ACE_ONE_TO_ONE`, `ACE_TO_HEAD`, `ACE_FROM_HEAD` and the `_PRINT_START_ACE_LANES`,
`_PRINT_END_ACE_LANES`, `_CANCEL_PRINT_ACE_LANES` hooks. When you update, delete them from the
printer's copy ([Copy](install.md#copy) and [Configure](install.md#configure)).

If you add a macro to the file, its name must contain no digit: Klipper reads a digit as the start
of a parameter.

## The web page

`http://<printer>/ace2k/` (install it as described under
[The web page](install.md#the-web-page)). It is a static page. It talks only to Moonraker, and it
sends the same G-codes as the console. It is in English, and works on a phone and on a desktop.
Its nginx snippet sends two headers for the page only, not for Fluidd: `Cache-Control: no-cache`,
so that after an update the browser checks each file and never mixes old and new ones, and a
`Content-Security-Policy` that lets the page run only its own files and talk only to its own
printer. That policy needs a browser from 2022 or later: Safari 15.4 or later (iOS 15.4), or a
current Chrome, Edge or Firefox. An older browser blocks the page's link to the printer.

With several units the page shows every unit: the Units tab has one box per unit, with its name, its
link and health lights, the dryer's state while it dries, and its four bays. Every ACEK G-code the
page sends carries `UNIT=<name>` of the unit it acts on when that unit is named, except Stop all's
plain `ACEK_STOP` (every lane of every unit).

The page reads which head each lane feeds from the adapter's status (`ace2k_u1`: `heads`, per head
its unit and lane), so the head's sensor, the filament setting it shows and Edit spool are that
head's, whatever the order of `lanes:`. While the adapter is hooked the map is the heads it hooked
(a head whose channel is missing feeds from no lane). While it is not hooked the map is the one
`lanes:` configures, so a lane the print uses stays locked either way; the status then says
`hooked: false`, and Edit spool is disabled ("the adapter is not hooked").

The history records the console lines and the chamber and heater samples of every unit. An event
is filed under a lane, or under the dryer, only when it is the first unit's: its own line
(`ace2k a: lane 3 …`) or a lane named with that unit (`lane a:3`). Another unit's lines are filed
under the unit.

**Tabs.** The page has four tabs: Units, Print, Dryer and History. The top bar stays on every tab.
[The web page](install.md#the-web-page) describes each tab. This section lists the G-codes behind
the controls.

**Top bar.** It shows the ace2k version, the theme button (system, light, dark; the browser keeps
the choice), **Stop all**, a status pill and the health dot. The dot is green, amber when a unit's
self-test fails, or red when a link is down. Click it to open the status panel: the health lights
(link and checks, in words), the adapter's version and the link's details, and
`The history is not being recorded: <why>` when the recorder failed. During a print the pill says
`Printing 40 %`, and `Paused` with the reason while it is paused. Click the pill to open the Print
tab.

**Print tab.** It plans a file from the printer (`ACEK_PLAN`): the file's tools, the spool for each,
each head's lane swaps and the start options. It is offered when a head is fed by several lanes.
When the page plans the file again, your choices stay while the new plan still offers them for the
same color and material; a message names any that went back to the suggestion.
Click a color to choose its bay. Every bay is offered, an empty one too, but not a spool of
another material. The bay you choose is the color's until you change it: emptied, refilled with
another spool, its spool moved elsewhere, or the file planned again, the color stays there (only
a bay the new plan no longer lists, its unit gone, is dropped, with the message).
**Print** says what the bay lacks: `No spool for color 2: put one in a:2 or pick a bay.`, `… define
a:2 first.` for a spool with no material set, or `a:2 holds PETG and color 2 is PLA: …`. A spool of
the same material in another color prints: its row names the color in amber, and a message says
`a:2 now holds black for color 2` once for each new spool. An empty bay you choose gets a step to put a new spool
there. **put a new … spool — the planner picks the bay** lets the planner choose the bay; the
color's row then reads `new spool → a:2`.
**Print** sends `ACEK_PLAN_SET`, then `SDCARD_PRINT_FILE_WITH_PARAMETERS` with the tool table and
the options, as the U1's screen does
([Printing more colors than heads](install.md#printing-more-colors-than-heads)).
During a print the tab shows the printing view instead, with **Pause** (`PAUSE`), **Resume**
(`RESUME`, while the print is paused) and **Stop** (`CANCEL_PRINT`, after a confirmation).

**Stop all** stops every lane (`ACEK_STOP`), after a confirmation. It sends no `UNIT=`, so with
several units it stops every lane of every unit, and then says so: the button reads
`Stop every unit's lanes` and asks `Stop every lane of every unit?`. During a print it stops only
the page's unit's lanes the print is not using (`ACEK_STOP LANE=n` for each, with `UNIT=` when the
unit is named), and it is disabled when there are none. Stop all stops only that unit's lanes, so it
cannot tell which of the other units' lanes the print uses, and it leaves them running: the button
reads `Stop idle lanes of unit a` (`Stop idle lanes` for an unnamed unit), and with several units
the confirmation adds `The other units' lanes are not stopped.` What Stop all sends is decided
when you press it, and sent as the confirmation said once you confirm. When a print starts or ends
while the confirmation is open, or one of the lanes it would stop goes into use (a tool change),
nothing is sent and the page asks you to press Stop all again.

**Banner.** A banner at the top says `disconnected — retrying` when the page cannot reach
Moonraker. When Klipper is not ready it says what Klipper is doing: `Klipper is starting`,
`Klipper is shut down`, `Klipper has an error` or `Klipper is not connected`. When a call of the
page itself fails, it says `The page could not read the printer: <why>. Trying again…`, and the
page tries again. `?demo` on the installed page says `Demo data is not installed on the printer.`
A screen reader reads the banner when it appears.

> [!CAUTION]
> **The humidity mode turns a mains heater on by itself, with nobody there.** The unit starts a
> drying cycle whenever its box stays humid, even with the printer off. The mode runs only with
> `unattended_drying: True`. Set it only with the unit in a ventilated place, with nothing that
> burns next to it, and nothing on the lid or over the vents. Read the dryer warnings in
> ace2k's [README](https://github.com/tobecwb/ace2k#read-this-first) first.

**Dryer tab.** It shows the dryer's state (Idle, Starting, Heating, Cooling down or Fault) and a
summary, with a tab per unit when there are several units. It also sets the mode (manual, humidity
or during a print).
- Idle: presets (PLA 45 °C 6 h, PETG 60 °C 6 h, ABS 65 °C 6 h, TPU 60 °C 6 h), an editable
  temperature, one **Time (h:mm)** field, and **Start drying** (`ACEK_DRY`). The note under the
  form gives the limits: "15 to 65 °C, 0:01 to 24:00." A value outside them shows "Temperature
  from 15 to 65 °C." or "Time as h:mm, from 0:01 to 24:00.", and **Start drying** stays off.
- Starting or heating: a ring with the chamber's temperature, its humidity and the target, and a
  strip "♨ Drying · 1:20 / 6:00" (or the time left), with **Stop drying** (`ACEK_DRY_STOP`).
  After a stop, the fans run until the heater has cooled.
- Cooldown: the fans run until both heater outlets are at most 45 °C and have stopped cooling.
  This takes at most 10 min. Nothing heats.
- Fault: the reason in words, "The fans cool it down. The unit accepts Clear once it is cool.",
  and **Clear fault** (`ACEK_DRY_CLEAR`). A thermal-cutout fault clears only with a power cycle.
- The **Advanced** fold, in every state: the two heater outlets, the **Heater aim** while it heats
  (about 7 °C above the target: the unit drives the hotter outlet toward it), the fans, the flaps,
  and **Dryer log** (`ACEK_DRYER_LOG`). While the dryer is idle it also has **Fans on** for 60 s
  and **Fans off** (`ACEK_FAN`), and **Open / Close** for the bottom and rear exhaust flaps
  (`ACEK_FLAP`).

**Units tab.** It draws the printer's heads, each unit's bays and a rail from every bay to its head.
Each bay shows its spool's color and type and a state label (Following, Loaded, In bay, Moving,
Empty, Parked, Blocked, In head?, or the error in words, such as Stuck or Tangled). A lane of a
head several lanes feed shows the merge icon (two lines that join into one) and the head's short
name, for example `H1`. Click a bay to open its card in the side panel, and a head or a unit to
open its card too.

**The bay card.** Lane n → head n. Its title says `Head 1 · shared with 2, 3` for a shared head
(`a:2` with several units). It shows:

- the spool (vendor, type, subtype, from the head's setting on the U1; `(tag)` when it came from
  the tag);
- the encoder's travel;
- the tag's state, in words;
- the buffer (`at rest`, `pushed`, `taut`);
- the last move in words, for example `the motor stalled (assisting) at 12.0 mm`;
- whether feed-forward is on.

Below the buttons, a note says why a button is disabled (`not connected`, `the adapter is not
hooked`, `this lane feeds no head`, `in use by the print`, `not while a print is printing or
paused`, `head 2 already holds filament: unload it first`, `Pause the print to change a spool`),
once for each reason.

The actions shown depend on the lane's state:
- **Load** (`ACEK_LOAD LANE=n WAIT=0`) — a filament in the bay, lane idle;
- **Eject** (`ACEK_EJECT LANE=n WAIT=0`) — a filament in the bay, after a confirmation;
- **Stop** (`ACEK_STOP LANE=n`) — the lane is moving;
- **Clear** (`ACEK_CLEAR LANE=n`) — the lane is in error;
- **Read tag** (`ACEK_RFID_FORGET LANE=n`) — a tag was read or none was found: the lane's tag goes
  back to pending and the unit reads it on the lane's next move, the same tag included;
- **Load into head N** — loads the lane into its head, as the U1 screen's **Load** does. This
  button heats the head and pushes the filament to the nozzle. It is not **Load**, which stops the
  filament at its waiting point, before the head. The button asks first, for example `Load lane
  a:1 into head 1? Head 1 heats to load it, and the lane's filament is pushed to the nozzle.` Then
  it sends these lines, one at a time:
  1. On a head that several lanes feed: `ACEK_LANE_ACTIVE LANE=n` (with `UNIT=`). This line makes
     the lane the head's lane, and the question says so. When the line is refused, nothing else is
     sent.
  2. [`ACEK_HEAD_PREPARE LANE=n`](#acek_head_prepare) (with `UNIT=`). A lane whose material is not
     set yet is pushed to the head's sensor, and its tag is read on the way. When its material
     stays unset, the lane is rolled back and nothing else is sent.
  3. [`ACEK_HEAD_SPOOL LANE=n`](#acek_head_spool) (with `UNIT=`). The head takes the lane's spool,
     as a swap writes it: the lane's tag in the U1's names, or else its Edit spool record. It comes
     before the load because the U1 takes the load temperature from the head's setting when the
     load starts. For a lane with neither, nothing is written, the head keeps its setting, and the
     events show the warning `ace2k_u1: head 1: a:2 has no spool set: set it with Edit spool`.
     When the setting is refused (for example a record with a value the U1 cannot take: fix it
     with Edit spool), nothing else is sent. If the load fails later, or `START` is refused, the
     head keeps the spool it was given.
  4. `AUTO_FEEDING_BATCH ACTION=START`. When it is refused, nothing else is sent.
  5. `AUTO_FEEDING_BATCH ACTION=DOING EXTRUDER=<head − 1> LOAD=1`. The U1 heats the head and loads
     it. The line ends when the load ends, about 90 s later.
  6. `AUTO_FEEDING_BATCH ACTION=STOP`, also when line 5 failed. Outside a print it turns the
     heaters of all four heads off, as the screen does. Without it the macro stays in its batch,
     and the heads stay at the load temperature.

  The button is offered for a lane with a spool in its bay, idle, that feeds a head, when the page
  reads that head's filament sensor and the macro's state (`gcode_macro AUTO_FEEDING_BATCH`). It
  is disabled:
  - while the adapter is not hooked;
  - while a print is printing or paused;
  - when the head's sensor sees filament (`head 2 already holds filament: unload it first`);
  - while a head load runs, from the screen or from a page (the macro's `doing` is true: `the
    printer is already loading a head`).

  A lost answer can leave the macro's batch open with nothing loading. The page then disables the
  button on every lane with that note. To close the batch, send `AUTO_FEEDING_BATCH ACTION=STOP` in
  the console, or load a head once from the screen.

  The U1 runs one head load at a time. So while the page runs one, the button reads `sending…` on
  every lane. After you answer the question, the page waits 0.3 s for the latest status, checks the
  lane again, and sends nothing when something changed meanwhile. A refusal from the printer shows
  at the bottom of the page and in the events, as for any other button.

  When the page loses its connection to the printer during the load, the load goes on in Klipper,
  but line 4 cannot be sent. The page does not send it later. The events say `The head load's stop
  did not reach the printer. Its heaters turn off on their own after 5 idle minutes.` The U1 turns
  its heaters off after 5 minutes without activity (its idle timeout). The macro's batch stays
  open: close it as said above;
- **Edit spool** — a lane whose tag was not read: type (the U1's filament table), vendor and color
  set the filament setting of the head the lane feeds on the U1 (`SET_PRINT_FILAMENT_CONFIG`); the
  U1 resets it when the bay empties. The form opens under the lane card, and status updates do not
  redraw its fields, so a field you edit or an open color picker stays as it is. A lane that feeds
  no head (not in `lanes:`) has Edit spool disabled, and so does every lane while the adapter is not
  hooked. Save writes the head the form was opened for; if the lane no longer feeds that head by
  then, nothing is written and the page says "The lane's head changed; reopen the form.";
- **Advanced** fold (its own row, in the same place open or closed): **Feed** and **Rollback** by a
  length of 1–2000 mm at 20 mm/s (`ACEK_FEED`, `ACEK_ROLLBACK`, lane idle with a filament),
  **Feed-forward on / off** (`ACEK_FEED_FORWARD`), and **Forget tag** for a lane whose tag was read
  (`ACEK_TAG_FORGET`): the tag is forgotten and not read again for the adapter until that spool
  leaves its bay, so Edit spool can set the color and the type without the tag putting them
  back. The forget goes when that spool is taken out by hand or ejected (not when the adapter's
  own roll back empties the bay). **Read tag again** is the other way, and stays offered on a
  forgotten lane: it undoes the forget and the unit reads the tag on the lane's next move.
- **Shared heads.** Under the bays, a head that several lanes feed has its own line, with the
  merge icon. It says which lane is in the head, for example "Head 1: lane a:2". After a restart
  the adapter does not know that, and the line says "Head 1: lane unknown". Then every lane of
  that head that holds a spool shows **In head?** instead of Loaded, because the head holds
  filament and the page cannot tell whose it is. On that line, **Find the head's lane**
  (`ACEK_HEAD_PROBE HEAD=n`) finds the lane. It asks first, and it is disabled while a print is
  printing or paused. It runs only on a cold head: on a warm one the console says to let it cool.
  When the lane is known, the button goes away. The adapter keeps the head's lane after the
  screen unloads the head. So when the head's sensor reads no filament, the line names the lane
  as empty, for example "Head 1: lane a:2, empty". With no lane named and the head's sensor reading
  no filament, the line says "Head 1: empty". With no lane named and no reading from the head's
  sensor, the head has no line.
- A lane in its bay whose last push toward the head ended blocked shows **Blocked** on its card
  and in the unit row until its next move (a parked lane shows Parked).

During a print, Load, Eject, Stop, Feed, Rollback and Feed-forward are disabled on a lane the print
uses. To stop that lane, pause the print on the U1. While the print is printing (not paused), Edit
spool, Read tag and Forget tag are disabled on every lane: pause the print to change a spool. Clear
stays available. Load into head N is disabled on every lane. The state is checked again when you
click a button.

**History tab: chart.** It shows the chamber's temperature and humidity, the two heater outlets and the dryer's
target, over 1, 6 or 24 hours. The legend buttons hide a series. While the dryer is starting or
heating, the chamber's legend shows its latest reading beside the target, for example
`Chamber 27.0 / 45.0 °C`.
A tap or a hover shows the time and every value. With several units, a button per unit chooses
whose samples are drawn.

**History tab: events.** It lists the console's ace2k lines and the dryer's and lanes' transitions in words,
newest first, for example `lane 3: tag read` or `dryer: Heating → Cooling down`. It has filters
(All, Errors, Lane 1–4, Dryer, and with several units one per unit) and **Show more…**.

`[ace2k_u1_history]` records the history behind the chart and the events: a sample every 30 s,
seven days kept, under `printer_data/logs/ace2k/`.
