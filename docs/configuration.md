# Configuration

The adapter reads two sections from `ace2k-u1.cfg`, in
`/home/lava/printer_data/config/extended/klipper/` on the U1. The file works as copied: every
setting is commented out at its default.

To change a setting:

1. Remove the `#` in front of it.
2. Set the value, and save the file.
3. Restart Klipper (`/etc/init.d/S60klipper restart`).

There is no G-code to change these settings at run time.

ace2k's own settings, in `ace2k.cfg` (`[ace2k]`), are described in ace2k's
[`features.md`](https://github.com/tobecwb/ace2k/blob/main/docs/features.md#configuration).

## `[ace2k_u1]` — the adapter

| Setting | Default | Range | What it does |
|---|---|---|---|
| `lanes` | `1, 2, 3, 4` | one unit: lanes 1–4; several units: `<unit>:<lane>` entries; lanes joined by `+` feed one head | The lanes wired to a head. With one unit, lane n always feeds head n, and a lane left out is not hooked: its head stays the U1's own. A lane left out feeds no head, so the adapter keeps ace2k's follow off it: it never pushes its filament when its plunger moves, and ace2k's `ACEK_STATUS` says `follow=off (held: not in [ace2k_u1] lanes)` (with ace2k host interface 21 or later; before, `follow=` is not shown). A follow it kept from before a restart is stopped once the adapter hooks. With several units, each entry names a unit and a lane, and its place in the list is its head (`-` leaves that head the U1's own) ([Several units](#several-units)). Lanes joined by `+` feed one head through a splitter: `a:1+a:2, a:3, a:4` ([Several spools in one head](#several-spools-in-one-head)). |
| `feed_speed` | `70` | mm/s; the unit's own bounds, 9–80, apply | The speed of the push toward a head, and of the follow. ace2k arms the follow at Klipper's start at this speed too. |
| `head_budget_mm` | `2000` | mm, above 0 | The longest push toward a head before the head's filament sensor must have seen the filament. Set it above the longest path from a bay to its head: two splitters in a row can make it longer than 2 000 mm (use `3000` there). A push longer than the unit's longest move (2 000 mm) runs in several moves, and stops when one ends short without having carried the filament. The limit stays: a tube come loose from a splitter wastes at most this much filament, once. |
| `poll_ms` | `200` | 50–1000 ms | How often the adapter watches the bays. |
| `tag_push_speed` | `70` | mm/s, 0 or the unit's 9–80 | A spool you put in a bay whose head only that lane feeds. The unit pulls the filament to its parking point, searches further for the spool's tag, and comes back to the parking point. When the U1 loads the head meanwhile and the tag is not read yet, the adapter stops that search and pushes on from where the filament is, with no return. Up to the search's reach (750 mm from the bay, ace2k's `rfid_search_mm`) the push runs at this speed, so the tag can still be read on the way; then, or once the tag is read, at `feed_speed`. At or above `feed_speed` the whole push runs at `feed_speed`. Reads on the move held at every speed up to 70 mm/s (bench 2026-10-24: Bambu Lab, Anycubic and Snapmaker tags, three times each at 30, 40, 50, 60 and 70 mm/s). `0`: the search runs and comes back first, as before. A value above 0 and under 9 stops Klipper at the start with a config error. A head that several lanes feed never cuts the search. A filament that waited behind an old piece in the head is not cut either: the unit's load pulls it from the bay to the head's sensor in one go. That pull starts at 30 mm/s (or ace2k's `load_speed`, or this speed, when lower), so the drive gear catches the tip, and keeps that speed until the tag is read or the search's reach is behind, then runs at `feed_speed`. If that pull ends by itself short of the head with the tag not read yet, the push that goes on runs at this speed for the rest of the search's reach. That pull counts against `head_budget_mm`. |
| `retry_push_mm` | `10` | 1–40 mm | When the U1's load finds that the filament did not extrude, the U1 would pulse its own side motor to push the tip into the extruder's gears. Here that motor is out of the filament's path. So instead, the adapter pauses the lane's follow, feeds this far, and arms the follow again. |
| `apply_tags` | `True` | `True` / `False` | A spool's tag sets its head's filament setting on the U1 ([How a spool's tag sets the head's filament](install.md#how-a-spools-tag-sets-the-heads-filament)). `False` turns that off; loading and feeding are unaffected. |
| `runout_source` | `head` | `head` / `unit` | What pauses the print when a spool runs out at the bay. `head`: the head's own sensor, when the end of the filament reaches it. `unit`: the adapter, as soon as the bay's sensor sees the end; the head is unloaded, the rest rolls back out of the bay, and `RESUME` loads the new spool, as in a shared head ([What pauses the print](install.md#what-pauses-the-print)). A head that several lanes feed does not use it: the print pauses as soon as the bay's sensor sees the end ([When a spool runs out in a shared head](install.md#when-a-spool-runs-out-in-a-shared-head)). |
| `grip_mm` | the unit's own, `40` | 10–45 mm | How far the unit pulls a filament you insert while its head still holds a piece that does not belong to this lane. It pulls just until the gear holds the filament. On a head that one lane feeds, a unit with ace2k v0.13.0 or later pulls nothing: the filament waits in the bay for the U1's load ([The wait](install.md#the-wait)). |
| `eject_probe_mm` | `80` | 20–150 mm | When the head's sensor sees filament, `ACEK_EJECT` first rolls the filament back up to this far. It stops sooner if the lane's buffer plunger is pulled tight. This tells whether the head holds it. Keep it longer than the slack between the unit and the head, which measured about 50 mm. A value below 20 is raised to 20, with a warning when Klipper starts ([Taking a filament out of its bay](install.md#taking-a-filament-out-of-its-bay)). |
| `eject_probe_speed` | `10` | 5–30 mm/s, within the unit's own speeds (9–80) | That probe's speed. A value below the unit's floor is refused when the adapter hooks ([Speeds and lengths the unit refuses](#speeds-and-lengths-the-unit-refuses)). |
| `eject_settle_ms` | `500` | 200–2000 ms | After the probe, how long to wait for the lane's buffer plunger. Back at rest: the filament is free, and the unit unloads it. Moved: the head holds the filament, and the U1 unloads the head first. |
| `temp_ranges` | `PLA:190-230, PETG:220-260, ABS:230-270, ASA:230-270, TPU:210-240` | `<material>:<low>-<high>` entries, separated by commas; 150–350 °C, low below high; empty for none | The temperature range of each material, in °C. A head set outside the range of its lane's material gets a warning on the console and among the page's events, as `head 1: 260 °C is above PLA's 190–230 °C (lane a:2)`. The head still heats; nothing is refused. The check runs when a print starts, for each tool of the file and the lane it prints with. Outside a print it also runs for `M104`, `M109` and the temperature set on the U1's screen or in Fluidd. The U1's own heating during its load, unload and manual feed is not checked. A temperature below 150 °C (a standby or a warm-up) and a material that is not listed never warn. A material is compared as in `unload_temps`; a material that is not listed takes the range of its first word, so `PLA Matte` uses `PLA`. |

The settings below are used only by a head that several lanes feed
([Several spools in one head](#several-spools-in-one-head)).

| Setting | Default | Range | What it does |
|---|---|---|---|
| `head1_splitter_mm` … `head4_splitter_mm` | `1000` | mm, above 0, up to 2000; only on a head several lanes feed | How far the old lane rolls back at a lane swap, so that its tip is behind the head's splitter. Measure it with [`ACEK_SPLITTER_MEASURE`](commands.md#acek_splitter_measure), or with a tape ([How far the old lane rolls back](#how-far-the-old-lane-rolls-back)). Set on a head with one lane or none, it stops Klipper. |
| `swap_speed` | `80`, or the unit's top speed when lower (70 on an older ace2k image), for each unit's moves | mm/s, within the unit's own speeds (9–80); set, it is checked with a head that several lanes feed, and otherwise clamped to the unit's speeds with a warning | The speed of a swap's roll back of the old lane, and of its push of the new lane to the head. |
| `background_heads` | empty: no head | head numbers 1–4, separated by commas; only heads several lanes feed | The heads that swap their lane while parked in the dock, while another head prints. When that head's T comes, it is ready, or its swap goes on from where it stopped. The new filament's first pushes and its purge come out in the dock. So list a head only when its dock carries the adapted nozzle wiper, with an opening under the parked nozzle, and a larger waste bin sits under the docks. What comes out then falls through into the bin. When the head is picked, only the U1's cut and clean are left. The adapter cannot check the dock: listing a head says that its dock has both. A head out of 1–4, listed twice, or fed by one lane or none, stops Klipper. **Not recommended as it is.** Use it only on a head whose dock has been modified for it: an adapted nozzle wiper with an opening under the parked nozzle, and a larger waste bin. Even then, the chance that oozed plastic or a strand soils the print, or that colours mix in it, is high. |
| `background_purge_mm` | `10`, the load macro's own purge | 0–100 mm; 0: the purge runs at the cleaning area | The purge of a background swap, in the dock. It is pushed past the new filament's 40 mm through the gear, in the same move, at the new filament's printing temperature. What comes out falls through the dock's opening. The head then arrives ready to print: its T heats it again if it cooled, cuts and cleans. With 0, the T runs the load's purge and clean at the cleaning area (`ACEK_SWAP_LOAD GRIP=0`). A value out of 0–100 stops Klipper. |
| `arrival_wipe` | `True` | `True` / `False` | At the end of every swap, inline or in the background, and of every refill, the U1's own pre-extrusion cuts, wipes the nozzle on the rubber pads, drops the purge in the bin and goes forward to the head's idle position. With `True` the nozzle is wiped on the pads once more, with the U1's own wipe (`INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=4`), after the drop in the bin and just before the idle position (`MOVE_TO_XY_IDLE_POSITION_EXTRUDER`). The U1's pre-extrusion itself runs as always. The same key covers a head in `background_heads` that a `T` of the file takes from its dock with no swap (the lane it needs is in it already): while printing, after the U1's `T`, the head goes to the cleaning area (`MOVE_TO_DISCARD_FILAMENT_POSITION`), is wiped once with the same wipe and goes to its idle position (`MOVE_TO_XY_IDLE_POSITION_EXTRUDER`). Its dock has an opening, so a parked head may ooze. Not in the print's start (layer 0), which cleans the nozzle itself. `False` turns both extra wipes off. A value that is not a boolean stops Klipper. |
| `probe_mm` | `100` | 60–300 mm | How far the probe rolls each lane back to find which lane is in a head. The swap's free check rolls back up to this far too. Keep it longer than the slack between the unit and the head (about 50 mm). A held lane takes up that slack with its plunger at rest, so a shorter value reads it as free. |
| `probe_speed` | `50` | mm/s, within the unit's own speeds (9–80), checked only with a head that several lanes feed | The speed of every probe move, the swap's free check among them. |
| `probe_max_temp` | `60` | 30–150 °C | The probe of a head whose sensor sees filament runs only on a cold head: the cold gear holds the filament, and that is what the probe reads. The adapter turns the heater off and waits until the head is below this temperature, with the head's print fan at full speed. It gives up after 15 minutes. |
| `unload_temps` | `PLA:220` | `<material>:<°C>` entries, separated by commas; 150–350 °C | The temperature at which a swap forms the old filament's tip, per material: `PLA:220, PETG:240`. A material not listed uses the U1's own unload temperature for it. A material is compared in upper case, as one word: only letters, digits, `-`, `+` and `_` count, so `PLA SILK` and `PLASILK` are the same entry. |
| `colour_max_distance` | `25` | above 0 | `ACEK_PLAN` suggests a spool for each color of a file. This is the farthest a spool's color may be from the file's color. It is measured as the eye sees colors (CIE76 ΔE, where about 2.3 is the smallest difference you can see). When no spool is that close, none is suggested and you choose one. The web page's own planner does not read this key: it always uses 25. |
| `colour_min_margin` | `8` | 0 or more | How much closer the best spool must be than the second best. When two spools are about as close (two black spools), `ACEK_PLAN` suggests none and you choose one. The web page's own planner does not read this key. |

### Speeds and lengths the unit refuses

The unit's firmware has its own limits for a move: a speed of 9–80 mm/s, and at most 2000 mm in
one move (ace2k reads them from the unit when it connects). When the adapter hooks, it checks
`eject_probe_speed` against the speeds of each unit in use. With a head that several lanes feed,
it also checks `probe_speed`, and `swap_speed` when it is set. Without such a head nothing uses
`probe_speed`, and it is not checked; a `swap_speed` that is set and outside a unit's speeds (a
push to a head that a lane blocks rolls back at it) does not stop the hook: the console says
`ace2k_u1: swap_speed 90 is outside the unit's 9..80 mm/s: its moves run at 80 mm/s`, and that
unit's moves run within its speeds. A `swap_speed` left out is not checked: each unit's moves run
at 80 mm/s, or at the unit's top speed when it is lower, and the console says so once for such a
unit: `ace2k_u1: swap_speed defaults to the unit's top speed, 70 mm/s` (an older ace2k image), or
`ace2k_u1: the unit reports no top speed: swap_speed is 70 mm/s` (an image that gives no speeds).
A speed outside the unit's limits would make the unit refuse every such
move, so nothing is hooked, and the console says why, as for `grip_mm`:

- `ace2k_u1: not hooked: eject_probe_speed 5 is outside the unit's 9..80 mm/s`;
- `ace2k_u1: not hooked: swap_speed 90 is outside the unit's 9..80 mm/s` (the same for
  `probe_speed`).

With several units, each line names its unit (`ace2k b: swap_speed 90 …`). Set the value within
the limits and restart Klipper. An older unit image that does not give its speeds is checked
against 70 mm/s only: `swap_speed 75 is above 70 mm/s`.

`probe_mm` is checked when Klipper starts: above 300 mm, the adapter's own limit, Klipper stops
with its config error. With a head that several lanes feed, the hook also checks it against the
unit's longest move. With ace2k's images that move is 2000 mm, so the check matters only for a
unit whose longest move is shorter than 300 mm:
`ace2k_u1: not hooked: probe_mm 250 is longer than the unit's longest move, 200 mm`.

At Klipper's start, ace2k turns the follow on for every lane with filament. That is its
`follow_default` key in `[ace2k]`, on by default. With `follow_default: False`, nothing is armed at
the start: the adapter then arms a lane only when the U1 loads, extrudes or unloads it, and a head
the U1 still holds loaded at the start
([How the follow turns itself back on](install.md#how-the-follow-turns-itself-back-on)).

### Several units

The adapter works with the units ace2k drives
([Several units in ace2k's `features.md`](https://github.com/tobecwb/ace2k/blob/main/docs/features.md#several-units)).
Each unit is a section `[ace2k <name>]` with its own `mcu:`. The adapter needs ace2k's host
interface 12 (`API_MIN` 12); an older ace2k is refused with `host interface older than 12`.

`lanes` says which lane of which unit feeds each head. There are two forms, and they do not mix.

- **One unit: the bare form.** Lane n feeds head n. A lane left out keeps its head the U1's own.
  This is the default. `lanes: 2, 4` hooks only lanes 2 and 4.

      lanes: 1, 2, 3, 4

- **Several units: `<unit>:<lane>`.** Every entry names the unit by its section name. The entries
  are placed on heads 1–4 by their position in the list. Two units, two lanes each, one lane per
  head:

      lanes: a:1, a:2, b:1, b:2

  Here head 1 is fed by lane 1 of unit `a`, head 2 by lane 2 of `a`, head 3 by lane 1 of `b`, and
  head 4 by lane 2 of `b`. A list shorter than four leaves the last heads the U1's own. Names
  compare case-insensitively.

  To leave a head in the middle the U1's own, put `-` in its place. `-` only holds the place:

      lanes: a:1, a:2, -, b:1

  Heads 1 and 2 are fed by lanes 1 and 2 of `a`, head 3 stays the U1's own, and head 4 is fed by
  lane 1 of `b`. `-` is valid only with `<unit>:<lane>` entries. In the bare form lane n always
  feeds head n, so leave the lane out instead; a `-` there stops Klipper. A list of only `-` hooks
  nothing.

Klipper does not start, and says why, when:

| What is wrong | Message |
|---|---|
| bare and `<unit>:<lane>` entries mixed | `ace2k_u1: lanes: every entry <unit>:<lane>, or every one bare` |
| more than four entries (`-` counts) | `ace2k_u1: lanes: more than 4 heads` |
| a bare entry, with several units | `ace2k_u1: lanes: needs <unit>:<lane> entries: this printer has several units (a, b)` |
| an entry that is not a number, has no number after the colon, or no unit before it (`:2`) | `ace2k_u1: lanes: 'x' is not <unit>:<lane>` (the entry as written) |
| `-` among bare lanes | `ace2k_u1: lanes: '-' needs <unit>:<lane> entries; with bare lanes leave the lane out` |
| a lane outside 1–4 | `ace2k_u1: lanes must be within 1..4` |
| a unit:lane on two heads | `ace2k_u1: lanes: a:1 feeds two heads` |
| the same bare lane twice | `ace2k_u1: lanes: 1 feeds two heads` |
| a unit the config does not declare | `ace2k_u1: lanes: no unit c; units: a, b` |
| a `+` with no lane, or a `-`, beside it (`a:1+`, `a:1+-`) | `ace2k_u1: lanes: 'a:1+' is not <unit>:<lane>` (the entry as written) |
| a lane of a `+` group whose `laneN_extruder` key in ace2k's section names another extruder than its head's | `ace2k_u1: lanes: a:2 is in head 1's group but [ace2k a] lane2_extruder is extruder1` |

The default `lanes` is the bare form, so with several units the setting must be changed: the
default stops Klipper with the third message above.

With several units the console names the lane as `b:1` in the adapter's lines and refusals, and a
hint to the operator carries the unit: `ACEK_CLEAR LANE=1 UNIT=b`.

**A head's lane that shares its extruder is not followed until it is chosen.** When ace2k maps a
head's lane and another lane, of any unit, to one extruder (a `laneN_extruder` key), only the
lane chosen with `ACEK_LANE_ACTIVE` follows. A load of that head from the U1's screen arms
nothing on a lane not chosen. klippy.log says it once,
`ace2k_u1: lane a:4 shares e3 and is not its active lane: no follow`, and nothing reaches the U1.
`ACEK_LANE_ACTIVE UNIT=a LANE=4` then arms it, and the adapter takes that follow as its own.

A load of such a head, from the U1's screen or a load G-code, needs the filament already at the
head's sensor. With the sensor empty the adapter refuses the load and pushes nothing, because it
cannot pick which lane's filament the head should get. The U1 shows its general load failure,
and the console says why:
`ace2k_u1: e3 is shared by lanes a:4, b:1: load refused with its sensor empty — put the filament
in the head by hand, then load`. With filament at the sensor, the load goes on as usual.

Inserting a filament in the bay of such a lane starts the U1's automatic preload. With the head's
sensor empty the adapter pushes nothing toward the head, and the U1's preload ends as a normal
one, with no error on its screen. ace2k parks the filament in the unit as usual (its automatic
load). klippy.log says it once:
`ace2k_u1: lane a:4: e3 is shared by lanes a:4, b:1: the preload pushes nothing into it with its
sensor empty`. A head whose extruder only its own lane feeds loads and preloads as before.

**The web page shows every unit.** It has one row per unit, with its name, its link and health
and its four bays, a dryer tab per unit and a chart button per unit. When a unit is named, the page
adds `UNIT=<name>` to every ACEK G-code it sends to that unit, except Stop all's plain
`ACEK_STOP`, which stops every lane of every unit. During a print, Stop all stops only the first
unit's idle lanes. The history records the console lines of every unit
(`ace2k:`, and `ace2k <name>:` where the name is a unit the printer declares; `ace2k dryer:` is no
unit's line and is not recorded, as with one unit) and the chamber and heater samples of every
unit. A line is filed under a lane, or under the dryer, only when it is the first unit's
(`ace2k a: lane 3 …`, `ace2k a: dryer: …`, or `lane a:3` in the adapter's lines). Another unit's
lines are filed under the unit, and the events have a filter per unit.

### Several spools in one head

A splitter joins several tubes into one. With a splitter in front of a head, several lanes feed
that head, and a print can use more colors than the U1 has heads. Only one lane is in the head at
a time. When the print asks for a color whose lane is not in its head, the adapter changes the
lane in that head. This is a **lane swap**: the old filament comes out of the head and rolls back
behind the splitter, and the new one goes in
([Printing more colors than heads](install.md#printing-more-colors-than-heads)).

To set it up, join the head's lanes with `+` in `lanes`:

    lanes: a:1+a:2, a:3, a:4

Here lanes 1 and 2 of unit `a` feed head 1 through a splitter. Lane 3 feeds head 2, and lane 4
feeds head 3. Head 4 stays the U1's own.

- A group takes the place of one entry, so its place in the list is its head.
- A group may join lanes of different units: `a:1+a:2+a:3+a:4+b:1+b:2+b:3+b:4` puts all eight
  lanes of two units on head 1.
- With one unit in the bare form, a group feeds the head of its first lane. `1+2, 3, 4` puts lanes
  1 and 2 on head 1, lane 3 on head 3 and lane 4 on head 4.
- When the adapter hooks, it tells ace2k that every lane of a group feeds its head's extruder. Leave
  `laneN_extruder` unset for these lanes in ace2k's sections. A key that names another extruder
  stops Klipper.

A head that several lanes feed is called a **shared head** in the console and on this page.

#### How far the old lane rolls back

At a swap, the old lane rolls back until its tip is behind the splitter. Only then can the new lane
pass. `head1_splitter_mm` sets this distance for head 1, `head2_splitter_mm` for head 2, and so on.
It is the length from the head's gear back to the far end of the splitter, plus a margin.

Measure it with `ACEK_SPLITTER_MEASURE HEAD=<n>`, then run `SAVE_CONFIG`.

You can also measure it with a tape, and add up:

1. the U1's own tube, from its rear inlet to the extruder's gear: about 810 mm;
2. the tube from the U1's inlet to the splitter;
3. the splitter's body;
4. 30 mm as a margin.

With two splitters in a row, measure to the splitter where the lane first meets the other lanes.
That is the splitter farthest from the head. For example, each unit's four lanes go into their own
4→1 splitter, and the two splitters go into a 2→1 splitter. Then add the 2→1 splitter and the tube
between the splitters.

Values measured on the U1:

| Setup | `headN_splitter_mm` |
|---|---|
| one 4→1 splitter: 80 mm of tube from the U1's inlet, a 70 mm splitter | `990` (810 + 80 + 70 + 30) |
| two splitters in a row: 80 mm of tube and a 70 mm splitter more | `1140` (990 + 80 + 70) |

The default is 1000 mm: 810 from the inlet to the gear, 80 to a splitter at the rear, 70 inside the
splitter and 30 as a margin make 990, rounded up to 1000. While a head uses the default, Klipper says so when it
starts: `ace2k_u1: head 1 rolls back the default 1000 mm: run ACEK_SPLITTER_MEASURE HEAD=1, then
SAVE_CONFIG`.

A value set by hand in an included file such as `ace2k-u1.cfg` must be deleted or commented out
before `SAVE_CONFIG` saves a measured one, because Klipper refuses to save a key that an included
file sets. After `SAVE_CONFIG` the value is at the end of `printer.cfg`.

A value that is too short leaves the old tip past the splitter. The new lane cannot pass it, so
the swap fails and the print pauses. A value that is too long only costs time: at `swap_speed`,
80 mm/s, 990 mm take about 12 s.

The path from a bay to a shared head is longer than to a head with its own tube. Set
`head_budget_mm` above the longest one. With two splitters in a row a bay was more than 2 000 mm
from its head: use `head_budget_mm: 3000` there.

Klipper does not start, and says why, when:

| What is wrong | Message |
|---|---|
| `headN_splitter_mm` on a head with one lane or none | `ace2k_u1: head2_splitter_mm: head 2 has one lane or none` |
| a head in `background_heads` that one lane feeds, or none | `ace2k_u1: background_heads: head 2 is fed by one lane or none` |
| a `background_heads` entry that is not a head 1–4 | `ace2k_u1: background_heads: '5' is not a head 1-4` |
| a head listed twice in `background_heads` | `ace2k_u1: background_heads: head 1 is listed twice` |
| an `unload_temps` entry that is not a material and a temperature within 150–350 °C | `ace2k_u1: unload_temps: 'PLA' is not <material>:<°C> (150-350)` |
| a material listed twice in `unload_temps` | `ace2k_u1: unload_temps: PLA is listed twice` |
| a `temp_ranges` entry that is not a material and a range within 150–350 °C, low below high | `ace2k_u1: temp_ranges: 'PLA:230-190' is not <material>:<low>-<high> (150-350 °C)` |
| a material listed twice in `temp_ranges` | `ace2k_u1: temp_ranges: PLA is listed twice` |

A value out of its range stops Klipper with Klipper's own message.

### The lane swap's macros

When a head that several lanes feed changes its lane during a print, the adapter runs two macros
of `ace2k-u1.cfg`. You may change them to make the swap faster or cleaner.

**Danger.** These macros run with a hot nozzle, in the middle of a print. A wrong G-code can crash
the head into the dock, the cleaning area or the part, and damage the printer and the extruder.
Change them only if you are sure of what every line does.

The macros must not move the unit: no `ACEK_*` or `ACE_*` command. The adapter does every move of
the unit around them.

`ACEK_SWAP_UNLOAD` takes the old filament out of the head. It receives:

| Parameter | Meaning |
|---|---|
| `HEAD` | the head, 0 to 3 |
| `TEMP` | the old material's unload temperature (`unload_temps`, else the U1's) |
| `SOFT` | 1 for a soft material, as the U1's filament table says |
| `NOZZLE_DIAMETER` | the head's nozzle |
| `LOAD_TEMP` | the temperature the new filament loads at: the new tool's printing temperature, else the U1's load temperature; 0 on a refill ([a spool ran out](install.md#when-a-spool-runs-out-during-a-print)) |
| `MATERIAL` | the old filament's material in upper case (`PLA`, `PETG`): its spool's, else the head's setting; empty when not known. Only letters, digits, `-`, `+` and `_` are kept |

Its default forms the tip with the U1's `INNER_FILAMENT_UNLOAD`, then heats toward `LOAD_TEMP`.
The U1's unload picks its tip-forming moves by nozzle and material. It is slow but reliable. To
change it for one material, branch on `MATERIAL`, as in `{% if params.MATERIAL == "PLA" %}`.

After this macro the adapter rolls the old lane back up to `probe_mm`, watching its plunger. If
the head still holds the filament, the print pauses.

`ACEK_SWAP_LOAD` puts the new filament in. Its tip waits at the head's sensor. It receives
`HEAD`, `LOAD_TEMP`, `PRINT_TEMP` (the new tool's printing temperature) and `MATERIAL`, the new
filament's material: from its tag or its `ACEK_SPOOL` record, empty when neither tells it (never the
old filament's head setting). It also receives `GRIP`, 0 when a background swap (`background_heads`)
already took the new filament through the gear in the dock with `background_purge_mm: 0`; the
adapter passes it only then, so it is 1 by default. A background swap that purged in the dock runs
no `ACEK_SWAP_LOAD` at all. Its default is below. It pushes 40 mm through the gear, which refills what the
unload retracted, and then purges 10 mm. Both run at `LOAD_TEMP`, at 360 mm/min, which is the speed
of the U1's own pre-extrusion. With `GRIP=0` it skips the 40 mm and only purges.

```
[gcode_macro ACEK_SWAP_LOAD]
gcode:
  MOVE_TO_DISCARD_FILAMENT_POSITION
  M109 S{params.LOAD_TEMP} T{params.HEAD} A0
  M83
  {% if params.GRIP|default(1)|int %}
  G1 E40 F360          ; through the gear, refilling what the unload retracted
  {% endif %}
  G1 E10 F360          ; purge, at the speed of the U1's pre-extrusion at a printing temperature
  ROUGHLY_CLEAN_NOZZLE_WITH_DISCARD
```

The swap passes the new tool's printing temperature as `LOAD_TEMP`, and `PRINT_TEMP` is the same
value. So the head never heats to the U1's load temperature and never cools back. A swap that knows
no printing temperature (a refill with no tool of the plan) passes the U1's load temperature as
both.

The macro's purge is the swap's only purge, so a custom `ACEK_SWAP_LOAD` must purge. The default
one purges 10 mm (a swap used to purge 30 mm here, and before that 20 mm more in the U1's
pre-extrusion).

A custom `ACEK_SWAP_LOAD` that does not read `GRIP` still works after a background swap. It pushes
its 40 mm through the gear again, which only purges 40 mm more.

The macro ends with the U1's clean. The swap's last step, `return`, then runs the U1's own
pre-extrusion with no purge (`LENGTH=0`): its fan, its cut, a second clean, and the U1's idle
position. A refill with no tool of the plan runs it with no purge too. One purge, two cleans: a
few seconds more, but the nozzle oozes less on the way to the part. With `arrival_wipe` (in the
settings above, on by default) the pre-extrusion wipes the nozzle on the pads once more before
the idle position: three cleans. Without the macro's clean,
the nozzle is cleaned once. Nothing breaks either way.

That pre-extrusion retracts 0.5 mm. When the tool has been on the head before, the swap first
retracts the rest of what the slicer retracted when the tool left (10 mm in the files seen),
because the slicer pushes that much back at the tower. A tool used for the first time keeps the
U1's 0.5 mm. So `ACEK_SWAP_LOAD` must not retract: its retraction would add to this one, and the
colour would start thin.

The adapter selects the head (`T<head> A0`) before each macro, so the macros act on that head.

After the load the adapter reads the head's sensor. If it sees no filament, or if a macro fails,
the print pauses. The head goes to the U1's idle position when it can. It is never moved over the
part. The U1's RESUME brings it back to that position, and the next print move takes it to the
part.

When you update from an older release with the installer, it adds the danger block and the two
macros at the end of the printer's copy when it lacks them. By hand, copy them from the
release's `config/ace2k-u1.cfg` into the printer's copy. Without them, a print that uses a head
fed by several lanes is refused at its start, even one that changes no lane. Klipper also says
which macro is missing when it starts, and a swap fails at the missing macro's step.

`swap_purge_mm` is no longer read. Klipper refuses it as an unknown option. Put the purge in
`ACEK_SWAP_LOAD`.

## `[ace2k_u1_history]` — the web page's history

| Setting | Default | Range | What it does |
|---|---|---|---|
| `interval` | `30` | 5–600 s | Seconds between samples of the chamber, humidity and heater outlets. |
| `keep_days` | `7` | 1–90 | How many days of history are kept, one file per day. |
| `path` | `/home/lava/printer_data/logs/ace2k` | a directory | Where the files go. Keep it under Moonraker's `logs` root: the web page reads it there. |
