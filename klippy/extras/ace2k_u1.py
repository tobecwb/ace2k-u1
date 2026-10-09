"""ace2k on the Snapmaker U1: the U1's own side-feeder flows drive an ace2k unit.

When each of the U1's heads is fed directly by one lane of an Anycubic ACE 2 Pro running ace2k,
the U1's load, unload, preload, print-start feed, print-end unload, runout and tangle detection all
go through its side-feeder module (klippy/extras/filament_feed.py), which expects filament at the
side feeder's inlet. This module attaches to that module at run time, after klippy:ready, through
instance attributes only — no file of the printer is replaced:

  inlet sensor           -> the lane's insert (printer.ace2k lanes[i].insert)
  push to the head       -> an ace2k feed stopped at the head's filament sensor
  side wheels            -> a counter fed by the lane's filament encoder — by the head's extruder
                            while the head holds a piece that is not the lane's filament (the
                            U1's tangle check must not see a waiting filament as stalled)
  channel states         -> + the follow (the unit's both-way assist, keeping the lane's buffer
                            at rest whichever way the extruder moves the filament) from the first
                            extrusion to unload_finish, the eject after an unload
  inlet insert / remove  -> a watch on the lane's insert raising the U1's own port events
  extrude retry nudge    -> the U1's forward pulse of its side motor in load_extruding (its
                            motor is not in the filament's path) becomes a short feed of the lane,
                            the follow stopped before it and armed again after

Everything is checked first; if any hook point differs from what this module expects (a U1
update), nothing is hooked and the console says why. It uses only ace2k's public interface: the
printer.ace2k status, the "ace2k:feed_event" event and the host interface on the [ace2k] object
— and ace2k's ACE_CLEAR G-code, wrapped the Klipper way so that it also releases a lane waiting
behind a piece in its head.

Config:
    [ace2k_u1]
    #lanes: 1, 2, 3, 4        # the lanes wired one-to-one to the heads (lane n -> extruder n-1)
    #feed_speed: 70           # mm/s, the push toward a head and the follow (bench-settled)
    #head_budget_mm: 2000     # the longest push before the head's sensor must have seen it
    #poll_ms: 200             # the bay watch's period
    #retry_push_mm: 10        # mm (1-40), the lane's feed in place of the U1's extrude-retry
                              # pulse: the filament's tip pushed into the extruder's gears
    #apply_tags: True         # a spool's tag read by the unit sets its head's filament on the
                              # U1 (vendor, type, subtype, colour); cleared when it leaves, idle
    #runout_source: head      # head: a filament leaving the bay in the follow feeds on as its
                              # tail and the head's own sensor pauses the print at its end;
                              # unit: the tail's end out of the unit (its tail_out) pauses the
                              # print (the U1's runout pause for that head)
    #grip_mm: 40              # mm, within the unit's bounds (10-45; absent: the unit's own
                              # default, 40): how far the unit pulls a filament inserted while its
                              # head still holds a piece the lane does not own — the gear takes
                              # it, the filament waits there
    #eject_probe_mm: 10       # mm (5-30): ACE_EJECT with the head's sensor set first rolls the
                              # filament back this far
    #eject_probe_speed: 10    # mm/s (5-30; the unit's own floor applies): that rollback's speed
    #eject_settle_ms: 500     # ms (200-2000): after the probe, the wait for the lane's buffer
                              # plunger to settle and be reported: at rest, the filament is free
                              # (the unit unloads it); moved, the head's gear holds it (the U1
                              # unloads the head first)

ACE_EJECT LANE=n [WAIT=0|1] takes a lane's filament out of its bay, refused (nothing moves) while
the lane is in use by a print, busy, in error, with no filament, or already ejecting. With the
head's sensor clear: the follow off, then the unit's unload. With it set: a short rollback probes
the filament, judged by the lane's buffer plunger once it has settled — at rest, the filament is
free: the same unload; moved, the head holds it (or the probe ended in a lane error): the probe's
motor travel fed back and the U1's own unload of that head started (AUTO_FEEDING ... UNLOAD=1,
prepare then doing, as its screen and its print-end unload run it), whose unload_finish brings the
eject. WAIT=1 waits for the decision (after the feed back, for a held filament), never for the U1's
unload. Everything runs from a reactor timer.

The pauses: a lane error in the follow during a print (stuck,
tangled, timeout, motor_stalled) pauses the print through the U1's own tangle pause for the lane's
head; with runout_source: unit the tail's end out of the unit (its tail_out) pauses it through the
U1's own runout pause. Both run from a reactor timer, never from the event handler. The follow is
the standing state of a loaded head: once the adapter arms it, the lane's follow is wanted until a
failure, the unload's end, the filament leaving its bay outside the tail, the tail's end, or a stop
by hand; a wanted lane found idle with no error and a filament is armed again (once per idle
episode), and the U1's resume clears a wanted lane's error so that it is.

The tail ignores the bay: a filament put in during it rests behind the old end, not at the drive's
gear, and nothing loads it. While a lane is in its tail the U1 is not told its bay emptied: that
port event is held and sent once the tail ends, at its tail_out or the stop when the filament's end
passes the head's sensor. At every tail_out one console line tells the operator a new filament can
go in, or — one put in during the tail — to pull it back out and push it in again until the gear
takes it. The U1 is never told of a filament in the bay while its head's sensor sees filament (it
would preload against the loaded head, which fails in the stock U1 too) — after a tail_out, a
tangle, any time: outside a print that port event waits until the head's sensor clears; in a
print (and after a tail_out even with the head already clear) it is dropped, the U1's own runout
path (its replenish load) or a load from its screen bringing the filament; a load of the head by
the U1 ends the hold.
The grip: while a head's sensor sees filament its lane does not own —
after a tail_out, after a tangle, the filament taken out of a loaded head's bay — the lane's grip is
set, so a filament inserted then is only pulled until the gear holds it (grip_mm, no tag search)
and waits there; the old piece is never touched, and the U1's own runout path (its replenish)
pushes the new filament to the head once the old piece has run out. The grip is cleared once that
head's sensor clears, and never set while the lane owns the head (its follow armed, its tail, the
channel loaded). At the gripped load's end the tag is asked for without motion (ACE_RFID_READ
MOVE=0, from a reactor timer): read, the tags apply it as any read tag; not read, a console line.
A preload whose push meets something that cannot move (the unit's "blocked") is not a failure: the
channel goes back to wait_insert and the filament waits; a head load's push "blocked" stays the U1's
failure, a load must not hang.

The tags: each hooked lane's tag, read by the unit, becomes its
head's filament setting through the U1's public SET_PRINT_FILAMENT_CONFIG; the decisions are
ace2k_u1_tags.TagPolicy's, the G-code runs from a reactor timer. Checked first like the hooks: if
the U1's command or its filament table differ, the tags are off and the console says why — the
feed hooks are unaffected.
"""

import inspect
import logging
import math
import re

try:
    from . import filament_feed as feed_module
except ImportError:  # the host tests put klippy/extras (and a fake filament_feed) on the path
    import filament_feed as feed_module
try:
    from . import ace2k_u1_tags as tags_module
except ImportError:  # the host tests, as above
    import ace2k_u1_tags as tags_module

# 2: the follow (assist_both); 3: set_lane_extruder, the feed-forward's lane map; 4: the follow's
# tail (the "tail" notice, the "tail_out" end, the lane's tail flag); 5: the kind "blocked" (a feed
# that meets what cannot move ends without an error), the tail ignoring the bay; 6: the grip
# (set_lane_grip, has_grip and its bounds)
API_MIN = 6
HOOK_DELAY_S = 1.0  # after klippy:ready; the U1 sets module_exist from its inlet ADC at 0.6 s
PUSH_POLL_S = 0.05
LOAD_WAIT_S = 120.0  # the longest wait for the unit's own load of a lane before a push
# the eject's console lines of the normal path ({n}: the lane's number)
EJECT_SAY = {
    "head_now_empty": "ace2k_u1: lane {n} eject: head now empty — unloading",
    "head_empty": "ace2k_u1: lane {n} eject: head empty — unloading",
    "filament_free": "ace2k_u1: lane {n} eject: filament free (plunger at rest) — unloading",
    "held": "ace2k_u1: lane {n} eject: held by the head ({why}) — unloading the head on the U1",
    "plunger_not_at_rest": (
        "ace2k_u1: lane {n} eject: plunger not at rest after the feed back — going on, the U1's"
        " unload settles it"
    ),
    "no_decision": (
        "ace2k_u1: lane {n} eject: no decision after {s:g} s — ACE_EJECT returns, the eject runs on"
    ),
}
EJECT_RETRY_S = 5.0  # how long an eject refused busy (the stopped follow winding down) is retried
EJECT_RETRY_POLL_S = 0.25  # the eject's retry period, whatever the feed report's rate
EJECT_MARGIN_S = 5.0  # an eject's probe and feed back: twice their travel time plus this
EJECT_WAIT_MAX_S = 60.0  # ACE_EJECT WAIT=1 waits this long at most for the eject's decision
FEED_BACK_MIN_MM = 0.5  # a probe's motor travel under this is not fed back
# the final events that leave a lane in error (ace2k's kinds 7-13)
LANE_ERROR_KINDS = (
    "stuck",
    "tangled",
    "motor_stalled",
    "timeout",
    "assist_stall",
    "assist_overrun",
    "unload_incomplete",
)
EJECT_FREE_KINDS = ("done", "blocked")  # a probe that ended without an error: judged by the plunger
# the U1's channel states at rest, by its constants' names: an eject is allowed only in one of
# them. Every other state — a step of a preload, a load, an unload or a manual feed, the U1 waiting
# inside a flow (unload_heat_finish, the manual flow's stage finishes), its test state, a state a
# later U1 adds — is a flow under way on the head
EJECT_REST_STATES = (
    "FEED_STA_NONE",
    "FEED_STA_INITED",
    "FEED_STA_WAIT_INSERT",
    "FEED_STA_PRELOAD_FINISH",
    "FEED_STA_PRELOAD_FAIL",
    "FEED_STA_LOAD_FINISH",
    "FEED_STA_LOAD_FAIL",
    "FEED_STA_UNLOAD_FINISH",
    "FEED_STA_UNLOAD_FAIL",
    "FEED_STA_MANUAL_FINISH",
    "FEED_STA_MANUAL_FAIL",
    "FEED_STA_MANUAL_PREPARE_FAIL",
    "FEED_STA_MANUAL_EXTRUDE_FAIL",
    "FEED_STA_MANUAL_FLUSH_FAIL",
)
# the U1's own unload of a head, as its screen and its print-end unload start it
U1_UNLOAD_SCRIPT = (
    "AUTO_FEEDING EXTRUDER={head} UNLOAD=1 STAGE=prepare\n"
    "AUTO_FEEDING EXTRUDER={head} UNLOAD=1 STAGE=doing"
)
NUDGE_BUSY_S = 5.0  # how long the retry's feed, and the follow's re-arm, are retried while busy
NUDGE_MARGIN_S = 5.0  # the retry feed's wait: twice its travel time plus this
LANES = 4
REPORT_HZ_DEFAULT = 1.0  # the ace2k feed state report's rate when its object does not say
BUSY_MODES = ("loading", "error")  # a lane the unit is still loading, or whose load failed
# a lane held by the follow, or by a one-way assist a manual ACE_ASSIST may have left on it
ARMED_MODES = ("following", "assisting", "assisting_back")
# the U1's states in which its extruder moves the filament (its stock side motor free-wheels then):
# the lane's follow is armed on entering any of them, as the unit's geared motor cannot be
# back-driven and the extruder would grind the filament against an idle lane. The unload's tip
# forming (unload_doing) extrudes forward and retracts in turn: the follow serves both ways.
PULL_STATES = (
    "FEED_STA_LOAD_EXTRUDING",
    "FEED_STA_LOAD_FLUSHING",
    "FEED_STA_MANUAL_EXTRUDING",
    "FEED_STA_MANUAL_FLUSHING",
    "FEED_STA_UNLOAD_DOING",
)
# the lane modes in which a stop ends a motion (and so brings a final event)
IDLE_MODES = (None, "idle", "error")
# the U1's states in which its channel holds the head with this lane's filament, besides the loaded
# ones: a preload brought it to the head's sensor
PRELOAD_STATES = (
    "FEED_STA_PRELOAD_PREPARE",
    "FEED_STA_PRELOAD_FEEDING",
    "FEED_STA_PRELOAD_FINISH",
)
# and every step of the U1's load and manual feed (its constants by these prefixes, whichever the
# module has): from the push to the extrusion — a heating that may take minutes — and a load that
# failed on the way, the filament at the head's sensor is this lane's own
OWNED_PREFIXES = ("FEED_STA_LOAD_", "FEED_STA_MANUAL_")
# the tag read without motion (ace2k_rfid), not waited: its G-code would hold the G-code lock until
# the unit's answer (up to seconds, mid-print); the outcome is read off the lane's tag state
READ_SCRIPT = "ACE_RFID_READ LANE=%d MOVE=0 WAIT=0"
HEAD_CLEAR_S = 1.0  # the head's sensor read clear this long before a waiting lane is released
READ_POLL_S = 0.25  # how often a started read's outcome is looked at
READ_DEADLINE_S = 5.0  # a read not "read" by then is not read (ace2k_rfid waits as long)
BLOCKED = object()  # a preload push that met what cannot move: the filament waits, no failure
OLD_SEQS_KEPT = 8  # the earlier motions this adapter started whose final event is still to come
RUNOUT_SOURCES = ("head", "unit")
# the final events of a lane in the follow that pause a print: the lane can no longer feed
PAUSE_KINDS = ("stuck", "tangled", "timeout", "motor_stalled")
# the U1's tangle pause (its filament_entangle_detect), for the lane's head
TANGLE_ID = 523
TANGLE_CODE = 38
TANGLE_MESSAGE = "detect filament tangled!"
TANGLE_LEVEL = 2
TANGLE_SCRIPT = "\nPAUSE\nM400\n"
TANGLE_EVENT = "filament_entangle_detect:tangled"  # the printer event it sends with the head
# the U1's runout pause (its filament_switch_sensor's RunoutHelper, behind each head's
# filament_motion_sensor e<n>_filament): MODULE_ID_TOOLHEAD, CODE_TOOLHEAD_FILAMENT_RUNOUT
RUNOUT_ID = 523
RUNOUT_CODE = 0
RUNOUT_LEVEL = 2
RUNOUT_SENSOR = "e%d_filament"  # the head sensor's name, which its runout message carries
RUNOUT_PAUSE_DELAY_S = 0.5  # the helper's pause_delay default, when the sensor does not say
RUNOUT_SCRIPT = "PAUSE IS_RUNOUT=1\n\nM400"  # its pause prefix, its (empty) runout_gcode, M400
REPLENISH_SCRIPT = "\nM400\nINNER_AUTO_REPLENISH_FILAMENT EXTRUDER=%d\n"
# the U1's unload steps before its tip forming: a loaded head entering them keeps its follow
UNLOAD_STEPS = (
    "FEED_STA_UNLOAD_PREPARE",
    "FEED_STA_UNLOAD_HOMING",
    "FEED_STA_UNLOAD_PICKING",
    "FEED_STA_UNLOAD_HEATING",
    "FEED_STA_UNLOAD_HEAT_FINISH",
)

CONSTANTS = {
    "FEED_OK": "ok",
    "FEED_ERR": "general",
    "FEED_ERR_TIMEOUT": "timeout",
    "FEED_ERR_NO_FILAMENT": "no_filament",
    "FEED_ERR_RESIDUAL_FILAMENT": "residual_filament",
    "FEED_ERR_MOTOR_SPEED": "motor_speed",
    "FEED_ERR_WHEEL_SPEED": "wheel_speed",
    "FEED_ERR_DISTANCE": "distance",
    "FEED_ACT_PRELOAD": "preload",
    "FEED_ACT_UPDATE_AUTO_MODE": "update_auto_mode",
    "FEED_STA_NONE": "none",
    "FEED_STA_WAIT_INSERT": "wait_insert",
    "FEED_STA_PRELOAD_PREPARE": "preload_prepare",
    "FEED_STA_PRELOAD_FEEDING": "preload_feeding",
    "FEED_STA_PRELOAD_FINISH": "preload_finish",
    "FEED_STA_PRELOAD_FAIL": "preload_fail",
    "FEED_STA_LOAD_EXTRUDING": "load_extruding",
    "FEED_STA_LOAD_FLUSHING": "load_flushing",
    "FEED_STA_LOAD_FINISH": "load_finish",
    "FEED_STA_UNLOAD_PREPARE": "unload_prepare",
    "FEED_STA_UNLOAD_HOMING": "unload_homing",
    "FEED_STA_UNLOAD_PICKING": "unload_picking",
    "FEED_STA_UNLOAD_HEATING": "unload_heating",
    "FEED_STA_UNLOAD_HEAT_FINISH": "unload_heat_finish",
    "FEED_STA_UNLOAD_DOING": "unload_doing",
    "FEED_STA_UNLOAD_FINISH": "unload_finish",
    "FEED_STA_MANUAL_EXTRUDING": "manual_sta_extruding",
    "FEED_STA_MANUAL_FLUSHING": "manual_sta_flushing",
    "FEED_WHEEL_CIRCUMFERENCE": 31.4159,
    "FEED_CHANNEL_1": 0,
    "FEED_CHANNEL_2": 1,
    "FEED_MOTOR_DIR_A": 1,
    "FEED_MOTOR_DIR_B": 2,
}
METHODS = {
    "_port_event_handler": ("detected", "channel"),
    "_put_into_drive": ("channel",),
    "_set_channel_state": ("channel", "state", "save"),
    "_do_feed": ("ch", "action", "stage", "auto_mode"),
    "_hang_neutral": ("channel",),
}
CHANNEL_LISTS = (
    "_port",
    "wheel",
    "wheel_2",
    "module_exist",
    "filament_ch",
    "runout_sensor",
    "channel_error",
    "exception_code",
    "channel_state",
    "channel_error_state",
    "manual_feeding",
)
ATTRIBUTES = ("reactor", "config", "channel_active", "exception_manager", "motor")
MOTOR_PULSE = ("dir", "value", "time")  # the side motor's run_one_cycle parameters

# the tags: the U1's per-head filament setting (print_task_config)
TAG_COMMAND = "SET_PRINT_FILAMENT_CONFIG"
TAG_PARAMS = (
    "CONFIG_EXTRUDER",
    "VENDOR",
    "FILAMENT_TYPE",
    "FILAMENT_SUBTYPE",
    "FILAMENT_COLOR_RGBA",
    "FORCE",
)
# the head's setting in print_task_config's status: one list entry per head
TAG_STATUS = {
    "vendor": "filament_vendor",
    "type": "filament_type",
    "subtype": "filament_sub_type",
    "rgba": "filament_color_rgba",
}
TAG_CLEAR = "VENDOR=NONE FILAMENT_TYPE=NONE FILAMENT_SUBTYPE=NONE FILAMENT_COLOR_RGBA=FFFFFFFF"
TAG_HEADS = 4  # the U1's CONFIG_EXTRUDER range, 0..3
TAG_TRIPLE = ("VENDOR", "FILAMENT_TYPE", "FILAMENT_SUBTYPE")  # the U1 takes them together
TAG_QUOTED = ("FILAMENT_SUBTYPE",)  # sent single-quoted on a write (subtypes carry spaces)
HEX8 = re.compile(r"[0-9A-Fa-f]{8}")
# what breaks a G-code value: the U1 cuts a line at ';' and its parameter parser stops at '#',
# '*' or ';', quotes or not; an unquoted value also ends at a blank
UNSAFE = re.compile(r"[\s'\"#*;]")
UNSAFE_QUOTED = re.compile(r"['\"#*;]")
PRINTING_STATES = ("printing", "paused")  # a clear waits while print_stats is in either
# a filament_parameters key is <vendor>_<type>_<subtype>_<key>; a type is upper case (PLA,
# PETG-CF, PA6-GF), which tells it from the table's own entries (hard_filaments_max_flow_k)
TYPE_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9+-]*")


def check_feed(module, ff):
    """[] when ff (a filament_feed object) and its module have every hook point this adapter
    uses, in the expected form; otherwise one reason per difference."""
    problems = []
    for name, value in CONSTANTS.items():
        if getattr(module, name, None) != value:
            problems.append(f"constant {name} is not {value!r}")
    for name, params in METHODS.items():
        method = getattr(ff, name, None)
        if method is None:
            problems.append(f"method {name} missing")
            continue
        got = tuple(inspect.signature(method).parameters)[: len(params)]
        if got != params:
            problems.append(f"method {name} takes {got}, expected {params}")
    for name in CHANNEL_LISTS:
        value = getattr(ff, name, None)
        if not isinstance(value, list) or len(value) != 2:
            problems.append(f"{name} is not a two-channel list")
    for name in ATTRIBUTES:
        if not hasattr(ff, name):
            problems.append(f"attribute {name} missing")
    config = getattr(ff, "config", None)
    if not isinstance(config, dict) or "auto_mode" not in config:
        problems.append("config['auto_mode'] missing")
    pulse = getattr(getattr(ff, "motor", None), "run_one_cycle", None)
    if pulse is None:
        problems.append("motor.run_one_cycle missing")
    else:
        got = tuple(inspect.signature(pulse).parameters)[: len(MOTOR_PULSE)]
        if got != MOTOR_PULSE:
            problems.append(f"motor.run_one_cycle takes {got}, expected {MOTOR_PULSE}")
    return problems


def known_types(fparams):
    """The U1 filament table's types: the distinct <type> parts of the keys of the
    filament_parameters object's tables (its status, and the dicts it holds them in)."""
    tables = []
    try:
        tables.append(fparams.get_status(0))
    except Exception:
        logging.exception("ace2k_u1: filament_parameters status")
    tables.extend(v for v in vars(fparams).values() if isinstance(v, dict))
    types_ = set()
    for table in tables:
        for key in table if isinstance(table, dict) else ():
            parts = str(key).split("_")
            if len(parts) >= 4 and TYPE_TOKEN.fullmatch(parts[1]):
                types_.add(parts[1])
    return types_


def check_tags(ptc):
    """[] when ptc (the U1's print_task_config object) takes SET_PRINT_FILAMENT_CONFIG with
    every parameter the tags write and reports the four per-head fields they compare; otherwise
    one reason per difference."""
    method = getattr(ptc, "cmd_" + TAG_COMMAND, None)
    if not callable(method):
        return [f"print_task_config has no {TAG_COMMAND}"]
    problems = []
    try:
        source = inspect.getsource(method)
    except (OSError, TypeError):
        return [f"{TAG_COMMAND}'s source unreadable"]
    for name in TAG_PARAMS:
        if f"'{name}'" not in source and f'"{name}"' not in source:
            problems.append(f"{TAG_COMMAND} does not read {name}")
    try:
        status = ptc.get_status(0)
    except Exception as err:
        return problems + [f"print_task_config status: {err}"]
    for key in TAG_STATUS.values():
        if not isinstance(status.get(key), list):
            problems.append(f"print_task_config status has no list {key}")
    return problems


def tag_params(head, setting):
    """The parameters of a Setting's write: vendor/type/subtype only with a type, the colour
    only with one."""
    params = {"CONFIG_EXTRUDER": str(head)}
    if setting.type is not None:
        params.update(
            VENDOR=setting.vendor, FILAMENT_TYPE=setting.type, FILAMENT_SUBTYPE=setting.subtype
        )
    if setting.rgba is not None:
        params["FILAMENT_COLOR_RGBA"] = setting.rgba
    params["FORCE"] = "1"
    return params


def clear_params(head, written=None):
    """The reset of what a write put on the head: vendor/type/subtype only if it carried a
    type, the colour only if it carried one (None: everything)."""
    reset = dict(w.split("=", 1) for w in TAG_CLEAR.split())
    if written is not None:
        if written.type is None:
            for key in TAG_TRIPLE:
                del reset[key]
        if written.rgba is None:
            del reset["FILAMENT_COLOR_RGBA"]
    params = {"CONFIG_EXTRUDER": str(head)}
    params.update(reset)
    params["FORCE"] = "1"
    return params


def check_params(params, quoted=()):
    """What the U1's SET_PRINT_FILAMENT_CONFIG would refuse, checked before sending, plus what
    would not survive the G-code parser: one reason per problem, [] when it can be sent."""
    problems = []
    head = params.get("CONFIG_EXTRUDER", "")
    if not (head.isdigit() and 0 <= int(head) < TAG_HEADS):
        problems.append(f"CONFIG_EXTRUDER {head!r} not within 0..{TAG_HEADS - 1}")
    given = [k for k in TAG_TRIPLE if k in params]
    if given and len(given) != len(TAG_TRIPLE):
        problems.append("VENDOR, FILAMENT_TYPE and FILAMENT_SUBTYPE not together")
    rgba = params.get("FILAMENT_COLOR_RGBA")
    if rgba is not None and not HEX8.fullmatch(rgba):
        problems.append(f"FILAMENT_COLOR_RGBA {rgba!r} not 8 hex digits")
    for key, value in params.items():
        bad = (UNSAFE_QUOTED if key in quoted else UNSAFE).search(value)
        if bad:
            problems.append(f"{key} {value!r} carries {bad.group()!r}")
    if params.get("FORCE") != "1":
        problems.append("FORCE not 1")
    return problems


def format_params(params, quoted=()):
    words = [TAG_COMMAND]
    for key, value in params.items():
        words.append(f"{key}='{value}'" if key in quoted else f"{key}={value}")
    return " ".join(words)


def tag_gcode(head, setting):
    """The write of a Setting as G-code; the subtype always quoted ('' when empty)."""
    return format_params(tag_params(head, setting), TAG_QUOTED)


def clear_gcode(head, written=None):
    return format_params(clear_params(head, written))


def head_setting(status, head):
    """The head's current setting from print_task_config's status, in the form the policy
    compares (vendor/type/subtype/rgba, trimmed); None when the status does not carry it."""
    current = {}
    for field, key in TAG_STATUS.items():
        values = status.get(key)
        if not isinstance(values, list) or not 0 <= head < len(values):
            return None
        value = values[head]
        current[field] = "" if value is None else str(value).strip()
    return current


def describe(setting):
    """What a write puts on the head, for ACE_ADAPTER_STATUS."""
    if setting.type is None:
        return f"{setting.rgba} only — {setting.reason}"
    subtype = setting.subtype or "''"
    text = f"{setting.vendor} {setting.type} {subtype}"
    return text + (f" {setting.rgba}" if setting.rgba is not None else "")


def tag_summary(tag):
    """A lane's tag for ACE_ADAPTER_STATUS: its state and the record's brand, name, colour."""
    tag = tag or {}
    record = tag.get("record") or {}
    words = [tag.get("state") or "unknown"]
    for key in ("brand", "name", "color_rgba"):
        if record.get(key):
            words.append(str(record[key]))
    if record and not record.get("name") and record.get("material"):
        words.insert(2 if record.get("brand") else 1, str(record["material"]))
    return " ".join(words)


def tag_identity(tag):
    """A lane's tag as (state, uid): what tells a fresh read from the record held before."""
    tag = tag or {}
    return (tag.get("state"), tag.get("uid"))


def extruder_names(extruders):
    """The U1's extruders by head index: its printer object "extruder_list" holds one extruder
    object per head, each carrying its index (extruder_num) and its section name (name)."""
    return {e.extruder_num: e.name for e in extruders or ()}


class EncoderWheel:
    """A stand-in for the U1's side-feeder wheel tachometer, fed by a lane's filament encoder:
    the counts grow with the absolute travel at the wheel's own resolution (ppr pulses per
    revolution counted on both edges, one revolution = FEED_WHEEL_CIRCUMFERENCE mm). The travel
    is kept in fractional counts: a sample's step is often under one count (at ppr 6, one count is
    about 2.6 mm), and rounding each step would read a slow extrusion as a stalled wheel."""

    def __init__(self, ppr, circumference_mm):
        self.ppr = ppr
        self.counts_per_mm = 2.0 * ppr / circumference_mm
        self._travel = 0.0  # in counts, fractional
        self._last_mm = None
        self._last_time = 0.0
        self._rpm = 0.0

    def sample(self, encoder_mm, eventtime):
        if encoder_mm is None:
            return
        if self._last_mm is not None:
            step = abs(encoder_mm - self._last_mm) * self.counts_per_mm
            dt = eventtime - self._last_time
            self._travel += step
            self._rpm = step / (2.0 * self.ppr) * 60.0 / dt if dt > 0 else 0.0
        self._last_mm = encoder_mm
        self._last_time = eventtime

    def rebase(self, mm, eventtime):
        """A new source from here on (the lane's encoder or the head's extruder): its reading
        taken as the base, nothing travelled — the counts stay where they are, never jump."""
        self._last_mm = mm
        self._last_time = eventtime
        self._rpm = 0.0

    def get_counts(self):
        # the whole counts travelled; the margin absorbs float error at an exact count
        return int(self._travel + 1e-6)

    def get_rpm(self):
        return self._rpm

    def get_last_report_time(self):
        return self._last_time


class EjectRefused(Exception):  # noqa: N818 — a refusal, read by ACE_EJECT as its G-code error
    """ACE_EJECT refused before anything moved; the text says why."""


class EjectJob:
    """One ACE_EJECT on a lane, run step by step from its reactor timer: begin (the follow off,
    the head's sensor read), probe, probe_wait, back, back_wait, u1 (the U1's unload of the
    head), done. decided: the eject's decision is taken (WAIT=1 returns then); failed: why the
    eject ended short, or None."""

    def __init__(self, h):
        self.h = h
        self.step = "begin"
        self.timer = None
        self.running = False  # the timer's call in progress (run_script may let it re-enter)
        self.decided = False
        self.failed = None
        self.move = None
        self.give_up = 0.0  # a busy start is retried until then
        self.deadline = 0.0  # a started move with no final event by then is stopped
        self.back_mm = 0.0
        self.probe_event = None  # the probe's final event, kept across the settle
        self.settle_at = 0.0  # when the plunger is read after the probe
        self.had_follow = False  # at begin: the follow wanted or armed — what a restore gives back


class Hooked:
    """One hooked channel: a feed object, its channel, the lane (0-based) and the head index."""

    def __init__(self, ff, ch, lane):
        self.ff = ff
        self.ch = ch
        self.lane = lane
        self.head = ff.filament_ch[ch]
        self.present = False  # what the U1 was last told about its inlet
        self.wheel = None
        self.rise_at = None  # when insert went True and stayed True, until the U1 is told
        self.error_said = False  # the console was told this lane's load ended in error
        self.armed = False  # this adapter armed the lane's follow and has not stopped it since
        self.armed_at = 0.0  # when it did
        self.assist_seq = None  # the seq of the follow move this adapter started
        # the follow as the standing state: set when this adapter arms it, cleared on a failure,
        # the unload's end, the filament leaving its bay outside the tail, the tail's end
        # (tail_out, or its end past the head's sensor), or a stop by hand
        self.follow_wanted = False
        self.rearm_tried = False  # the standing re-arm was tried in this idle episode
        # this adapter stopped the lane while it ran: its next final event answers that stop
        # (a "stopped" then is this adapter's own, not a stop by hand)
        self.expect_stopped = False
        self.expect_seq = None  # the seq of the motion that stop ended (always known when set)
        self.port_held = False  # the bay's emptying, held back from the U1 while in the tail
        self.tail_stopped = False  # the tail's end at the head's sensor stopped the lane
        self.tail_ended = False  # a final event came while the bay's emptying was held
        # a tail_out came: a filament in the bay is held like rise_held below, even when the head's
        # sensor has already cleared (in a print its insert is then dropped, not raised)
        self.after_tail = False
        # a settled insert not told to the U1 because the head's sensor saw filament (a preload
        # against a loaded head fails): told outside a print once the head clears, dropped in one
        self.rise_held = False
        self.push_end = None  # the final event kind that ended the last push short of the head
        # when the lane's current motion ended by its final event; until a report shows the
        # lane idle, this adapter starts a motion, or a report period has surely passed (a
        # report since then shows a newer motion), the reported mode is not believed
        self.ended_at = None
        self.motion_seq = None  # the seq of the last motion this adapter started on the lane
        self.motion_open = False  # its final event is still to come
        self.old_seqs = []  # earlier motions of this adapter's, replaced before their end came
        self.hold = False  # this adapter is running the lane itself (the extrude retry)
        # the grip: set by this adapter (and not cleared since), and believed still on the unit —
        # a start on the lane clears it there, the gripped load consumes it: any final event
        self.grip_wanted = False
        self.grip_live = False
        # the bay was seen empty while the grip was live (at its set, or by the bay watch): the
        # unit's automatic load starts only on an insert edge, so a loaded of seq 0 with the
        # grip live, the insert down since and up at the event, is the gripped load — a read with
        # motion (ACE_RFID_READ MOVE=1) starts on a filament present all along, no edge
        self.grip_down = False
        # the lane's tag (state, uid) while the bay was empty: a "read" the same as it is a stale
        # record, not the gripped spool's
        self.tag_before = (None, None)
        self.grip_refused = None  # the want the unit's interface last refused (said once)
        self.head_unread = False  # the head's sensor could not be read (logged once)
        # what the wheel stand-in reads: the lane's encoder, or — while the head holds a piece
        # that is not this lane's filament — the head's extruder (the U1's tangle check compares
        # the wheel with the extruder only while the bay reads a filament: the waiting one, still)
        self.wheel_source = "encoder"
        # the lane's filament waits behind a piece in the head that is not its own: a U1 load that
        # finds the head set uses that piece — the lane gets no follow, no end of its holds, the
        # grip stays wanted and the wheel follows the extruder. Every decision reads this field.
        # Set only at a transition — a tail_out with the head's sensor set, a rise held because
        # the head is set while the U1's channel does not hold it — never by a tick, never at
        # the hook: the lane owns what is in its head by default. A known limit of that default:
        # after a restart with a filament in the bay and the head set by a piece that is not its
        # own, the lane is taken to own it (the U1's channel reads preload_finish either way); a
        # wrong guess ends in the follow's error and the lane-error pause — the safe side.
        # Cleared on the head sensor's clear, by a push that starts with the head clear, or by
        # ACE_CLEAR LANE=n
        self.waits_behind = False
        self.behind_said = False  # its "no follow" logged in this episode
        self.clear_since = None  # since when the head's sensor has read clear (the debounce)
        self.unread_said = False  # the console told the sensor cannot be read while waiting
        self.position_unread = False  # the head's extruder position could not be read (logged once)


class Adapter:
    """The hooks; Klipper-free so the tests can build it from fakes (ACE2kU1 below wires it)."""

    def __init__(
        self,
        reactor,
        module,
        ace2k,
        feeds,
        lanes,
        feed_speed,
        head_budget_mm,
        poll_s,
        retry_push_mm=10.0,
        extruders=None,
        grip_mm=None,
        extruder_position=None,
        eject_probe_mm=10.0,
        eject_probe_speed=10.0,
        eject_settle_s=0.5,
    ):
        self.reactor = reactor
        # ACE_EJECT with the head's sensor set: a rollback of eject_probe_mm at eject_probe_speed;
        # eject_settle_s after its end, the lane's buffer plunger at rest: free; moved: held (the
        # encoder cannot tell — the buffer between unit and head takes up the whole probe)
        self.eject_probe_mm = eject_probe_mm
        self.eject_probe_speed = eject_probe_speed
        self.eject_settle_s = eject_settle_s
        self.eject_jobs = {}  # lane -> its EjectJob still running
        self.module = module
        self.ace2k = ace2k
        self.api = ace2k.feed
        self.feeds = feeds
        self.lanes = set(lane - 1 for lane in lanes)
        self.feed_speed = feed_speed
        self.head_budget_mm = min(head_budget_mm, self.api.move_max_mm)
        self.poll_s = poll_s
        self.retry_push_mm = retry_push_mm
        # None: the unit's own default, read by ace2k from the
        # dictionary at connect — None too on an image without the grip, which is not hooked
        self.grip_mm = grip_mm if grip_mm is not None else getattr(self.api, "grip_mm", None)
        # head index -> the head's extruder position now (mm, the U1's own reading); None: none
        self.extruder_position = extruder_position
        self.owned_states = None  # _owned_states(), built at the hook
        self.loaded_states = None  # _loaded_states(), built at the hook beside it
        # the tag read without motion at a gripped load's end: the lanes still to read, and the
        # reads started (lane -> (Hooked, deadline)) whose outcome the read timer watches
        self.read_timer = None
        self.read_pending = []
        self.read_waits = {}
        self.read_running = False
        # head index -> the U1's extruder name (extruder_names); None: no lane is mapped
        self.extruders = extruders
        # insert arrives at 10 Hz but the lane's mode only with the feed state report: an insert
        # is told to the U1 once it has held for two reports (plus a watch period) and the mode
        # then says the unit is not loading the filament itself
        report_hz = getattr(self.api, "report_hz", None) or REPORT_HZ_DEFAULT
        self.settle_s = 2.0 / report_hz + poll_s
        # a busy refusal is retried once per report, not per local poll: each try is a round
        # trip on the unit's half-duplex link
        self.busy_retry_s = 1.0 / report_hz
        self.gcode = None  # set by ACE2kU1; the console notices go to the log only without it
        self.hooked = []
        self.problems = None
        self.ejects = {}
        self.eject_timers = {}  # lane -> the timer of an eject still to be started
        # the tags: off (tags_off says why) until setup_tags
        self.tags = None  # the TagPolicy
        self.tags_off = "not set up"
        self.ptc = None  # the U1's print_task_config
        self.print_stats = None
        self.toolhead = None  # the active extruder: a write to it waits while printing
        self.tag_timer = None
        self.tag_pending = {}  # lane -> the action still to be run by the tag timer
        self.tag_running = set()  # lanes whose G-code is being run (the timer may be re-entered)
        self.applied = {}  # lane -> what the tag last did to the head, for ACE_ADAPTER_STATUS
        # the pauses: the U1's objects (setup_pause); without them a pause is a console line
        self.runout_source = "head"
        self.pause_resume = None
        self.exception_manager = None
        self.send_event = None  # the printer's send_event
        self.task_config = None  # the U1's print_task_config: its auto replenish
        self.pause_timer = None
        self.pause_pending = []  # (path, Hooked, kind) still to be run by the pause timer
        self.pause_running = False  # the pause timer is running (its G-code may yield)
        self.last_print_state = None  # print_stats' state at the last watch tick

    def _lane_status(self, lane, eventtime=None):
        status = self.ace2k.get_status(eventtime or self.reactor.monotonic())
        return status["lanes"][lane]

    def _say(self, msg):
        """A notice for the log and, when wired, the console."""
        logging.info(msg)
        if self.gcode is not None:
            self.gcode.respond_info(msg)

    def _present(self, lane):
        return self._lane_status(lane).get("insert") is True

    def _present_or_not(self, h):
        """_present, a failure read as not present (logged): never raises, so the caller's
        resets after it always run."""
        try:
            return self._present(h.lane)
        except Exception:
            logging.exception("ace2k_u1: lane %d status", h.lane + 1)
            return False

    def hook_all(self):
        """Check everything, then hook every mapped channel — or nothing. Returns the problems."""
        problems = []
        # ace2k carries the version on the feed object (printer.lookup_object("ace2k").feed),
        # the only part of its host interface this adapter holds
        if getattr(self.api, "API_VERSION", 0) < API_MIN:
            problems.append(f"ace2k host interface older than {API_MIN}")
        else:
            problems.extend(self._check_grip())
        for ff in self.feeds:
            problems.extend(check_feed(self.module, ff))
        if not problems:
            problems.extend(self._check_head_sensors())
        self.problems = problems
        if problems:
            return problems
        self.loaded_states = self._loaded_states()  # once, at the hook: the U1's constants
        self.owned_states = self._owned_states()
        for ff in self.feeds:
            mine = []
            for ch in range(2):
                lane = ff.filament_ch[ch]
                if lane in self.lanes:
                    mine.append(Hooked(ff, ch, lane))
            if mine:
                self._hook_feed(ff, {h.ch: h for h in mine})
                self.hooked.extend(mine)
        self._map_extruders()
        return []

    def _map_extruders(self):
        """Tell ace2k which extruder each hooked lane feeds: its head's, for the feed-forward. A
        laneN_extruder key in [ace2k] wins over this on ace2k's side. A head the printer has no
        extruder for leaves its lane unmapped (hooked, without the feed-forward) and is said."""
        if self.extruders is None:
            return
        for h in sorted(self.hooked, key=lambda h: h.lane):
            name = self.extruders.get(h.head)
            if name is None:
                self._say(
                    f"ace2k_u1: lane {h.lane + 1}: no extruder {h.head} on the printer;"
                    " no feed-forward"
                )
                continue
            self.api.set_lane_extruder(h.lane, name)

    def _check_grip(self):
        """grip_mm within the unit's bounds (the dictionary's, read by ace2k at connect)."""
        low = getattr(self.api, "grip_min_mm", None)
        high = getattr(self.api, "grip_max_mm", None)
        if low is None or high is None:
            return []  # no grip on the image: the hook says so first
        if self.grip_mm is None:
            return ["the unit's firmware gives no default grip"]
        if not low <= self.grip_mm <= high:
            return [f"grip_mm {self.grip_mm:g} is outside the unit's {low:g}..{high:g} mm"]
        return []

    def _check_head_sensors(self):
        """One problem per channel to be hooked whose head filament sensor is absent: the U1
        looks it up optionally, and the push toward the head stops on it."""
        problems = []
        for ff in self.feeds:
            for ch in range(2):
                head = ff.filament_ch[ch]
                if head not in self.lanes:
                    continue
                sensor = ff.runout_sensor[ch]
                if sensor is None or not callable(getattr(sensor, "get_status", None)):
                    problems.append(f"head sensor e{head}_filament missing")
        return problems

    def _hook_feed(self, ff, by_ch):
        orig_hang = ff._hang_neutral

        def hang_neutral(channel):
            if channel not in by_ch:
                orig_hang(channel)

        ff._hang_neutral = hang_neutral
        now = self.reactor.monotonic()
        for ch, h in by_ch.items():
            ff._port[ch].get_filament_detected = lambda lane=h.lane: self._present(lane)
            ff.module_exist[ch] = True
            lane = self._lane_status(h.lane, now)
            h.wheel = EncoderWheel(ff.wheel[ch].ppr, self.module.FEED_WHEEL_CIRCUMFERENCE)
            h.wheel.sample(lane.get("encoder_mm"), now)
            ff.wheel[ch] = h.wheel
            ff.wheel_2[ch] = h.wheel
            # a filament already in the bay at boot is present at once; the U1's state is
            # re-derived below, not raised as an insert
            insert = lane.get("insert") is True
            h.present = insert and lane.get("mode") not in BUSY_MODES
            h.rise_at = now if insert else None
            self.reactor.register_async_callback(
                lambda et, ff=ff, ch=ch: ff._do_feed(
                    ch,
                    self.module.FEED_ACT_UPDATE_AUTO_MODE,
                    auto_mode=ff.config["auto_mode"][ch],
                )
            )
        self._hook_push(ff, by_ch)
        self._hook_states(ff, by_ch)
        self._hook_nudge(ff, by_ch)

    # a push's final event -> the U1's error; a kind not listed here is a timeout
    EVENT_ERRORS = {
        "stuck": "FEED_ERR_WHEEL_SPEED",
        "tangled": "FEED_ERR_WHEEL_SPEED",
        "motor_stalled": "FEED_ERR_MOTOR_SPEED",
        "runout": "FEED_ERR_NO_FILAMENT",
        "unload_incomplete": "FEED_ERR_WHEEL_SPEED",
        "timeout": "FEED_ERR_TIMEOUT",
        "stopped": "FEED_ERR_TIMEOUT",
        "stopped_link": "FEED_ERR_TIMEOUT",
        "stopped_shutdown": "FEED_ERR_TIMEOUT",
        "done": "FEED_ERR_DISTANCE",
        "blocked": "FEED_ERR_DISTANCE",  # the push met what cannot move: the head not reached
    }
    # the U1's exception code is its flow's base (10 preload, 30 load) plus this
    CODE_OFFSET = {
        "general": 0,
        "motor_speed": 1,
        "wheel_speed": 2,
        "no_filament": 3,
        "timeout": 4,
        "residual_filament": 5,
        "distance": 5,
    }

    def _err(self, name):
        return getattr(self.module, name)

    def _head_set(self, h):
        status = h.ff.runout_sensor[h.ch].get_status(self.reactor.monotonic())
        return status["filament_detected"]

    def _start_push(self, h):
        """Start the push; returns (move, None) or (None, the U1 error name). While the unit is
        still loading the lane itself — by its mode, or by a busy refusal, the mode report being
        slower than the unit — it waits, up to LOAD_WAIT_S."""
        give_up = self.reactor.monotonic() + LOAD_WAIT_S
        waiting = False
        next_try = 0.0  # a busy refusal defers the next start by busy_retry_s
        while True:
            lane = self._lane_status(h.lane)
            mode = lane.get("mode")
            waiting = waiting or mode == "loading"
            if waiting:
                if mode == "error":
                    logging.warning("ace2k_u1: lane %d load ended in error", h.lane + 1)
                    return None, self._err("FEED_ERR_MOTOR_SPEED")
                if lane.get("insert") is not True:
                    return None, self._err("FEED_ERR_NO_FILAMENT")
            if mode != "loading" and self.reactor.monotonic() >= next_try:
                try:
                    move = self.api.start_move(h.lane, "feed", self.head_budget_mm, self.feed_speed)
                    self._started(h, move)
                    return move, None
                except Exception as err:  # FeedRefused (reason), ValueError, RuntimeError
                    reason = getattr(err, "reason", None)
                    if reason != "busy":
                        logging.warning("ace2k_u1: lane %d push refused: %s", h.lane + 1, err)
                        name = (
                            "FEED_ERR_NO_FILAMENT"
                            if reason == "no_filament"
                            else "FEED_ERR_MOTOR_SPEED"
                        )
                        return None, self._err(name)
                    waiting = True
                    next_try = self.reactor.monotonic() + self.busy_retry_s
            now = self.reactor.pause(self.reactor.monotonic() + PUSH_POLL_S)
            if now >= give_up:
                logging.warning("ace2k_u1: lane %d still loading, push abandoned", h.lane + 1)
                self.api.stop(h.lane)
                return None, self._err("FEED_ERR_TIMEOUT")

    def push_to_head(self, h):
        """Push the lane until the head's filament sensor sees the filament. None on success, else
        the U1 error name (motor_speed, wheel_speed, no_filament, timeout, distance); a push
        that ended by its final event leaves that kind in h.push_end."""
        h.push_end = None
        if self._head_set(h):
            return None
        move, error = self._start_push(h)
        if error is not None:
            return error
        try:
            return self._poll_push(h, move)
        except BaseException:
            # a push toward a head always stops: whatever broke the watch, the feed must not run
            # on to its budget behind a failure
            try:
                self.api.stop(h.lane)
            except Exception:
                logging.exception("ace2k_u1: lane %d stop after a failed push", h.lane + 1)
            raise

    def _poll_push(self, h, move):
        """Watch a started push: None at the head's sensor, else the U1 error name."""
        deadline = self.reactor.monotonic() + self.head_budget_mm / self.feed_speed * 2.0 + 5.0
        while True:
            now = self.reactor.pause(self.reactor.monotonic() + PUSH_POLL_S)
            if self._head_set(h):
                self.api.stop(h.lane)
                return None
            event = move.result()
            if event is not None:
                logging.warning(
                    "ace2k_u1: lane %d push ended %s before the head sensor",
                    h.lane + 1,
                    event["kind"],
                )
                h.push_end = event["kind"]
                return self._err(self.EVENT_ERRORS.get(event["kind"], "FEED_ERR_TIMEOUT"))
            if now >= deadline:
                logging.warning("ace2k_u1: lane %d push timed out", h.lane + 1)
                self.api.stop(h.lane)
                return self._err("FEED_ERR_TIMEOUT")

    def _hook_push(self, ff, by_ch):
        """The load's push into the drive and the preload, on hooked channels, through the
        unit; everything else reaches the U1's own code."""
        orig_put = ff._put_into_drive
        orig_do_feed = ff._do_feed

        def put_into_drive(channel):
            h = by_ch.get(channel)
            if h is None:
                return orig_put(channel)
            if self._head_holds(h):
                if h.waits_behind:
                    # the head already set by the piece ahead of this lane's waiting filament:
                    # the U1's load goes on with that piece; the lane does not take the head
                    logging.info(
                        "ace2k_u1: lane %d waits behind e%d's piece: its load uses that piece",
                        h.lane + 1,
                        h.head,
                    )
                    return None
            else:
                self._clear_waits_behind(h)  # the push brings the lane's own filament to the head
            error = self.push_to_head(h)
            if error is not None:
                ff.channel_error[channel] = error
                ff.exception_code[channel] = 30 + self.CODE_OFFSET[error]
                raise RuntimeError(f"ace2k_u1: lane {h.lane + 1} push to e{h.head}: {error}")
            self._end_hold(h)
            return None

        def do_feed(ch, action=None, stage=None, auto_mode=None):
            h = by_ch.get(ch)
            if h is None or action != self.module.FEED_ACT_PRELOAD:
                return orig_do_feed(ch, action, stage, auto_mode)
            return self.preload(h)

        ff._put_into_drive = put_into_drive
        ff._do_feed = do_feed

    def _preload_push(self, h):
        """The preload's checks and push: None, BLOCKED, or the U1 error name."""
        ff, ch, m = h.ff, h.ch, self.module
        ff._set_channel_state(ch, m.FEED_STA_PRELOAD_PREPARE, True)
        if not self._present(h.lane):
            return m.FEED_ERR_NO_FILAMENT
        if self._head_set(h):
            return m.FEED_ERR_RESIDUAL_FILAMENT
        # a push that starts with the head clear brings the lane's own filament (as a load's)
        self._clear_waits_behind(h, "a preload from a clear head")
        ff._set_channel_state(ch, m.FEED_STA_PRELOAD_FEEDING)
        error = self.push_to_head(h)
        if error is not None and h.push_end == "blocked":
            return BLOCKED
        if error == m.FEED_ERR_DISTANCE:  # a whole budget without the sensor: 14
            return m.FEED_ERR_TIMEOUT
        return error

    def preload(self, h):
        """The U1's PRELOAD on a hooked channel: the same lock, states and reporting, the push
        being the unit's. A push that met what cannot move (blocked: something in the tube
        ahead) is no failure: the channel goes back to wait_insert and the filament waits."""
        ff, ch, m = h.ff, h.ch, self.module
        if not (ff.config["auto_mode"][ch] and ff.module_exist[ch]):
            return
        while ff.channel_active is not None:
            self.reactor.pause(self.reactor.monotonic() + 0.1)
        ff.channel_active = ch
        ff.channel_error[ch] = m.FEED_OK
        ff.exception_code[ch] = 10
        ff.channel_error_state[ch] = m.FEED_STA_NONE
        try:
            try:
                error = self._preload_push(h)
            except Exception:  # as the U1 does: anything unexpected is a general preload failure
                logging.exception("ace2k_u1: lane %d preload failed", h.lane + 1)
                error = m.FEED_ERR
            if error is None:
                ff._set_channel_state(ch, m.FEED_STA_PRELOAD_FINISH)
                return
            if error is BLOCKED:
                ff._set_channel_state(ch, m.FEED_STA_WAIT_INSERT, True)
                # the unit cannot tell what holds the filament (a stop at its encoder): the line
                # names both places; in a print the U1's replenish load pushes it once the head's
                # piece has run out, outside one nothing retries it
                n = h.lane + 1
                cause = f"ace2k_u1: lane {n}: the filament does not move — something in the tube"
                if self._printing():
                    self._say(
                        f"{cause} ahead or at the spool; it waits and goes to the head when the"
                        " head's piece has run out"
                    )
                else:
                    self._say(
                        f"{cause} ahead or at the spool; clear it, then pull the filament out and"
                        " push it in again"
                    )
                return
            ff.channel_error[ch] = error
            ff.exception_code[ch] = 10 + self.CODE_OFFSET[error]
            ff._set_channel_state(ch, m.FEED_STA_PRELOAD_FAIL)
            ff.channel_error_state[ch] = ff.channel_state[ch]
            if ff.exception_manager is not None:
                ff.exception_manager.raise_exception_async(
                    id=ff.exception_manager.list.MODULE_ID_FEEDING,
                    index=h.head,
                    code=ff.exception_code[ch],
                    message=f"preload fail: {error}",
                    oneshot=1,
                    level=1,
                )
        finally:
            ff.channel_active = None

    # the kinds an unload move can end with in error — deliberately not ace2k's full list
    ERROR_KINDS = {"stuck", "tangled", "motor_stalled", "timeout", "unload_incomplete", "runout"}
    NOTICE_KINDS = ("behind", "snag", "tail")  # never a move's end

    def _following(self, lane):
        try:
            return self._lane_status(lane).get("mode") == "following"
        except Exception:
            logging.exception("ace2k_u1: lane %d status", lane + 1)
            return False

    def _in_tail(self, h, lane=None):
        """_tail on the lane's current status (lane: the tick's, already read). Never raises."""
        if lane is None:
            try:
                lane = self._lane_status(h.lane)
            except Exception:
                logging.exception("ace2k_u1: lane %d status", h.lane + 1)
                return False
        return self._tail(h, lane)

    def _tail(self, h, lane):
        """The lane is in its tail: the unit said so, or the filament is out of its bay while the
        lane's current motion is the follow — the unit always enters the tail then, and its
        notice may still be on the way. An unknown insert (None: no sensors' report) counts as
        down: the lane is left running."""
        if lane.get("tail") is True:
            return True
        return self._motion(h, lane) == "following" and lane.get("insert") is not True

    # --- the lane's current motion: the one source every stop and suppression decision reads ---

    def _ended(self, h):
        """The current motion's final event came and no report since can be believed over it."""
        if h.ended_at is None:
            return False
        if self.reactor.monotonic() >= h.ended_at + self.settle_s:
            h.ended_at = None  # a report since the end has surely come: its mode is the lane's
            return False
        return True

    def _motion(self, h, lane=None):
        """The mode of the lane's current motion, None when it runs none: the follow this
        adapter armed (before any report shows it); none once its final event came (until a
        report can be believed again); else what the last report says. Never raises."""
        if h.armed:
            return "following"
        if self._ended(h):
            return None
        if lane is None:
            try:
                lane = self._lane_status(h.lane)
            except Exception:
                logging.exception("ace2k_u1: lane %d status", h.lane + 1)
                return None
        mode = lane.get("mode")
        return None if mode in IDLE_MODES else mode

    def _current_seq(self, h):
        """The seq of the lane's current motion, when it is one this adapter started."""
        if h.armed:
            return h.assist_seq
        return h.motion_seq if h.motion_open else None

    def _started(self, h, move):
        """This adapter started a motion on the lane: it is the current one; an earlier one of
        its own still open is now an old one, whose final event, late, ends nothing current."""
        if h.motion_open and h.motion_seq is not None:
            h.old_seqs = (h.old_seqs + [h.motion_seq])[-OLD_SEQS_KEPT:]
        h.motion_seq = getattr(move, "seq", None)
        h.motion_open = h.motion_seq is not None
        h.ended_at = None
        h.grip_live = False  # a start clears the lane's grip on the unit
        h.grip_down = False  # and what the bay showed under it

    def _head_holds(self, h):
        """The head's sensor still sees the filament. A sensor that cannot be read counts as
        seeing it (the tail is bounded by the unit anyway; while it stays so, every automatic
        load of this lane is a grip — the safe side), logged once per episode. Never raises."""
        try:
            holds = bool(self._head_set(h))
        except Exception:
            if not h.head_unread:
                h.head_unread = True
                logging.exception(
                    "ace2k_u1: lane %d head sensor unreadable: counted as holding, its loads grip",
                    h.lane + 1,
                )
            return True
        h.head_unread = False
        return holds

    def _running(self, h, lane=None):
        """The lane runs a motion (_motion). Never raises (unknown: not running)."""
        return self._motion(h, lane) is not None

    def _halt(self, h, lane=None):
        """The lane's stop by this adapter. On a motion of its own still open (its seq known)
        that motion's final event answers it: a "stopped" then is not a stop by hand. Any other
        stop expects nothing — an idle lane's brings no event, and an expectation no event
        answers would take a later hand stop (of a follow armed since) for this adapter's own."""
        seq = self._current_seq(h)
        if seq is not None and self._running(h, lane):
            h.expect_stopped = True
            h.expect_seq = seq
        self.api.stop(h.lane)

    def _stop(self, h, why, lane=None):
        """Stop the lane; a failure is a notice, never an error into the U1."""
        try:
            self._halt(h, lane)  # before the disarm: an armed follow is a running lane
        except Exception as err:
            logging.exception("ace2k_u1: lane %d stop on %s", h.lane + 1, why)
            self._say(f"ace2k_u1: lane {h.lane + 1} not stopped on {why} — {err}")
        finally:
            h.armed = False

    def _arm(self, h, busy_wait_s=0.0):
        """Arm the lane's follow, once: not again while this adapter armed it and has not
        stopped it since. With busy_wait_s (a re-arm right after this adapter's own stop) a busy
        refusal is retried for that long whatever mode the last report gave, the lane winding
        down. A refusal is a notice, never an error into the U1."""
        self._cancel_eject(h.lane)  # a pending eject must not start under a new pull
        if h.waits_behind:
            # the head runs on the piece ahead; the lane's filament cannot move behind it — a
            # follow would only time out
            if not h.behind_said:  # once per episode
                h.behind_said = True
                logging.info(
                    "ace2k_u1: lane %d waits behind e%d's piece: no follow", h.lane + 1, h.head
                )
            return
        h.follow_wanted = True
        if h.armed:
            return
        n = h.lane + 1
        give_up = self.reactor.monotonic() + busy_wait_s
        while True:
            try:
                move = self.api.start_move(h.lane, "assist_both", 0.0, self.feed_speed)
                self._started(h, move)
                h.armed = True
                h.armed_at = self.reactor.monotonic()
                h.assist_seq = getattr(move, "seq", None)
                return
            except Exception as err:  # FeedRefused (reason), ValueError, RuntimeError
                busy = getattr(err, "reason", None) == "busy"
                # a head already following (load_finish set again by the U1's auto-mode
                # re-derivation) is refused busy: nothing to tell. Not after this adapter's own
                # stop (busy_wait_s): the reported mode is up to a report old and may still say
                # following while the stopped lane winds down
                if busy and not busy_wait_s and self._following(h.lane):
                    logging.info("ace2k_u1: lane %d follow already armed", n)
                    return
                if busy and self.reactor.monotonic() + EJECT_RETRY_POLL_S <= give_up:
                    self.reactor.pause(self.reactor.monotonic() + EJECT_RETRY_POLL_S)
                    continue
                self._say(f"ace2k_u1: lane {n} follow not armed — {err}")
                return

    def _cancel_eject(self, lane):
        timer = self.eject_timers.pop(lane, None)
        if timer is not None:
            self.reactor.unregister_timer(timer)

    def _schedule_eject(self, h):
        """Start the eject (the unit's unload, not waited) from a reactor timer, not from under
        the U1's lock: right after the follow's stop the unit may refuse it busy while the lane
        winds down, and it is retried every EJECT_RETRY_POLL_S for up to EJECT_RETRY_S."""
        # a lane has one completion slot: an earlier eject can no longer end, so it is replaced
        # (or dropped when this one does not start)
        self.ejects.pop(h.lane, None)
        self._cancel_eject(h.lane)
        give_up = self.reactor.monotonic() + EJECT_RETRY_S
        n = h.lane + 1

        def try_eject(eventtime):
            try:
                move = self.api.start_move(h.lane, "unload", 0.0, self.feed_speed)
            except Exception as err:
                busy = getattr(err, "reason", None) == "busy"
                if busy and eventtime + EJECT_RETRY_POLL_S <= give_up:
                    return eventtime + EJECT_RETRY_POLL_S
                self._say(f"ace2k_u1: lane {n} eject not started — {err}")
            else:
                self.ejects[h.lane] = move
                self._started(h, move)
            self._cancel_eject(h.lane)
            return self.reactor.NEVER

        self.eject_timers[h.lane] = self.reactor.register_timer(try_eject, self.reactor.monotonic())

    # --- ACE_EJECT: the filament out of its bay, whatever the head holds -------------------------

    def start_eject(self, lane):
        """ACE_EJECT LANE=lane+1: refused (EjectRefused, nothing moved) while the lane is in use
        by a print, busy or in error, with no filament in its bay, or with an eject still running
        on it; otherwise the eject's job, run from its own reactor timer. With the head's sensor
        clear: the follow off and the unit's unload (the eject after a U1 unload). With it set:
        a short rollback probes the filament — free, the same unload; held by the head's gear, the
        probe's motor travel fed back and the U1's own unload of that head started, whose end
        brings the eject."""
        n = lane + 1
        h = next((x for x in self.hooked if x.lane == lane), None)
        if h is None:
            raise EjectRefused(f"ace2k_u1: lane {n} eject refused: the lane is not hooked")
        why = self._eject_refusal(h)
        if why is not None:
            raise EjectRefused(f"ace2k_u1: lane {n} eject refused: {why}")
        job = EjectJob(h)
        self.eject_jobs[lane] = job
        job.timer = self.reactor.register_timer(
            lambda eventtime, job=job: self._run_eject(job, eventtime), self.reactor.NOW
        )
        return job

    def _eject_refusal(self, h):
        """Why ACE_EJECT may not start on the lane now, or None."""
        n = h.lane + 1
        if h.lane in self.eject_jobs or h.lane in self.eject_timers or h.lane in self.ejects:
            return "an eject is already running"  # the last: the unit's unload still in flight
        try:
            lane = self._lane_status(h.lane)
        except Exception as err:
            logging.exception("ace2k_u1: lane %d status", n)
            return f"the lane's status cannot be read — {err}"
        mode = lane.get("mode")
        idle = mode in (None, "idle") and not h.armed
        if self._printing() and (not idle or self._head_holds(h)):
            return "in use by the print"
        if mode == "error":
            return f"the lane is in error — ACE_CLEAR LANE={n} first"
        if not idle and not h.armed and mode not in ARMED_MODES:
            return f"the lane is busy ({mode})"
        if lane.get("insert") is not True:
            return "no filament in the bay"
        if self._u1_flow(h):
            return "the U1 is loading/unloading this head"
        return None

    def _u1_flow(self, h):
        """The U1 runs one of its flows on the head's channel: the channel taken (its active
        one), or its state not one of EJECT_REST_STATES. Never raises: a channel that cannot be
        read counts as one (the eject refused, the safe side)."""
        try:
            if h.ff.channel_active == h.ch:
                return True
            m = self.module
            rest = {getattr(m, name) for name in EJECT_REST_STATES if hasattr(m, name)}
            return h.ff.channel_state[h.ch] not in rest
        except Exception:
            logging.exception("ace2k_u1: lane %d channel state", h.lane + 1)
            return True

    def _eject_recheck(self, job):
        """Before each step that moves (the probe, the feed back, the U1's unload): a print
        started since the command, or the U1 started a flow on the head — the eject fails ("why"
        back); the head's sensor clear by now — "empty" (the unit's plain unload); else None."""
        if self._printing():
            return "a print started"
        if self._u1_flow(job.h):
            return "the U1 is loading/unloading this head"
        if not self._head_holds(job.h):
            return "empty"
        return None

    def _eject_rechecked(self, job):
        """_eject_recheck acted on: the waketime when the eject ended, else None (go on)."""
        why = self._eject_recheck(job)
        if why is None:
            return None
        if why == "empty":
            self._say(EJECT_SAY["head_now_empty"].format(n=job.h.lane + 1))
            return self._eject_unload(job)
        # a print or a U1 flow on a head the eject took the follow from: the follow given back,
        # the lane known at rest here (nothing moved yet, or the feed back ended done/blocked)
        self._eject_restore_follow(job)
        return self._eject_fail(job, why)

    def _eject_restore_follow(self, job):
        """The follow the eject stopped, armed again as the standing re-arm would: only when the
        lane had it (wanted or armed at begin), the U1's channel holds the head loaded
        (_loaded_states, as the standing re-arm checks), the head's sensor still sees filament,
        and the lane is at rest with no error and a filament in its bay (a report may still show
        the follow the eject stopped: the busy refusal of its winding down is waited out). The
        hold stays on across the arm, so the standing re-arm cannot run inside its busy pause.
        Never raises."""
        h = job.h
        if not job.had_follow:
            return
        try:
            if h.ff.channel_state[h.ch] not in self._loaded_states():
                return
            lane = self._lane_status(h.lane)
            mode = lane.get("mode")
            at_rest = mode in (None, "idle") or mode in ARMED_MODES
            if not (
                at_rest
                and lane.get("error") is None
                and lane.get("insert") is True
                and self._head_holds(h)
            ):
                return
            h.hold = True
            try:
                self._arm(h, busy_wait_s=NUDGE_BUSY_S)
            finally:
                h.hold = False
        except Exception:
            logging.exception("ace2k_u1: lane %d follow after the eject", h.lane + 1)

    def wait_eject(self, job):
        """ACE_EJECT WAIT=1: until the eject's decision (never the U1's unload, which waits for
        the G-code lock this command holds), at most EJECT_WAIT_MAX_S."""
        give_up = self.reactor.monotonic() + EJECT_WAIT_MAX_S
        while not job.decided and self.reactor.monotonic() < give_up:
            self.reactor.pause(self.reactor.monotonic() + PUSH_POLL_S)
        if not job.decided:
            self._say(EJECT_SAY["no_decision"].format(n=job.h.lane + 1, s=EJECT_WAIT_MAX_S))

    def _run_eject(self, job, eventtime):
        """The eject's timer. Never raises: anything unexpected ends the eject with its line. The
        U1's unload runs through run_script, which waits for the G-code lock: the reactor may run
        this timer again meanwhile — the re-entered call leaves at once."""
        if job.running:
            return self.reactor.NEVER
        job.running = True
        try:
            return self._eject_step(job, eventtime)
        except Exception as err:
            logging.exception("ace2k_u1: lane %d eject", job.h.lane + 1)
            return self._eject_fail(job, str(err) or type(err).__name__)
        finally:
            job.running = False

    def _eject_speed(self):
        """eject_probe_speed within the unit's speed bounds (when ace2k gives them)."""
        speed = self.eject_probe_speed
        low = getattr(self.api, "speed_min", None)
        high = getattr(self.api, "speed_max", None)
        if isinstance(low, (int, float)) and speed < low:
            speed = low
        if isinstance(high, (int, float)) and speed > high:
            speed = high
        return speed

    def _eject_start(self, job, mode, length, now):
        """Start the probe or the feed back: ("started", move), ("retry", None) while a busy
        refusal (the stopped follow winding down) may still be retried, or ("failed", why)."""
        try:
            move = self.api.start_move(job.h.lane, mode, length, self._eject_speed())
        except Exception as err:  # FeedRefused (reason), ValueError, RuntimeError
            busy = getattr(err, "reason", None) == "busy"
            if busy and now + EJECT_RETRY_POLL_S <= job.give_up:
                return "retry", None
            return "failed", str(err) or type(err).__name__
        self._started(job.h, move)
        job.move = move
        job.deadline = now + length / self._eject_speed() * 2.0 + EJECT_MARGIN_S
        return "started", move

    def _eject_step(self, job, now):
        h = job.h
        n = h.lane + 1
        if job.step == "begin":
            job.had_follow = h.follow_wanted or h.armed
            h.follow_wanted = False  # the eject ends the follow's standing
            h.hold = True  # and keeps the standing re-arm off while it runs the lane
            lane = self._lane_status(h.lane)
            if h.armed or lane.get("mode") in ARMED_MODES:
                self._stop(h, "the eject", lane)
            if not self._head_holds(h):
                self._say(EJECT_SAY["head_empty"].format(n=n))
                return self._eject_unload(job)
            job.step = "probe"
            job.give_up = now + EJECT_RETRY_S
        if job.step == "probe":
            ended = self._eject_rechecked(job)
            if ended is not None:
                return ended
            state, got = self._eject_start(job, "rollback", self.eject_probe_mm, now)
            if state == "retry":
                return now + EJECT_RETRY_POLL_S
            if state == "failed":
                return self._eject_fail(job, f"the probe not started — {got}")
            job.step = "probe_wait"
            return now + PUSH_POLL_S
        if job.step == "probe_wait":
            event = job.move.result()
            if event is None:
                if now < job.deadline:
                    return now + PUSH_POLL_S
                self._stop(h, "the probe's timeout")
                return self._eject_fail(job, "the probe got no end")
            return self._eject_judge(job, event, now)
        if job.step == "settle":
            if now < job.settle_at:
                return job.settle_at
            return self._eject_settled(job, now)
        if job.step == "back":
            ended = self._eject_rechecked(job)
            if ended is not None:
                return ended
            state, got = self._eject_start(job, "feed", job.back_mm, now)
            if state == "retry":
                return now + EJECT_RETRY_POLL_S
            if state == "failed":
                return self._eject_fail(job, f"the feed back not started — {got}")
            job.step = "back_wait"
            return now + PUSH_POLL_S
        if job.step == "back_wait":
            event = job.move.result()
            if event is None:
                if now < job.deadline:
                    return now + PUSH_POLL_S
                self._stop(h, "the feed back's timeout")
                return self._eject_fail(job, "the feed back got no end")
            kind = event.get("kind")
            if kind not in EJECT_FREE_KINDS and kind not in LANE_ERROR_KINDS:
                return self._eject_fail(job, f"the feed back ended {kind}")
            if kind in LANE_ERROR_KINDS:
                self.api.clear(h.lane)
            # the plunger read after the same settle as the probe's: a report from before the
            # feed back's end is no reading of where it left the buffer
            job.step = "back_settle"
            job.settle_at = now + self.eject_settle_s
            return job.settle_at
        if job.step == "back_settle":
            if now < job.settle_at:
                return job.settle_at
            if not self._plunger_at_rest(h):
                # the U1's unload arms the follow at its tip forming, which settles the buffer
                self._say(EJECT_SAY["plunger_not_at_rest"].format(n=n))
            job.step = "u1"
        if job.step == "u1":
            return self._eject_u1(job)
        return self.reactor.NEVER

    def _eject_judge(self, job, event, now):
        """The probe's end: a stop (or any end but done, blocked or a lane error) and a travel
        that is no number end the eject; a lane error is held at once; done or blocked waits
        eject_settle_s for the plunger (_eject_settled)."""
        kind = event.get("kind")
        travel = event.get("filament_mm")
        if (
            isinstance(travel, bool)
            or not isinstance(travel, (int, float))
            or not math.isfinite(travel)
        ):
            return self._eject_fail(job, "the probe reported no filament travel")
        if kind not in EJECT_FREE_KINDS and kind not in LANE_ERROR_KINDS:
            return self._eject_fail(job, f"the probe ended {kind}")
        job.probe_event = event
        if kind in LANE_ERROR_KINDS:
            return self._eject_held(job, "the probe met resistance", now)
        job.step = "settle"
        job.settle_at = now + self.eject_settle_s
        return job.settle_at

    def _plunger_at_rest(self, h):
        """The lane's buffer plunger at its rest position (rest, not pushed), as ace2k reports
        it. A report that cannot be read is not at rest, nor is one whose freshness cannot be
        trusted: ace2k does not expose when its last sensors report arrived, so the reading is
        taken only on a proven link — the unit then sends its switches unasked at 10 Hz, and
        the settle (at least 200 ms) spans two reports or more after the motion's end. Without
        it the reading may predate the probe: held, the safe side."""
        try:
            status = self.ace2k.get_status(self.reactor.monotonic())
            lane = status["lanes"][h.lane]
        except Exception:
            logging.exception("ace2k_u1: lane %d status", h.lane + 1)
            return False
        if status.get("link_proven") is not True:
            return False
        return lane.get("rest") is True and lane.get("pushed") is not True

    def _eject_settled(self, job, now):
        """The plunger after the settle: at rest, the filament came back whole — free, the unit's
        unload; moved (pushed, or off its rest), the head's gear held the tip and the buffer
        took the probe up — held. A print or a U1 flow started during the settle, or the head
        cleared, is acted on first (_eject_rechecked)."""
        ended = self._eject_rechecked(job)
        if ended is not None:
            return ended
        n = job.h.lane + 1
        if self._plunger_at_rest(job.h):
            self._say(EJECT_SAY["filament_free"].format(n=n))
            return self._eject_unload(job)
        return self._eject_held(job, "plunger moved", now)

    def _eject_held(self, job, why, now):
        """Held by the head: the lane cleared when the probe ended in error, the probe's motor
        travel fed back, then the U1's unload."""
        h = job.h
        event = job.probe_event
        self._say(EJECT_SAY["held"].format(n=h.lane + 1, why=why))
        if event.get("kind") in LANE_ERROR_KINDS:
            self.api.clear(h.lane)
        motor = event.get("motor_mm")
        motor = motor if isinstance(motor, (int, float)) and math.isfinite(motor) else 0.0
        job.back_mm = min(abs(motor), self.api.move_max_mm)
        if job.back_mm < FEED_BACK_MIN_MM:
            job.step = "u1"
            return self._eject_u1(job)
        job.step = "back"
        job.give_up = now + EJECT_RETRY_S
        return now

    def _eject_unload(self, job):
        """The unit's unload of the lane: the eject after a U1 unload (busy retried)."""
        job.h.hold = False
        self._schedule_eject(job.h)
        self._eject_end(job)
        return self.reactor.NEVER

    def _eject_u1(self, job):
        """The U1's own unload of the head, from this timer (its G-code waits for the lock: a
        WAIT=1 ACE_EJECT has returned by then, the decision taken). Its unload_finish brings the
        eject; a failure of it is the eject's line."""
        h = job.h
        ended = self._eject_rechecked(job)
        if ended is not None:
            return ended
        job.step = "done"
        job.decided = True
        h.hold = False
        try:
            if self.gcode is None:
                raise RuntimeError("no G-code object")
            self.gcode.run_script(U1_UNLOAD_SCRIPT.format(head=h.head))
        except Exception as err:
            return self._eject_fail(job, f"the U1's unload of e{h.head} — {err}")
        self._eject_end(job)
        return self.reactor.NEVER

    def _eject_fail(self, job, why):
        """The eject ends short: its line. A print or a U1 flow found at a recheck has already
        given the follow back where it was and the head is loaded (_eject_restore_follow): the
        line says so. Any other failure (a probe or feed back refused, stopped or with no end)
        leaves the follow off — the lane's state then is not known well enough to pull on the
        filament — and a head still loaded gets it back at its next load (its load_finish arms
        it), which the line says."""
        job.failed = why
        h = job.h
        line = f"ace2k_u1: lane {h.lane + 1} eject failed: {why}"
        if h.armed:
            line += "; its follow armed again"
        elif self._head_holds(h):
            line += f"; the follow stays off until e{h.head}'s next load"
        self._say(line)
        self._eject_end(job)
        return self.reactor.NEVER

    def _eject_end(self, job):
        job.step = "done"
        job.decided = True
        job.h.hold = False
        if self.eject_jobs.get(job.h.lane) is job:
            del self.eject_jobs[job.h.lane]
        if job.timer is not None:
            self.reactor.unregister_timer(job.timer)
            job.timer = None

    def _hook_states(self, ff, by_ch):
        """The U1's channel states, kept, plus on hooked channels: the follow armed from the
        U1's extrusion on (any of PULL_STATES: its extruder moves the filament then, the unload's
        tip forming included) and on a loaded head, kept through the unload's steps; the lane
        stopped when anything fails and when an unload ends, the eject (the unit's unload, not
        waited) following that stop. A head leaving load_finish by any other way (a head runout,
        the manual feed's finish) stops the lane too. Called from under the U1's lock: never
        raises."""
        orig = ff._set_channel_state
        m = self.module
        pull = {getattr(m, name) for name in PULL_STATES}
        keep = pull | {getattr(m, name) for name in UNLOAD_STEPS}

        def set_channel_state(channel, state, save=False):
            prev = ff.channel_state[channel]
            orig(channel, state, save)
            h = by_ch.get(channel)
            if h is None:
                return
            if state == m.FEED_STA_LOAD_FINISH:
                self._end_hold(h)
            if state == m.FEED_STA_LOAD_FINISH or state in pull:
                self._arm(h)
            elif state.endswith("_fail"):
                h.follow_wanted = False
                self._cancel_eject(h.lane)
                self._stop(h, state)
            elif state == m.FEED_STA_UNLOAD_FINISH:
                h.follow_wanted = False
                self._stop(h, state)
                self._schedule_eject(h)
            elif prev == m.FEED_STA_LOAD_FINISH and state not in keep:
                # the head left load_finish: no follow on it — but for the tail, whose filament
                # left the bay (the U1 told so goes to wait_insert) and still feeds the head
                if self._in_tail(h) and self._head_holds(h):
                    logging.info("ace2k_u1: lane %d in its tail: the follow kept", h.lane + 1)
                else:
                    self._stop(h, state)

        ff._set_channel_state = set_channel_state

    def _hook_nudge(self, ff, by_ch):
        """The U1's extrude retry: in load_extruding, when its check says the filament did not
        extrude, the U1 pulses its side motor forward (run_one_cycle in the channel's feed
        direction) to push the filament into the extruder's gears. On a hooked channel that motor
        is not in the filament's path: the pulse becomes the lane's retry feed. The channel is the
        U1's own active one, the state its own; the direction only tells the retry's forward
        pulse from the hang-neutral pulse, reversed (which, on a hooked channel, the adapter
        already keeps from the motor). Every other pulse reaches the U1's motor unchanged."""
        m = self.module
        motor = ff.motor
        orig = motor.run_one_cycle
        forward = {m.FEED_CHANNEL_1: m.FEED_MOTOR_DIR_A, m.FEED_CHANNEL_2: m.FEED_MOTOR_DIR_B}

        def run_one_cycle(dir, value, time):  # the U1's own parameter names
            ch = ff.channel_active
            h = by_ch.get(ch)
            if (
                h is None
                or ff.channel_state[ch] != m.FEED_STA_LOAD_EXTRUDING
                or dir != forward.get(ch)
            ):
                return orig(dir, value, time)
            if h.waits_behind:
                return None  # the lane's filament waits behind the head's piece: not pushed
            return self.nudge(h)

        motor.run_one_cycle = run_one_cycle

    def nudge(self, h):
        """The extrude retry on a hooked channel: the lane's follow stopped, a feed of
        retry_push_mm at feed_speed run to its end (bounded), the follow armed again whatever
        became of the feed. A stop that fails leaves the follow running: the retry is then
        skipped, nothing started or re-armed, and the U1 gets its loop back at once. Called from
        the U1's load loop: never raises."""
        n = h.lane + 1
        try:
            self._halt(h)
        except Exception as err:
            logging.exception("ace2k_u1: lane %d stop for the extrude retry", n)
            self._say(
                f"ace2k_u1: lane {n} not stopped for the extrude retry — {err};"
                " retry push skipped, the follow left running"
            )
            return
        h.armed = False
        h.hold = True  # the standing re-arm keeps off the lane while the retry runs it
        try:
            try:
                self._feed_and_wait(h)
            except Exception as err:
                logging.exception("ace2k_u1: lane %d retry push", n)
                self._say(f"ace2k_u1: lane {n} retry push failed — {err}")
            try:
                self._arm(h, busy_wait_s=NUDGE_BUSY_S)
            except Exception as err:
                logging.exception("ace2k_u1: lane %d re-arm after the retry push", n)
                self._say(f"ace2k_u1: lane {n} follow not armed — {err}")
        finally:
            h.hold = False

    def _feed_and_wait(self, h):
        """The retry's feed: started (a busy refusal, the stopped follow winding down, retried
        for NUDGE_BUSY_S), then waited for its final event; past the wait it is stopped. A
        refusal, a timeout and an end other than done are console lines."""
        n = h.lane + 1
        give_up = self.reactor.monotonic() + NUDGE_BUSY_S
        while True:
            try:
                move = self.api.start_move(h.lane, "feed", self.retry_push_mm, self.feed_speed)
                self._started(h, move)
                break
            except Exception as err:  # FeedRefused (reason), ValueError, RuntimeError
                busy = getattr(err, "reason", None) == "busy"
                if busy and self.reactor.monotonic() + EJECT_RETRY_POLL_S <= give_up:
                    self.reactor.pause(self.reactor.monotonic() + EJECT_RETRY_POLL_S)
                    continue
                self._say(f"ace2k_u1: lane {n} retry push not started — {err}")
                return
        deadline = (
            self.reactor.monotonic() + self.retry_push_mm / self.feed_speed * 2.0 + NUDGE_MARGIN_S
        )
        while move.result() is None:
            now = self.reactor.pause(self.reactor.monotonic() + PUSH_POLL_S)
            if move.result() is None and now >= deadline:
                self._say(f"ace2k_u1: lane {n} retry push timed out")
                self._stop(h, "the retry push's timeout")
                return
        kind = move.result().get("kind")
        if kind != "done":
            self._say(f"ace2k_u1: lane {n} retry push ended {kind}")

    def on_feed_event(self, lane, event):
        """The "ace2k:feed_event" handler: a follow (or an assist) the unit ended is no longer
        counted as armed (a later pull state, load_finish or the standing re-arm arms it again);
        a stop this adapter did not cause (by hand) ends the follow's standing; a lane error in
        the follow pauses a print; the tail notice is told; the tail's end (tail_out) is told,
        ends the follow's standing, and pauses a print (runout_source: unit); an eject that
        ended in error is a console notice, any other end of it is silent. Other events are not
        this adapter's. Never raises."""
        try:
            self._on_feed_event(lane, event)
        except Exception:
            logging.exception("ace2k_u1: lane %d feed event", lane + 1)

    def _on_feed_event(self, lane, event):
        kind = event.get("kind")
        if kind == "tail":
            for h in self.hooked:
                if h.lane == lane:
                    self._on_tail(h)
        if kind in self.NOTICE_KINDS:
            return
        for h in self.hooked:
            if h.lane != lane:
                continue
            seq = event.get("seq")
            # the final event of the motion this adapter stopped answers that stop (_halt expects
            # only a motion whose seq it knows); an older motion's late end does not
            stale = seq is not None and seq in h.old_seqs
            answers = h.expect_stopped and seq == h.expect_seq
            own = answers and kind == "stopped"
            if answers or (h.expect_stopped and not stale):
                h.expect_stopped = False
                h.expect_seq = None
            if stale:
                # an earlier motion of this adapter's, ended late: nothing current ends with it
                h.old_seqs.remove(seq)
                continue
            if seq is not None and seq == h.motion_seq:
                h.motion_open = False
            h.ended_at = self.reactor.monotonic()
            if (
                kind in ("loaded", "blocked")  # blocked: the grip met the old end at the gear
                and seq == 0
                and h.grip_live
                and h.grip_down
                and self._present_or_not(h)
            ):
                # the unit's own load, started by an insert edge with the grip set: the gripped
                # load, the filament at the gear — its tag asked for without motion. Judged here,
                # not on the bay watch's next tick, which a short grip may beat
                self._queue_read(h)
            h.grip_live = False  # every motion was a start or the grip's own load: none left
            h.grip_down = False
            if h.port_held:
                h.tail_ended = True  # the tail's end, whatever the next report or the insert says
            if event.get("mode") in ARMED_MODES or (seq is not None and seq == h.assist_seq):
                h.armed = False
                h.assist_seq = None
                if kind == "stopped" and not own and h.follow_wanted:
                    h.follow_wanted = False
                    self._say(f"ace2k_u1: lane {lane + 1} follow stopped by hand; left off")
            if kind in PAUSE_KINDS and event.get("mode") == "following":
                self._on_lane_error(h, kind)
            if kind == "tail_out":
                self._on_tail_out(h)
        move = self.ejects.get(lane)
        if move is None or event.get("seq") != move.seq:
            return
        del self.ejects[lane]
        if kind in self.ERROR_KINDS:
            n = lane + 1
            self._say(f"ace2k_u1: lane {n} eject failed: {kind} — ACE_CLEAR LANE={n}")

    # --- the pauses and the standing follow ----------------------------------------------------

    def setup_pause(
        self,
        print_stats,
        pause_resume,
        exception_manager,
        send_event,
        runout_source="head",
        task_config=None,
    ):
        """The U1's objects the pauses go through: print_stats (its state), pause_resume, the
        exception manager, the printer's send_event, and print_task_config (its auto
        replenish)."""
        self.print_stats = print_stats
        self.pause_resume = pause_resume
        self.exception_manager = exception_manager
        self.send_event = send_event
        self.runout_source = runout_source
        self.task_config = task_config
        if self.pause_timer is None:
            self.pause_timer = self.reactor.register_timer(self._run_pauses, self.reactor.NEVER)

    def _print_state(self):
        return getattr(self.print_stats, "state", None)

    def _on_lane_error(self, h, kind):
        """A lane error in the follow: during a print the U1's tangle pause for its head (from
        the pause timer); outside one a console line."""
        n = h.lane + 1
        if self._print_state() == "printing" and self.pause_timer is not None:
            self._say(f"ace2k_u1: lane {n} {kind} in the follow — pausing the print (e{h.head})")
            self._queue_pause("tangle", h, kind)
        else:
            self._say(f"ace2k_u1: lane {n} {kind} in the follow — ACE_CLEAR LANE={n}")

    def _on_tail(self, h):
        """The unit's tail notice: the filament left the bay while following and feeds on as its
        tail. A console line, whatever the runout source (unit pauses at the tail's end)."""
        n = h.lane + 1
        self._say(f"ace2k_u1: lane {n} ran out at the bay — its tail feeds on to e{h.head}")

    def _on_tail_out(self, h):
        """The tail's end: the filament's end is out of the unit, its gear free. The follow is
        wanted no more (a filament put in during the tail rests behind the old end, not at the
        gear: no follow for it); the operator is told, by the insert now, that a new filament can
        go in, or to put the one inserted during the tail in again; a filament in the bay is not
        told to the U1 while the old piece is in the head. runout_source: unit pauses a print
        through the U1's runout pause for the head."""
        n = h.lane + 1
        h.follow_wanted = False
        h.after_tail = True
        if self._head_holds(h):
            self._set_waits_behind(h, "its tail_out with the head's sensor set")
        try:
            inserted = self._present(h.lane)
        except Exception:  # the line, and the pause below, all the same
            logging.exception("ace2k_u1: lane %d status", n)
            inserted = False
        if inserted:
            line = (
                f"ace2k_u1: lane {n}: the end is out of the unit — pull lane {n}'s new filament"
                " back out of the bay and push it in again until the gear takes it"
            )
        else:
            line = f"ace2k_u1: lane {n}: the end is out of the unit — a new filament can go in"
        self._sync_grip(h)  # the old piece still in the head: a filament put in now is gripped
        printing = self._print_state() == "printing"
        if self.runout_source == "unit" and printing and self.pause_timer is not None:
            self._say(f"{line}; pausing the print (e{h.head})")  # one line per tail_out
            self._queue_pause("runout", h, "tail_out")
        else:
            self._say(line)

    # --- the grip --------------------------------------------------------------------------------

    def _owns_head(self, h, lane=None):
        """The lane owns what its head's sensor sees: its follow armed, its tail, or the U1's
        channel holding the head (_owned_states: loaded, preloaded, any step of a load) — never
        after its tail_out, until the U1 loads the head again. Never raises."""
        if h.after_tail or h.waits_behind:
            return False
        if h.armed or self._in_tail(h, lane):
            return True
        return self._channel_holds(h)

    def _sync_grip(self, h, holds=None, lane=None):
        """The lane's grip as the head stands: set while its head's sensor sees filament the
        lane does not own (a sensor that cannot be read counts as seeing it), cleared otherwise
        — left alone while the lane runs a motion, which ends it on the unit anyway. Sent only
        on a change, and again after the unit dropped it. A refusal is a console line."""
        if self._running(h, lane):
            return
        if holds is None:
            holds = self._head_holds(h)
        want = holds and not self._owns_head(h, lane)
        if h.grip_refused is not None and h.grip_refused != want:
            h.grip_refused = None  # the want changed: tried again
        if want == h.grip_wanted and (h.grip_live or not want) or h.grip_refused is not None:
            return
        try:
            self.api.set_lane_grip(h.lane, self.grip_mm if want else 0)
        except Exception as err:
            h.grip_refused = want  # said once, not retried every tick
            logging.exception("ace2k_u1: lane %d grip", h.lane + 1)
            what = "set" if want else "cleared"
            self._say(f"ace2k_u1: lane {h.lane + 1} grip not {what} — {err}")
            return
        h.grip_wanted = h.grip_live = want
        if not want:
            h.grip_down = False  # an empty bay seen under a grip now cleared tells nothing
        if want:
            if lane is None:
                try:
                    lane = self._lane_status(h.lane)
                except Exception:
                    logging.exception("ace2k_u1: lane %d status", h.lane + 1)
            if lane is not None and lane.get("insert") is not True:
                self._grip_bay_empty(h, lane)
        logging.info(
            "ace2k_u1: lane %d grip %s",
            h.lane + 1,
            f"{self.grip_mm:g} mm (e{h.head} holds a piece the lane does not own)"
            if want
            else "cleared",
        )

    def _grip_bay_empty(self, h, lane):
        """The bay seen empty with the grip live: the next insert's load is the gripped one; the
        tag the lane shows now is what a fresh read must differ from."""
        h.grip_down = True
        h.tag_before = tag_identity(lane.get("tag"))

    # --- a filament waiting behind a piece in the head that is not its own -----------------------

    def _set_waits_behind(self, h, why):
        """A transition put a piece that is not this lane's ahead of its filament: its follow is
        wanted no more (no standing re-arm once it clears)."""
        if not h.waits_behind:
            logging.info("ace2k_u1: lane %d waits behind e%d's piece (%s)", h.lane + 1, h.head, why)
        h.waits_behind = True
        h.follow_wanted = False
        h.clear_since = None  # a clear seen before the set is not this piece's end
        self._say_unreadable(h)

    def _clear_waits_behind(self, h, why=None):
        if h.waits_behind and why is not None:
            logging.info(
                "ace2k_u1: lane %d no longer behind e%d's piece (%s)", h.lane + 1, h.head, why
            )
        h.waits_behind = False
        h.behind_said = False

    def _channel_holds(self, h):
        """The U1's channel holds the head (_owned_states)."""
        if self.owned_states is None:
            self.owned_states = self._owned_states()  # not hooked through hook_all
        return h.ff.channel_state[h.ch] in self.owned_states

    def _watch_head_clear(self, h, holds, eventtime):
        """The head's sensor read clear for HEAD_CLEAR_S: the piece ahead has run out (a shorter
        clear is a glitch). A sensor that cannot be read, or one stuck set, never releases the
        lane: the console says ACE_CLEAR LANE=n does, once per episode."""
        if holds:
            h.clear_since = None
        elif h.clear_since is None:
            h.clear_since = eventtime
        if h.clear_since is not None and eventtime - h.clear_since >= HEAD_CLEAR_S:
            self._clear_waits_behind(h, "the head's sensor cleared")
        self._say_unreadable(h)

    def _say_unreadable(self, h):
        """While a waiting lane's head sensor cannot be read: one console line per episode."""
        if not (h.waits_behind and h.head_unread):
            h.unread_said = False
            return
        if h.unread_said:
            return
        h.unread_said = True
        n = h.lane + 1
        self._say(
            f"ace2k_u1: lane {n}: e{h.head}'s sensor cannot be read — lane {n} waits behind its"
            f" piece until the sensor reads clear, or ACE_CLEAR LANE={n}"
        )

    def on_ace_clear(self, lane):
        """ACE_CLEAR LANE=n ran (the operator's escape, e.g. a head sensor stuck set): the lane
        no longer waits behind a piece, and its holds end as a U1 load's would (_end_hold: the
        filament in the bay counted as told, no rise held again) — its next load takes the head.
        Outside a print, with the head reading clear and a filament in the bay, the U1 is told of
        it once (its preload), as a hold released outside a print is. Never raises."""
        for h in self.hooked:
            if h.lane == lane and h.waits_behind:
                try:
                    tell = (
                        not self._printing()
                        and not self._head_holds(h)
                        and not h.present
                        and self._present(h.lane)
                    )
                except Exception:
                    logging.exception("ace2k_u1: lane %d status", lane + 1)
                    tell = False
                self._clear_waits_behind(h)
                self._end_hold(h)
                if tell:
                    h.present = True
                    h.ff._port_event_handler(True, h.ch)
                self._say(
                    f"ace2k_u1: lane {lane + 1}: ACE_CLEAR — no longer counted as waiting behind"
                    f" e{h.head}'s piece; its next load takes the head"
                )

    def _head_foreign(self, h, holds=None, lane=None):
        """The head holds a piece that is not this lane's filament: after its tail_out until the
        U1 loads the head again, or whenever the head's sensor sees filament the lane does not
        own. Never raises (an unreadable sensor counts as seeing it, as for the grip)."""
        if h.after_tail:
            return True
        if holds is None:
            holds = self._head_holds(h)
        return holds and not self._owns_head(h, lane)

    def _sample_wheel(self, h, lane, eventtime, holds=None):
        """The wheel stand-in's sample. While the head holds a piece that is not this lane's
        filament, the filament in the bay waits (gripped, or held) while the extruder pulls the old
        piece: the U1's tangle check — on while its inlet reads a filament — would see a stalled
        wheel and pause the print, so the wheel follows the head's extruder then. Back on the
        lane's encoder once the lane owns the head again; each change of source re-bases the
        wheel, so its counts never jump."""
        source, value = "encoder", lane.get("encoder_mm")
        foreign = self.extruder_position is not None and self._head_foreign(h, holds, lane)
        if not foreign:
            h.position_unread = False  # a later episode's failure is logged again
        else:
            try:
                position = self.extruder_position(h.head)
            except Exception:
                if not h.position_unread:  # logged once per episode
                    h.position_unread = True
                    logging.exception("ace2k_u1: lane %d e%d position", h.lane + 1, h.head)
                if h.wheel_source == "extruder":
                    # this sample skipped, the source kept: no re-base, so the next good
                    # reading counts the travel since the last one
                    return
                position = None  # still on the encoder: it goes on sampling the encoder
            else:
                h.position_unread = False
            if position is not None:
                source, value = "extruder", position
        if source != h.wheel_source:
            logging.info(
                "ace2k_u1: lane %d wheel follows %s",
                h.lane + 1,
                f"e{h.head}'s extruder (its piece is not the lane's)"
                if source == "extruder"
                else "the lane's encoder",
            )
            h.wheel_source = source
            h.wheel.rebase(value, eventtime)
            return
        h.wheel.sample(value, eventtime)

    def _queue_read(self, h):
        if self.read_timer is None:
            self.read_timer = self.reactor.register_timer(self._run_reads, self.reactor.NEVER)
        if h not in self.read_pending:
            self.read_pending.append(h)
        self.reactor.update_timer(self.read_timer, self.reactor.NOW)

    def _run_reads(self, eventtime):
        """The read timer: each queued lane's read started (not waited), then every started
        read's outcome looked at — read: done, the tags apply it; past its deadline: one console
        line. Runs again every READ_POLL_S while a read is out. The reactor may run it again
        while the G-code takes the lock: the running call does the work, the re-entered one
        leaves at once."""
        if self.read_running:
            return self.reactor.NEVER
        self.read_running = True
        try:
            while self.read_pending:
                h = self.read_pending.pop(0)
                try:
                    self._start_read(h)
                except Exception:  # never out of the timer
                    logging.exception("ace2k_u1: lane %d tag read", h.lane + 1)
            now = self.reactor.monotonic()
            for lane, wait in list(self.read_waits.items()):
                try:
                    self._watch_read(wait, now)
                except Exception:  # never out of the timer
                    self.read_waits.pop(lane, None)
                    logging.exception("ace2k_u1: lane %d tag read", lane + 1)
        finally:
            self.read_running = False
        if self.read_waits:
            return self.reactor.monotonic() + READ_POLL_S
        return self.reactor.NEVER

    def _start_read(self, h):
        """The gripped spool's tag asked for in front of the antenna, not waited; a refusal (or
        no G-code object) is the console line at once."""
        try:
            if self.gcode is None:
                raise RuntimeError("no G-code object")
            self.gcode.run_script(READ_SCRIPT % (h.lane + 1))
        except Exception as err:
            self.read_waits.pop(h.lane, None)
            self._not_read(h, str(err) or type(err).__name__)
            return
        # [Hooked, deadline, the tag before, the state seen leaving "read" since the start]
        self.read_waits[h.lane] = [
            h,
            self.reactor.monotonic() + READ_DEADLINE_S,
            h.tag_before,
            False,
        ]

    def _watch_read(self, wait, now):
        """A started read: its tag read — new since the filament went in (not the record the lane
        held then), or read again after the state left "read" since the read started (the same
        spool re-read) — the tags apply it on their next tick (the active extruder's rule while
        printing); still not read at its deadline — the console line, the head keeping its
        setting."""
        h, deadline, before, _left = wait
        tag = self._lane_status(h.lane).get("tag") or {}
        if tag.get("state") != "read":
            wait[3] = True  # searching, reading, pending: a "read" after it is this read's
        fresh = before[0] != "read" or tag_identity(tag) != before or wait[3]
        if tag.get("state") == "read" and tag.get("record") and fresh:
            del self.read_waits[h.lane]
            return
        if now >= deadline:
            del self.read_waits[h.lane]
            stale = tag.get("state") == "read" and not fresh
            self._not_read(
                h, "the earlier record only" if stale else f"tag {tag.get('state') or 'unknown'}"
            )

    def _not_read(self, h, why):
        n = h.lane + 1
        self._say(
            f"ace2k_u1: lane {n}: the gripped spool's tag was not read ({why}) — e{h.head} keeps"
            f" its filament setting until a load at idle or ACE_RFID_READ LANE={n} MOVE=1"
        )

    def _end_hold(self, h):
        """The U1 loaded the head itself (its replenish, or a load from its screen, both reading
        the bay): any hold of the bay's insert is over, and a filament in the bay is the one it
        loaded — already told, never raised again as an insert. Not while the lane's filament waits
        behind the piece the U1's load used instead (waits_behind). Never raises."""
        if h.waits_behind:
            return
        h.after_tail = False
        h.rise_held = False
        if h.present:
            return
        try:
            h.present = self._present(h.lane)
        except Exception:
            logging.exception("ace2k_u1: lane %d status", h.lane + 1)
            h.present = True  # a filament the U1 just loaded: never raised as an insert

    def _queue_pause(self, path, h, kind):
        self.pause_pending.append((path, h, kind))
        self.reactor.update_timer(self.pause_timer, self.reactor.NOW)

    def _run_pauses(self, eventtime):
        """The pause timer: each queued pause in turn. A print no longer printing (paused by an
        earlier one, or ended) is a console line instead; nothing is raised. While a pause's
        G-code waits the reactor may run this timer again: the running call drains the queue,
        so the re-entered one leaves at once."""
        if self.pause_running:
            return self.reactor.NEVER
        self.pause_running = True
        try:
            self._drain_pauses()
        finally:
            self.pause_running = False
        return self.reactor.NEVER

    def _drain_pauses(self):
        while self.pause_pending:
            path, h, kind = self.pause_pending.pop(0)
            n = h.lane + 1
            try:
                if self._print_state() != "printing":
                    self._say(
                        f"ace2k_u1: lane {n} {kind}: the print is {self._print_state()},"
                        " not paused again"
                    )
                elif path == "tangle":
                    self._tangle_pause(h)
                else:
                    self._runout_pause(h)
            except Exception as err:
                logging.exception("ace2k_u1: lane %d pause", n)
                self._say(f"ace2k_u1: lane {n} {kind}: the pause failed — {err}")

    def _tangle_pause(self, h):
        """The U1's tangle pause for the head, as its own filament tangle detection raises it."""
        if self.send_event is not None:
            self.send_event(
                "print_stats:update_exception_info",
                TANGLE_ID,
                h.head,
                TANGLE_CODE,
                TANGLE_MESSAGE,
                TANGLE_LEVEL,
            )
        if self.pause_resume is not None:
            self.pause_resume.send_pause_command()
        if self.send_event is not None:
            self.send_event(TANGLE_EVENT, h.head)
        if self.exception_manager is not None:
            self.exception_manager.raise_exception_async(
                id=TANGLE_ID,
                index=h.head,
                code=TANGLE_CODE,
                message=TANGLE_MESSAGE,
                oneshot=1,
                level=TANGLE_LEVEL,
            )
        self.gcode.run_script(TANGLE_SCRIPT)

    def _runout_pause(self, h):
        """The U1's runout pause for the head, as its head sensor's runout raises it: nothing
        with the sensor disabled; the head's filament backed up; a line instead with its pause
        on runout off; then the exception, the pause command, its pause delay, PAUSE
        IS_RUNOUT=1, the auto replenish, and the runout exception when no replenish took the
        print over."""
        n = h.lane + 1
        cfg = self.task_config
        helper = getattr(h.ff.runout_sensor[h.ch], "runout_helper", None)
        if not getattr(helper, "sensor_enabled", True):
            self._say(f"ace2k_u1: lane {n} ran out; e{h.head}'s sensor is disabled: not paused")
            return
        if cfg is not None and callable(getattr(cfg, "backup_filament_info", None)):
            cfg.backup_filament_info(h.head)
        if not getattr(helper, "runout_pause", True):
            self._say(f"ace2k_u1: lane {n} ran out; e{h.head}'s pause on runout is off")
            return
        if cfg is not None and getattr(cfg, "is_exec_print_end_action", False):
            self._say(f"ace2k_u1: lane {n} ran out during the print's end; not paused")
            return
        name = getattr(helper, "name", None) or RUNOUT_SENSOR % h.head
        delay = getattr(helper, "pause_delay", RUNOUT_PAUSE_DELAY_S)
        message = f"{name} runout"
        if self.send_event is not None:
            self.send_event(
                "print_stats:update_exception_info",
                RUNOUT_ID,
                h.head,
                RUNOUT_CODE,
                message,
                RUNOUT_LEVEL,
            )
        if self.pause_resume is not None:
            self.pause_resume.send_pause_command()
        self.reactor.pause(self.reactor.monotonic() + delay)
        try:  # as the U1's: a failing PAUSE script is logged, the replenish and exception follow
            self.gcode.run_script(RUNOUT_SCRIPT)
        except Exception:
            logging.exception("ace2k_u1: lane %d runout pause", h.lane + 1)
        try:
            self.gcode.run_script(REPLENISH_SCRIPT % h.head)
        except Exception:
            logging.exception("ace2k_u1: lane %d auto replenish", h.lane + 1)
        replenished = cfg is not None and getattr(cfg, "perform_auto_replenish", False)
        if not replenished and self.exception_manager is not None:
            self.exception_manager.raise_exception_async(
                id=RUNOUT_ID,
                index=h.head,
                code=RUNOUT_CODE,
                message=message,
                oneshot=0,
                level=RUNOUT_LEVEL,
            )

    def _owned_states(self):
        """The U1's channel states in which the head's filament is this lane's: loaded, in an
        unload's steps, preloaded, or anywhere in a load or a manual feed (failed included)."""
        m = self.module
        flows = {getattr(m, n) for n in dir(m) if n.startswith(OWNED_PREFIXES)}
        return self._loaded_states() | {getattr(m, n) for n in PRELOAD_STATES} | flows

    def _loaded_states(self):
        """The U1's channel states of a loaded head: built once (at the hook, or at the first
        use when not hooked through hook_all), read on every tick."""
        if self.loaded_states is None:
            m = self.module
            names = ("FEED_STA_LOAD_FINISH",) + PULL_STATES + UNLOAD_STEPS
            self.loaded_states = {getattr(m, name) for name in names}
        return self.loaded_states

    def _clear_on_resume(self, h, lane):
        """The U1 resumed: a wanted lane in error is cleared; the standing re-arm follows."""
        if lane.get("mode") != "error" or not h.follow_wanted:
            return
        n = h.lane + 1
        try:
            self.api.clear(h.lane)
        except Exception as err:
            logging.exception("ace2k_u1: lane %d clear on the resume", n)
            self._say(f"ace2k_u1: lane {n} not cleared on the resume — {err}")
            return
        self._say(f"ace2k_u1: lane {n} cleared on the resume; its follow is armed again")

    def _watch_follow(self, h, lane):
        """The standing follow: a wanted lane idle with no error and a filament in its bay, on a
        head the U1 holds loaded, is armed again — once per idle episode."""
        if lane.get("mode") != "idle":
            h.rearm_tried = False
            return
        if h.rearm_tried or h.armed or h.hold or not h.follow_wanted or h.waits_behind:
            return
        if lane.get("error") is not None:
            return
        if lane.get("insert") is not True:  # an idle lane has no tail (the unit ends it)
            return
        if h.ff.channel_state[h.ch] not in self._loaded_states():
            return
        h.rearm_tried = True
        self._arm(h)

    def watch(self, eventtime):
        """The bay watch: the wheels' samples, the U1's port events on a change, the follow
        stopped when a filament leaves its bay outside the tail, the clear on the U1's resume,
        the standing follow, the tags' decisions. Returns the next waketime."""
        status = self.ace2k.get_status(eventtime)["lanes"]
        state = self._print_state()
        resumed = self.last_print_state == "paused" and state == "printing"
        self.last_print_state = state
        for h in self.hooked:
            lane = status[h.lane]
            if lane.get("mode") in IDLE_MODES:
                h.ended_at = None  # the report has caught up with the last final event
                if h.armed and eventtime >= h.armed_at + self.settle_s:
                    # a follow reported idle well after its arm, its final event never seen
                    logging.warning("ace2k_u1: lane %d follow gone without its event", h.lane + 1)
                    h.armed = False
                    h.assist_seq = None
            holds = self._head_holds(h)  # one reading of the head's sensor per tick
            self._sample_wheel(h, lane, eventtime, holds)
            if h.grip_live and lane.get("insert") is not True:
                self._grip_bay_empty(h, lane)
            self._watch_lane(h, lane, eventtime, holds)
            try:
                self._watch_head_clear(h, holds, eventtime)
                self._sync_grip(h, holds, lane)
            except Exception:  # never into the bay watch
                logging.exception("ace2k_u1: lane %d grip", h.lane + 1)
            try:
                if resumed:
                    self._clear_on_resume(h, lane)
                self._watch_follow(h, lane)
            except Exception:  # never into the bay watch
                logging.exception("ace2k_u1: lane %d follow", h.lane + 1)
        if self.tags is not None:
            try:
                self._watch_tags(status, eventtime)
            except Exception:  # never into the bay watch
                logging.exception("ace2k_u1: tags")
        return eventtime + self.poll_s

    # --- the tags ---------------------------------------------------------------------------

    def setup_tags(self, ptc, fparams, print_stats, enabled=True, toolhead=None):
        """Turn the tags on after the hooks: the U1's command and status checked, its filament
        types read. Returns why they are off, or None. Never raises."""
        self.tags = None
        if not enabled:
            self.tags_off = "apply_tags: False"
            return self.tags_off
        try:
            problems = ["[print_task_config] absent"] if ptc is None else check_tags(ptc)
            types_ = known_types(fparams) if fparams is not None and not problems else set()
            if not problems and not types_:
                problems = ["no filament types in [filament_parameters]"]
        except Exception as err:
            logging.exception("ace2k_u1: tags setup")
            problems = [str(err)]
        if problems:
            self.tags_off = "; ".join(problems)
            self._say(f"ace2k_u1: tags not applied: {self.tags_off}")
            return self.tags_off
        self.ptc, self.print_stats, self.toolhead = ptc, print_stats, toolhead
        self.tags = tags_module.TagPolicy({h.lane: h.head for h in self.hooked}, types_)
        self.tags_off = None
        if self.tag_timer is None:
            self.tag_timer = self.reactor.register_timer(self._run_tags, self.reactor.NEVER)
        return None

    def _printing(self):
        state = getattr(self.print_stats, "state", None)
        return state in PRINTING_STATES

    def _active_head(self):
        """The active extruder's head index; None when it cannot be told."""
        try:
            name = self.toolhead.get_extruder().get_name()
        except Exception:
            return None
        heads = {v: k for k, v in (self.extruders or {}).items()}
        return heads.get(name)

    def _held(self):
        """The heads whose writes wait: the active extruder's while printing (not paused) —
        every head when the active one cannot be told."""
        if getattr(self.print_stats, "state", None) != "printing":
            return set()
        active = self._active_head()
        return {h.head for h in self.hooked} if active is None else {active}

    def _watch_tags(self, status, eventtime):
        """The tags' decisions for this tick; the G-code is queued for the tag timer. A tick
        whose head setting cannot be read is skipped: the policy reads None as a value. A lane
        that fails is logged and the others go on; what is queued is always scheduled."""
        try:
            ptc_status = self.ptc.get_status(eventtime)
            printing = self._printing()
            held = self._held()
            for h in sorted(self.hooked, key=lambda h: h.lane):
                try:
                    self._watch_tag(h, status[h.lane], ptc_status, printing, h.head in held)
                except Exception:
                    logging.exception("ace2k_u1: lane %d tag", h.lane + 1)
        except Exception:
            logging.exception("ace2k_u1: tags")
        finally:
            if self.tag_pending:
                self.reactor.update_timer(self.tag_timer, self.reactor.NOW)

    def _watch_tag(self, h, lane, ptc_status, printing, hold):
        if h.lane in self.tag_pending:  # its last action has not run yet
            return
        current = head_setting(ptc_status, h.head)
        if current is None:
            return
        tag = lane.get("tag") or {}
        action = self.tags.on_tick(
            h.lane,
            lane.get("insert") is True,
            tag.get("state"),
            tag.get("record"),
            printing,
            current,
            hold,
        )
        if action is None:
            return
        if action[0] == "skip":
            self.applied[h.lane] = action[2].reason
            self._say(f"ace2k_u1: lane {h.lane + 1} tag not applied: {action[2].reason}")
            return
        self.tag_pending[h.lane] = action

    def _run_tags(self, eventtime):
        """The tag timer: each queued write or clear as one G-code. A lane stays queued until
        its G-code has run (run_script may wait for the G-code lock while the bay watch goes
        on), so the policy hears of a refusal before that lane's next decision. While one call
        waits for the lock the reactor may run this timer again: a lane being run is left to
        the call running it, so each action runs once. A refusal is a console line; nothing is
        raised."""
        try:
            while True:
                ready = [lane for lane in self.tag_pending if lane not in self.tag_running]
                if not ready:
                    break
                lane = min(ready)
                self.tag_running.add(lane)
                try:
                    self._run_tag(lane, self.tag_pending[lane])
                except Exception:  # never out of the timer
                    logging.exception("ace2k_u1: lane %d tag", lane + 1)
                finally:
                    self.tag_running.discard(lane)
                    self.tag_pending.pop(lane, None)
        except Exception:  # never out of the timer
            logging.exception("ace2k_u1: tags")
        return self.reactor.NEVER

    def _run_tag(self, lane, action):
        """One write or clear: checked first (a refusal foreseen is a console line and nothing
        sent); a refusal by the U1 itself shows as the U1 shows any G-code error, and is logged
        here. Either way the policy is told the head kept what it had."""
        n = lane + 1
        if action[0] == "write":
            head, setting = action[1], action[2]
            params, quoted, done = tag_params(head, setting), TAG_QUOTED, describe(setting)
        else:
            head, setting = action[1], None
            params, quoted, done = clear_params(head, action[2]), (), "none"
        script = format_params(params, quoted)
        problems = check_params(params, quoted)
        if problems:
            self.tags.note_failed(lane)
            reason = "; ".join(problems)
            self.applied[lane] = f"not applied — {reason}"
            self._say(f"ace2k_u1: lane {n} -> e{head}: not sent — {reason}")
            return
        try:
            self.gcode.run_script(script)
        except Exception as err:
            self.tags.note_failed(lane)
            self.applied[lane] = f"not applied — {err}"
            logging.warning("ace2k_u1: lane %d -> e%d: %s refused — %s", n, head, script, err)
            return
        self.applied[lane] = done
        if setting is not None and setting.reason:
            self._say(f"ace2k_u1: lane {n} -> e{head}: colour only — {setting.reason}")

    def status_lines(self, say):
        """ACE_ADAPTER_STATUS's lines: per hooked lane, its bay, the U1's state, the head
        sensor, the wheel, the lane's tag and what the tag last did to the head."""
        lanes = self.ace2k.get_status(self.reactor.monotonic())["lanes"]
        if self.tags_off:
            say(f"ace2k_u1: tags off: {self.tags_off}")
        for h in sorted(self.hooked, key=lambda h: h.lane):
            sensor = h.ff.runout_sensor[h.ch].get_status(0)["filament_detected"]
            say(
                f"lane {h.lane + 1} -> e{h.head}: bay={'yes' if h.present else 'no'}"
                f" state={h.ff.channel_state[h.ch]} head_sensor={sensor}"
                f" wheel={h.wheel.get_counts()}"
                f" follow={'wanted' if h.follow_wanted else 'off'}"
                f" behind={f'e{h.head}' if h.waits_behind else 'no'}"
                f" grip={self._grip_text(h)}"
                f" tag={tag_summary(lanes[h.lane].get('tag'))}"
                f" applied={self.applied.get(h.lane, 'none')}"
            )

    def _grip_text(self, h):
        """The grip for ACE_ADAPTER_STATUS: as the unit holds it (as far as this adapter knows),
        and when it is wanted but not there (consumed by its load, cleared by a start) so."""
        if h.grip_live:
            return f"{self.grip_mm:g} mm"
        return "off (wanted, to be set again)" if h.grip_wanted else "off"

    def _watch_tail_end(self, h, lane, tail, holds):
        """A tail whose end has passed the head's sensor has nothing left in the drive: the
        lane is stopped once (the U1's own runout takes the head from there) — only while its
        current motion is the follow (_motion): not once that motion has ended."""
        if not tail:
            h.tail_stopped = False
            return
        if h.tail_stopped or self._motion(h, lane) != "following":
            return
        if holds:
            return
        h.tail_stopped = True
        # the tail ends here, not at a tail_out: the follow is wanted no more, as _on_tail_out
        # has it (a filament put in during the tail rests behind the old end, not at the gear).
        # The head's sensor reads clear by now, so nothing waits behind a piece in it
        h.follow_wanted = False
        self._say(f"ace2k_u1: lane {h.lane + 1}: the tail passed e{h.head}'s sensor — stopped")
        self._stop(h, "the tail's end", lane)

    def _watch_held(self, h, lane, holds):
        """The bay's emptying held back while in the tail, told once the tail has ended (the
        lane out of the follow: its tail_out, or the stop at the head's sensor). The tail ignores
        the bay: a filament put in meanwhile changes nothing (while the lane's motion is the follow
        it is the tail's). A final event since the hold ends the tail at once: a filament in the
        bay then is told the stock way on a later tick."""
        if h.tail_ended:
            self._send_held(h)
            return
        tail = self._tail(h, lane) or self._motion(h, lane) == "following"
        self._watch_tail_end(h, lane, tail, holds)
        if tail:
            return
        self._send_held(h)

    def _send_held(self, h):
        h.port_held = False
        h.tail_ended = False
        h.present = False
        h.ff._port_event_handler(False, h.ch)

    def _watch_lane(self, h, lane, eventtime, holds):
        mode = lane.get("mode")
        if h.after_tail and not h.port_held and lane.get("insert") is not True and not holds:
            # the old piece has left the head with no filament in the bay: nothing to hold. A
            # filament in the bay is decided at its rise below, after its own checks
            h.after_tail = False
        if h.port_held:
            self._watch_held(h, lane, holds)
            if h.port_held or h.present:
                return
        if lane.get("insert") is not True:
            # a removal is told at once, but only if the U1 had been told of the filament. In the
            # tail the follow feeds on — not stopped, still wanted — and the U1 is not told of
            # the empty bay until the tail ends
            h.rise_at = None
            h.error_said = False
            h.rise_held = False  # a held insert is gone with its filament
            tail = self._tail(h, lane)
            if lane.get("insert") is False and not tail:
                h.follow_wanted = False
            if h.present:
                if tail:
                    h.port_held = True
                    h.tail_ended = False
                    self._watch_held(h, lane, holds)
                    return
                h.present = False
                # the reported mode too: a hand motion started while a fresh end hides it is
                # stopped all the same (a stop of no open motion of its own expects nothing)
                if self._motion(h, lane) in ARMED_MODES or mode in ARMED_MODES:
                    self._halt(h, lane)
                h.armed = False
                h.ff._port_event_handler(False, h.ch)
            else:
                # a tail the U1 was never told the filament of
                self._watch_tail_end(h, lane, tail, holds)
            return
        if h.present:
            return
        if h.rise_at is None or mode in BUSY_MODES:
            h.rise_at = eventtime  # the window starts when the unit's load (or its error) ends
        if mode == "error":
            if not h.error_said:
                h.error_said = True
                n = h.lane + 1
                self._say(
                    f"ace2k_u1: lane {n}: the unit's load ended in error"
                    f" — ACE_CLEAR LANE={n}, then re-insert"
                )
            return
        h.error_said = False
        if mode == "loading" or eventtime - h.rise_at < self.settle_s:
            return
        if holds:
            # the head's sensor sees filament (an old piece, or a filament behind it): no preload
            # against it — it would fail on the loaded head, in the stock U1 too
            if not h.rise_held:
                h.rise_held = True
                logging.info("ace2k_u1: lane %d waits for e%d to clear", h.lane + 1, h.head)
                if not self._channel_holds(h):
                    self._set_waits_behind(h, "a filament in the bay with the head set")
                self._sync_grip(h, holds, lane)  # set already, as the head holds a piece: kept so
            return
        held = h.rise_held or h.after_tail
        h.rise_held = False
        h.after_tail = False
        if held and self._printing():
            # in a print a held insert is dropped, never raised: the U1's own runout path (its
            # replenish load, which reads the bay) or a load from its screen brings the filament
            # to the head
            h.present = True
            logging.info("ace2k_u1: lane %d's filament left to the U1's runout", h.lane + 1)
            return
        h.present = True
        h.ff._port_event_handler(True, h.ch)


class ACE2kU1:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.lanes = [int(x) for x in config.getlist("lanes", ["1", "2", "3", "4"])]
        if any(not 1 <= lane <= LANES for lane in self.lanes):
            raise config.error("ace2k_u1: lanes must be within 1..4")
        self.feed_speed = config.getfloat("feed_speed", 70.0, above=0.0)
        self.head_budget_mm = config.getfloat("head_budget_mm", 2000.0, above=0.0)
        self.poll_s = config.getint("poll_ms", 200, minval=50, maxval=1000) / 1000.0
        self.retry_push_mm = config.getfloat("retry_push_mm", 10.0, minval=1.0, maxval=40.0)
        self.apply_tags = config.getboolean("apply_tags", True)
        self.runout_source = config.get("runout_source", "head").strip().lower()
        if self.runout_source not in RUNOUT_SOURCES:
            raise config.error("ace2k_u1: runout_source must be head or unit")
        # judged against the unit's bounds at the hook, once ace2k has read them; absent, the
        # unit's own default (the dictionary's, read by ace2k at connect)
        self.grip_mm = config.getfloat("grip_mm", None, above=0.0)
        # ACE_EJECT's probe of a filament in a loaded head
        self.eject_probe_mm = config.getfloat("eject_probe_mm", 10.0, minval=5.0, maxval=30.0)
        self.eject_probe_speed = config.getfloat("eject_probe_speed", 10.0, minval=5.0, maxval=30.0)
        self.eject_settle_ms = config.getint("eject_settle_ms", 500, minval=200, maxval=2000)
        self.adapter = None
        self.problems = None  # why nothing was hooked before an adapter could be built
        self.feed_events = False  # "ace2k:feed_event" registered (once, routed to the adapter)
        self.printer.register_event_handler("klippy:ready", self._on_ready)
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command(
            "ACE_ADAPTER_STATUS",
            self.cmd_ACE_ADAPTER_STATUS,
            desc="Report what the ace2k U1 adapter hooked",
        )
        gcode.register_command(
            "ACE_EJECT",
            self.cmd_ACE_EJECT,
            desc="Take LANE's filament out of its bay, unloading its head on the U1 first when the"
            " head holds it; WAIT=0 returns at once",
        )

    def _on_ready(self):
        self.reactor.register_timer(self._hook, self.reactor.monotonic() + HOOK_DELAY_S)

    def _hook(self, eventtime):
        ace2k = self.printer.lookup_object("ace2k", None)
        feeds = [obj for name, obj in self.printer.lookup_objects("filament_feed")]
        gcode = self.printer.lookup_object("gcode")
        if ace2k is None or not hasattr(ace2k, "feed") or not feeds:
            return self._not_hooked(gcode, ["[ace2k] or the U1 feed modules are absent"])
        if getattr(ace2k.feed, "move_max_mm", None) is None:
            # a firmware built without the feed: the host module loads but has no lane bounds
            return self._not_hooked(gcode, ["the ace2k firmware has no feed support"])
        feed = ace2k.feed
        if getattr(feed, "API_VERSION", 0) >= API_MIN and not getattr(feed, "has_follow", False):
            # known at connect, before klippy:ready: an image without it would refuse every arm
            return self._not_hooked(
                gcode, ["the unit's firmware has no follow tail; flash v0.11.0"]
            )
        if getattr(feed, "API_VERSION", 0) >= API_MIN and not getattr(feed, "has_grip", False):
            # as the follow: a filament put in behind an old piece would be chased by a full load
            return self._not_hooked(gcode, ["the unit's firmware has no grip; flash v0.11.0"])
        self.adapter = Adapter(
            self.reactor,
            feed_module,
            ace2k,
            feeds,
            self.lanes,
            self.feed_speed,
            self.head_budget_mm,
            self.poll_s,
            self.retry_push_mm,
            extruder_names(self.printer.lookup_object("extruder_list", None)),
            self.grip_mm,
            self._extruder_position_reader(),
            self.eject_probe_mm,
            self.eject_probe_speed,
            self.eject_settle_ms / 1000.0,
        )
        self.adapter.gcode = gcode
        problems = self.adapter.hook_all()
        if problems:
            return self._not_hooked(gcode, problems)
        self.adapter.gcode = gcode
        if not self.feed_events:
            self.feed_events = True
            self.printer.register_event_handler("ace2k:feed_event", self._on_feed_event)
        lanes = sorted(h.lane + 1 for h in self.adapter.hooked)
        gcode.respond_info(f"ace2k_u1: hooked lanes {lanes}")
        self.adapter.setup_pause(
            self.printer.lookup_object("print_stats", None),
            self.printer.lookup_object("pause_resume", None),
            self.printer.lookup_object("exception_manager", None),
            self.printer.send_event,
            self.runout_source,
            self.printer.lookup_object("print_task_config", None),
        )
        self._wrap_ace_clear(gcode)
        self.adapter.setup_tags(
            self.printer.lookup_object("print_task_config", None),
            self.printer.lookup_object("filament_parameters", None),
            self.printer.lookup_object("print_stats", None),
            self.apply_tags,
            self.printer.lookup_object("toolhead", None),
        )
        self.reactor.register_timer(self.adapter.watch, self.reactor.NOW)
        return self.reactor.NEVER

    def _wrap_ace_clear(self, gcode):
        """ACE_CLEAR (ace2k's) also tells the adapter: the operator's escape from a lane counted
        as waiting behind a piece. Klipper's own replacement of a command: the old handler taken
        out (register_command(name, None) hands it back), a wrapper registered with the same
        description and readiness that runs it and then — whatever it raised, a link down or a
        firmware without the feed — releases the lane, the original error raised after. Without
        the command the escape is unavailable: said in the log once."""
        help_ = getattr(gcode, "gcode_help", {}) or {}
        desc = help_.get("ACE_CLEAR")
        when_not_ready = "ACE_CLEAR" in (getattr(gcode, "base_gcode_handlers", {}) or {})
        old = gcode.register_command("ACE_CLEAR", None)
        if old is None:
            logging.warning(
                "ace2k_u1: no ACE_CLEAR to wrap: the escape from a lane waiting behind a piece"
                " is unavailable"
            )
            return
        adapter = self.adapter

        def cmd_ace_clear(gcmd):
            try:
                old(gcmd)
            finally:
                try:
                    lane = gcmd.get_int("LANE", None)
                    if lane is not None:
                        adapter.on_ace_clear(lane - 1)
                except Exception:  # never over the original's error
                    logging.exception("ace2k_u1: ACE_CLEAR's release")

        gcode.register_command("ACE_CLEAR", cmd_ace_clear, when_not_ready=when_not_ready, desc=desc)

    def _extruder_position_reader(self):
        """head index -> the head's extruder position now, as the U1's tangle check reads it
        (find_past_position at the MCU's estimated print time); None without the objects."""
        extruders = {
            e.extruder_num: e for e in self.printer.lookup_object("extruder_list", None) or ()
        }
        mcu = self.printer.lookup_object("mcu", None)
        if not extruders or mcu is None:
            return None

        def position(head):
            extruder = extruders.get(head)
            if extruder is None:
                return None
            return extruder.find_past_position(mcu.estimated_print_time(self.reactor.monotonic()))

        return position

    def _not_hooked(self, gcode, problems):
        """Say why nothing is hooked, and keep it for ACE_ADAPTER_STATUS. Ends the hook timer."""
        self.problems = problems
        msg = "ace2k_u1: not hooked: " + "; ".join(problems)
        logging.warning(msg)
        gcode.respond_info(msg)
        return self.reactor.NEVER

    def _on_feed_event(self, lane, event):
        if self.adapter is not None:
            self.adapter.on_feed_event(lane, event)

    def cmd_ACE_ADAPTER_STATUS(self, gcmd):
        a = self.adapter
        if self.problems:
            gcmd.respond_info("ace2k_u1: not hooked: " + "; ".join(self.problems))
            return
        if a is None:  # within a second of klippy:ready, before the hook ran
            gcmd.respond_info("ace2k_u1: not hooked yet")
            return
        a.status_lines(gcmd.respond_info)

    def cmd_ACE_EJECT(self, gcmd):
        """ACE_EJECT LANE=<1-4> [WAIT=0|1]: a refusal is the G-code's error, nothing moved;
        WAIT=1 (the default) returns once the eject has decided, WAIT=0 at once."""
        lane = gcmd.get_int("LANE", minval=1, maxval=LANES)
        wait = gcmd.get_int("WAIT", 1, minval=0, maxval=1)
        a = self.adapter
        if self.problems or a is None or not a.hooked:
            raise gcmd.error(f"ace2k_u1: lane {lane} eject refused: the adapter is not hooked")
        try:
            job = a.start_eject(lane - 1)
        except EjectRefused as err:
            raise gcmd.error(str(err)) from None
        if wait:
            a.wait_eject(job)


def load_config(config):
    return ACE2kU1(config)
