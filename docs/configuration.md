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
| `lanes` | `1, 2, 3, 4` | lanes 1–4 | The lanes wired to a head. Lane n always feeds head n. A lane left out is not hooked: its head stays the U1's own. |
| `feed_speed` | `70` | mm/s; the unit's own bounds, 9–70, apply | The speed of the push toward a head, and of the follow. |
| `head_budget_mm` | `2000` | mm, above 0 | The longest push toward a head before the head's filament sensor must have seen the filament. |
| `poll_ms` | `200` | 50–1000 ms | How often the adapter watches the bays. |
| `retry_push_mm` | `10` | 1–40 mm | When the U1's load finds that the filament did not extrude, the U1 would pulse its own side motor to push the tip into the extruder's gears. Here that motor is out of the filament's path. So instead, the adapter pauses the lane's follow, feeds this far, and arms the follow again. |
| `apply_tags` | `True` | `True` / `False` | A spool's tag sets its head's filament setting on the U1 ([How a spool's tag sets the head's filament](install.md#how-a-spools-tag-sets-the-heads-filament)). `False` turns that off; loading and feeding are unaffected. |
| `runout_source` | `head` | `head` / `unit` | What pauses the print when a spool runs out at the bay. `head`: the head's own sensor, when the end of the filament reaches it. `unit`: the adapter, once the end has left the unit ([When a spool runs out during a print](install.md#when-a-spool-runs-out-during-a-print)). |
| `grip_mm` | the unit's own, `40` | 10–45 mm | How far the unit pulls a filament you insert while its head still holds a piece that does not belong to this lane. It pulls just until the gear holds the filament. |
| `eject_probe_mm` | `10` | 5–30 mm | When the head's sensor sees filament, `ACE_EJECT` first rolls the filament back this far. This tells whether the head holds it ([Taking a filament out of its bay](install.md#taking-a-filament-out-of-its-bay)). |
| `eject_probe_speed` | `10` | 5–30 mm/s; the unit's own floor, 9, applies | That probe's speed. |
| `eject_settle_ms` | `500` | 200–2000 ms | After the probe, how long to wait for the lane's buffer plunger. Back at rest: the filament is free, and the unit unloads it. Moved: the head holds the filament, and the U1 unloads the head first. |

## `[ace2k_u1_history]` — the web page's history

| Setting | Default | Range | What it does |
|---|---|---|---|
| `interval` | `30` | 5–600 s | Seconds between samples of the chamber, humidity and heater outlets. |
| `keep_days` | `7` | 1–90 | How many days of history are kept, one file per day. |
| `path` | `/home/lava/printer_data/logs/ace2k` | a directory | Where the files go. Keep it under Moonraker's `logs` root: the web page reads it there. |
