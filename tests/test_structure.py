"""Every hook point the adapter uses exists in the real U1 filament_feed.py (Snapmaker Klipper
1.6.0, the bench host's U1 tree). Skipped where that tree is absent.

The names come from the adapter itself (CONSTANTS, METHODS, CHANNEL_LISTS, ATTRIBUTES), so a hook
point added there is checked here too. Where each attribute is assigned in the real module:

- in FilamentFeed.__init__: every CHANNEL_LISTS name, and reactor, config and channel_active;
- outside __init__: exception_manager, assigned in the ready handler (READY_ASSIGNED below) —
  checked as assigned anywhere in the class. The adapter only hooks after klippy:ready, so it
  is in place by then.

The extrude-retry hook relies on three more facts, checked statically: the side motor's
run_one_cycle takes (dir, value, time); the load's feed direction is A on channel 1 and B on
channel 2, and load_extruding's retry pulses with it; the hang-neutral pulse runs the other way.

The lane -> extruder map reads the U1's extruders from its printer object "extruder_list", each
by its extruder_num and name; those three facts are checked in kinematics/extruder.py.

Two more facts the adapter relies on are checked statically: the U1's saved configuration
defaults carry an auto_mode entry, and the head sensors kept in runout_sensor are the optional
lookups of `filament_motion_sensor e<n>_filament`, two of them, in __init__.
"""

import ast
import types
import warnings
from pathlib import Path

import ace2k_u1
import pytest

REAL = Path.home() / "ace2k-bench" / "klipper-u1" / "klippy" / "extras" / "filament_feed.py"
pytestmark = pytest.mark.skipif(not REAL.exists(), reason=f"{REAL} absent (setup-u1.sh)")
EXTRUDER = REAL.parent.parent / "kinematics" / "extruder.py"

READY_ASSIGNED = ("exception_manager",)
HEAD_SENSOR = "filament_motion_sensor e%d_filament"


def tree():
    return ast.parse(REAL.read_text(encoding="utf-8"))


def feed_class():
    return next(n for n in tree().body if isinstance(n, ast.ClassDef) and n.name == "FilamentFeed")


def method(name):
    return next(n for n in feed_class().body if isinstance(n, ast.FunctionDef) and n.name == name)


def self_assigned(node):
    """The attribute names assigned on self anywhere under node."""
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, ast.AnnAssign):
            targets = [n.target]
        else:
            continue
        for t in targets:
            is_self = isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
            if is_self and t.value.id == "self":
                names.add(t.attr)
    return names


def test_constants():
    values = {}
    for node in tree().body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value
    for name, value in ace2k_u1.CONSTANTS.items():
        assert name in values, name
        assert values[name] == value, (name, values[name])


def test_methods():
    defs = {n.name: n for n in feed_class().body if isinstance(n, ast.FunctionDef)}
    for name, params in ace2k_u1.METHODS.items():
        assert name in defs, name
        args = tuple(a.arg for a in defs[name].args.args[1:])
        assert args[: len(params)] == params, (name, args)


def test_init_assigns_every_attribute():
    assigned = self_assigned(method("__init__"))
    for name in ace2k_u1.CHANNEL_LISTS + ace2k_u1.ATTRIBUTES:
        if name not in READY_ASSIGNED:
            assert name in assigned, name


def test_ready_assigned_attributes_are_assigned_in_the_class():
    assert set(READY_ASSIGNED) <= set(ace2k_u1.ATTRIBUTES)
    assigned = self_assigned(feed_class())
    for name in READY_ASSIGNED:
        assert name in assigned, name


def test_default_config_has_auto_mode():
    default = None
    for node in tree().body:
        named = isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FEED_DEFAULT_CONFIG" for t in node.targets
        )
        if named and isinstance(node.value, ast.Dict):
            default = node.value
    assert default is not None, "FEED_DEFAULT_CONFIG"
    keys = {k.value for k in default.keys if isinstance(k, ast.Constant)}
    assert "auto_mode" in keys


def test_runout_sensor_holds_the_head_sensors():
    init = method("__init__")
    lookups = [
        n
        for n in ast.walk(init)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "lookup_object"
        and n.args
        and isinstance(n.args[0], ast.BinOp)
        and isinstance(n.args[0].left, ast.Constant)
        and n.args[0].left.value == HEAD_SENSOR
    ]
    assert len(lookups) == 2, "two head-sensor lookups"
    for call in lookups:  # optional: None when the sensor is absent, which the adapter checks
        assert len(call.args) == 2 and isinstance(call.args[1], ast.Constant)
        assert call.args[1].value is None
    appends = [
        n
        for n in ast.walk(init)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "append"
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "runout_sensor"
    ]
    assert len(appends) == 2, "runout_sensor holds one entry per channel"


def calls_named(node, attr):
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


def first_arg(call):
    arg = call.args[0]
    return arg.id if isinstance(arg, ast.Name) else None


def test_the_side_motor_pulse_takes_what_the_hook_takes():
    motor = next(n for n in tree().body if isinstance(n, ast.ClassDef) and n.name == "FeedMotor")
    pulse = next(
        n for n in motor.body if isinstance(n, ast.FunctionDef) and n.name == "run_one_cycle"
    )
    args = tuple(a.arg for a in pulse.args.args[1:])
    assert args == ace2k_u1.MOTOR_PULSE
    assert "motor" in self_assigned(method("__init__"))


def test_the_load_extruding_retry_pulses_forward():
    do_feed = method("_do_feed")
    # motor_dir = FEED_MOTOR_DIR_A, then B when ch == FEED_CHANNEL_2
    assigned = [
        n.value.id
        for n in ast.walk(do_feed)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "motor_dir" for t in n.targets)
        and isinstance(n.value, ast.Name)
    ]
    assert assigned == ["FEED_MOTOR_DIR_A", "FEED_MOTOR_DIR_B"]
    flip = [
        n
        for n in ast.walk(do_feed)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Compare)
        and isinstance(n.test.comparators[0], ast.Name)
        and n.test.comparators[0].id == "FEED_CHANNEL_2"
        and any(
            isinstance(b, ast.Assign) and getattr(b.targets[0], "id", None) == "motor_dir"
            for b in n.body
        )
    ]
    assert len(flip) == 1
    # the retry pulse inside load_extruding's retry loop: a run_one_cycle(motor_dir, ...) in the
    # for-loop over _feed_load_extrude_max_times
    loops = [
        n
        for n in ast.walk(do_feed)
        if isinstance(n, ast.For)
        and isinstance(n.iter, ast.Call)
        and n.iter.args
        and isinstance(n.iter.args[0], ast.Attribute)
        and n.iter.args[0].attr == "_feed_load_extrude_max_times"
    ]
    assert len(loops) == 1
    pulses = calls_named(loops[0], "run_one_cycle")
    assert [first_arg(c) for c in pulses] == ["motor_dir"]


def test_the_hang_neutral_pulse_runs_the_other_way():
    hang = method("_hang_neutral")
    branch = next(
        n
        for n in ast.walk(hang)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Compare)
        and getattr(n.test.comparators[0], "id", None) == "FEED_CHANNEL_1"
    )
    body = [first_arg(c) for b in branch.body for c in calls_named(b, "run_one_cycle")]
    other = [first_arg(c) for b in branch.orelse for c in calls_named(b, "run_one_cycle")]
    assert body == ["FEED_MOTOR_DIR_B"] and other == ["FEED_MOTOR_DIR_A"]


@pytest.mark.skipif(not EXTRUDER.exists(), reason=f"{EXTRUDER} absent (setup-u1.sh)")
def test_the_extruders_are_listed_with_their_index_and_name():
    module = ast.parse(EXTRUDER.read_text(encoding="utf-8"))
    add = next(
        n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "add_printer_objects"
    )
    listed = [
        c
        for c in calls_named(add, "add_object")
        if len(c.args) == 2
        and isinstance(c.args[0], ast.Constant)
        and c.args[0].value == "extruder_list"
    ]
    assert len(listed) == 1, "printer.add_object('extruder_list', ...)"
    cls = next(
        n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "PrinterExtruder"
    )
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    assert "extruder_num" in self_assigned(init)
    named = [
        n
        for n in ast.walk(init)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute)
            and isinstance(t.value, ast.Name)
            and t.value.id == "self"
            and t.attr == "name"
            for t in n.targets
        )
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute)
        and n.value.func.attr == "get_name"
        and not n.value.args
    ]
    assert len(named) == 1, "self.name = config.get_name()"


# --- the tags: the U1's per-head filament setting and its filament table ------------------------

TASK_CONFIG = REAL.parent / "print_task_config.py"
FILAMENT_PARAMETERS = REAL.parent / "filament_parameters.py"


def module_def(path, cls, name):
    module = ast.parse(path.read_text(encoding="utf-8"))
    klass = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return (
        module,
        klass,
        next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name),
    )


def template(joined):
    """An f-string as a template: '{name}' for each formatted name, the constants as they are."""
    parts = []
    for v in joined.values:
        if isinstance(v, ast.Constant):
            parts.append(v.value)
        else:
            parts.append("{" + v.value.id + "}")
    return "".join(parts)


@pytest.mark.skipif(not TASK_CONFIG.exists(), reason=f"{TASK_CONFIG} absent (setup-u1.sh)")
def test_the_filament_config_command_reads_every_parameter_the_tags_write():
    _, _, cmd = module_def(TASK_CONFIG, "PrintTaskConfig", "cmd_" + ace2k_u1.TAG_COMMAND)
    read = {
        c.args[0].value
        for c in ast.walk(cmd)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr.startswith("get")
        and isinstance(c.func.value, ast.Name)
        and c.func.value.id == "gcmd"
        and c.args
        and isinstance(c.args[0], ast.Constant)
    }
    assert set(ace2k_u1.TAG_PARAMS) <= read, set(ace2k_u1.TAG_PARAMS) - read


@pytest.mark.skipif(not TASK_CONFIG.exists(), reason=f"{TASK_CONFIG} absent (setup-u1.sh)")
def test_the_unset_head_is_none_and_white_in_lists_per_head():
    module = ast.parse(TASK_CONFIG.read_text(encoding="utf-8"))
    default = next(
        n.value
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "DEFAULT_PRINT_TASK_CONFIG" for t in n.targets)
    )
    entries = {k.value: v for k, v in zip(default.keys, default.values) if k is not None}
    expected = {
        "filament_vendor": "NONE",
        "filament_type": "NONE",
        "filament_sub_type": "NONE",
        "filament_color_rgba": "FFFFFFFF",
    }
    assert set(expected) == set(ace2k_u1.TAG_STATUS.values())
    for key, value in expected.items():
        node = entries[key]  # ['NONE'] * PHYSICAL_EXTRUDER_NUM: one entry per head
        assert isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult), key
        assert isinstance(node.left, ast.List) and len(node.left.elts) == 1, key
        assert node.left.elts[0].value == value, key
    clear = dict(w.split("=", 1) for w in ace2k_u1.TAG_CLEAR.split())
    assert clear == {
        "VENDOR": "NONE",
        "FILAMENT_TYPE": "NONE",
        "FILAMENT_SUBTYPE": "NONE",
        "FILAMENT_COLOR_RGBA": "FFFFFFFF",
    }


@pytest.mark.skipif(
    not FILAMENT_PARAMETERS.exists(), reason=f"{FILAMENT_PARAMETERS} absent (setup-u1.sh)"
)
def test_the_filament_table_is_keyed_vendor_type_subtype_key_with_a_generic_fallback():
    _, klass, search = module_def(
        FILAMENT_PARAMETERS, "FilamentParameters", "_search_filament_param_value"
    )
    keys = [
        template(n.value)
        for n in ast.walk(search)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.JoinedStr)
        and any(isinstance(t, ast.Name) and t.id == "key" for t in n.targets)
    ]
    assert keys[0] == "{filament_vendor}_{filament_main_type}_{filament_sub_type}_{key_name}"
    assert "{default_fill}_{filament_main_type}_{default_fill}_{key_name}" in keys
    fill = [
        n.value.value
        for n in ast.walk(search)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "default_fill" for t in n.targets)
    ]
    assert fill == ["generic"]
    # the tables the adapter reads the types from: dicts held on the instance
    init = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    tables = [
        n
        for n in ast.walk(init)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Attribute)
        and n.targets[0].attr.startswith("_config_")
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "attr", None) == "load_snapmaker_config_file"
    ]
    assert tables, "self._config_* = load_snapmaker_config_file(...)"


# --- the pauses: the U1's tangle pause, its runout pause, its print states ------------------------

ENTANGLE = REAL.parent / "filament_entangle_detect.py"
SWITCH_SENSOR = REAL.parent / "filament_switch_sensor.py"
MOTION_SENSOR = REAL.parent / "filament_motion_sensor.py"
PAUSE_RESUME = REAL.parent / "pause_resume.py"
PRINT_STATS = REAL.parent / "print_stats.py"
EXCEPTIONS = REAL.parent.parent / "exception_manager.py"


def literal(node):
    """A call argument as a value: a constant, a self.<...>.<NAME> attribute as 'NAME', a
    '%s ...' % (...) as its format string."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return literal(node.left)
    if isinstance(node, ast.Name):
        return node.id
    return None


def class_method(path, cls, name):
    return module_def(path, cls, name)[2]


def exception_calls(fn):
    """The send_event("print_stats:update_exception_info", ...) calls under fn, their args."""
    return [
        [literal(a) for a in c.args[1:]]
        for c in calls_named(fn, "send_event")
        if c.args and literal(c.args[0]) == "print_stats:update_exception_info"
    ]


def raised(fn):
    return [
        {k.arg: literal(k.value) for k in c.keywords}
        for c in calls_named(fn, "raise_exception_async")
    ]


@pytest.mark.skipif(not ENTANGLE.exists(), reason=f"{ENTANGLE} absent (setup-u1.sh)")
def test_the_tangle_pause_is_the_u1s_as_used():
    fn = class_method(ENTANGLE, "FilamentEntangleDetect", "_check_entangle_event")
    a = ace2k_u1
    info = [a.TANGLE_ID, "extruder_index", a.TANGLE_CODE, a.TANGLE_MESSAGE, a.TANGLE_LEVEL]
    assert exception_calls(fn) == [info]
    assert len(calls_named(fn, "send_pause_command")) == 1
    assert raised(fn) == [
        dict(
            id=a.TANGLE_ID,
            index="extruder_index",
            code=a.TANGLE_CODE,
            message=a.TANGLE_MESSAGE,
            oneshot=1,
            level=a.TANGLE_LEVEL,
        )
    ]
    scripts = [literal(c.args[0]) for c in calls_named(fn, "run_script")]
    assert scripts == [a.TANGLE_SCRIPT]
    # in that order: the exception info, the pause command, the exception, PAUSE
    order = [
        c.func.attr
        for c in sorted(
            calls_named(fn, "send_event")
            + calls_named(fn, "send_pause_command")
            + calls_named(fn, "raise_exception_async")
            + calls_named(fn, "run_script"),
            key=lambda c: (c.lineno, c.col_offset),
        )
    ]
    assert order == [
        "send_event",
        "send_pause_command",
        "send_event",
        "raise_exception_async",
        "run_script",
    ]
    # the second event: the tangle's own, with the head
    tangled = [c for c in calls_named(fn, "send_event") if literal(c.args[0]) == a.TANGLE_EVENT]
    assert len(tangled) == 1 and [literal(x) for x in tangled[0].args[1:]] == ["extruder_index"]


@pytest.mark.skipif(not PAUSE_RESUME.exists(), reason=f"{PAUSE_RESUME} absent (setup-u1.sh)")
def test_the_pause_command_takes_nothing_and_no_resume_event_is_sent():
    module = ast.parse(PAUSE_RESUME.read_text(encoding="utf-8"))
    klass = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "PauseResume")
    defs = {n.name: n for n in klass.body if isinstance(n, ast.FunctionDef)}
    assert [a.arg for a in defs["send_pause_command"].args.args] == ["self"]
    # the resume is seen as print_stats going from paused back to printing: pause_resume sends
    # no event of its own on a resume
    events = [literal(c.args[0]) for c in calls_named(klass, "send_event") if c.args]
    assert not [e for e in events if str(e).split(":")[-1].startswith("resume")], events


@pytest.mark.skipif(not PRINT_STATS.exists(), reason=f"{PRINT_STATS} absent (setup-u1.sh)")
def test_print_stats_pauses_and_resumes_through_its_state():
    _, _, start = module_def(PRINT_STATS, "PrintStats", "note_start")
    _, _, pause = module_def(PRINT_STATS, "PrintStats", "note_pause")

    def states(fn):
        return [
            n.value.value
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Attribute)
            and n.targets[0].attr == "state"
            and isinstance(n.value, ast.Constant)
        ]

    assert states(start) == ["printing"]
    assert states(pause) == ["paused"]


@pytest.mark.skipif(not SWITCH_SENSOR.exists(), reason=f"{SWITCH_SENSOR} absent (setup-u1.sh)")
def test_the_runout_pause_is_the_u1s_as_used():
    fn = class_method(SWITCH_SENSOR, "RunoutHelper", "_runout_event_handler")
    a = ace2k_u1
    info = ["MODULE_ID_TOOLHEAD", "extruder_index", "CODE_TOOLHEAD_FILAMENT_RUNOUT", "%s runout"]
    assert exception_calls(fn) == [info + [a.RUNOUT_LEVEL]]
    assert len(calls_named(fn, "send_pause_command")) == 1
    assert raised(fn) == [
        dict(
            id="MODULE_ID_TOOLHEAD",
            index="extruder_index",
            code="CODE_TOOLHEAD_FILAMENT_RUNOUT",
            message="%s runout",
            oneshot=0,
            level=a.RUNOUT_LEVEL,
        )
    ]
    prefix = [
        n.value.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pause_prefix" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    ]
    assert prefix == ["", "PAUSE IS_RUNOUT=1\n"]
    assert prefix[1] + "" + "\nM400" == a.RUNOUT_SCRIPT  # with an empty runout_gcode
    exec_gcode = class_method(SWITCH_SENSOR, "RunoutHelper", "_exec_gcode")
    script = calls_named(exec_gcode, "run_script")[0].args[0]
    assert ast.unparse(script) == "prefix + template.render() + '\\nM400'"
    replenish = [c.args[0] for c in calls_named(fn, "run_script")]
    assert len(replenish) == 1 and isinstance(replenish[0], ast.JoinedStr)
    # the f-string evaluated for a head: the adapter's script for that head
    expr = compile(ast.Expression(replenish[0]), "replenish", "eval")
    for head in range(4):
        sensor = types.SimpleNamespace(extruder_index=head)
        assert eval(expr, {"self": sensor}) == a.REPLENISH_SCRIPT % head  # noqa: S307
    # the replenish's verdict, read after it: print_task_config's perform_auto_replenish
    reads = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "perform_auto_replenish" in reads and "pause_delay" in reads
    init = class_method(SWITCH_SENSOR, "RunoutHelper", "__init__")
    delay = [
        c for c in calls_named(init, "getfloat") if c.args and literal(c.args[0]) == "pause_delay"
    ]
    assert len(delay) == 1 and literal(delay[0].args[1]) == a.RUNOUT_PAUSE_DELAY_S
    names = [
        n
        for n in ast.walk(init)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Attribute)
        and n.targets[0].attr == "name"
    ]
    assert ast.unparse(names[0].value) == "config.get_name().split()[-1]"
    # the print's end: no runout pause then
    note = class_method(SWITCH_SENSOR, "RunoutHelper", "note_filament_present")
    assert "is_exec_print_end_action" in {
        n.attr for n in ast.walk(note) if isinstance(n, ast.Attribute)
    }


@pytest.mark.skipif(not MOTION_SENSOR.exists(), reason=f"{MOTION_SENSOR} absent (setup-u1.sh)")
def test_the_head_sensor_runs_out_through_the_runout_helper():
    module = ast.parse(MOTION_SENSOR.read_text(encoding="utf-8"))
    assigned = [
        ast.unparse(n.value)
        for n in ast.walk(module)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Attribute)
        and n.targets[0].attr == "runout_helper"
    ]
    assert assigned == ["filament_switch_sensor.RunoutHelper(config)"]
    # the head sensor's name, as the adapter builds it when the sensor does not say
    assert (HEAD_SENSOR % 2).split()[-1] == ace2k_u1.RUNOUT_SENSOR % 2


@pytest.mark.skipif(not EXCEPTIONS.exists(), reason=f"{EXCEPTIONS} absent (setup-u1.sh)")
def test_the_runout_exception_is_the_toolheads_runout():
    module = ast.parse(EXCEPTIONS.read_text(encoding="utf-8"))
    klass = next(
        n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "ExceptionList"
    )
    values = {
        t.id: n.value.value
        for n in klass.body
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    assert values["MODULE_ID_TOOLHEAD"] == ace2k_u1.RUNOUT_ID == ace2k_u1.TANGLE_ID
    assert values["CODE_TOOLHEAD_FILAMENT_RUNOUT"] == ace2k_u1.RUNOUT_CODE
    manager = next(
        n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "ExceptionManager"
    )
    fn = next(
        n
        for n in manager.body
        if isinstance(n, ast.FunctionDef) and n.name == "raise_exception_async"
    )
    params = [a.arg for a in fn.args.args[1:]]
    assert params[:6] == ["id", "index", "code", "message", "oneshot", "level"]


@pytest.mark.skipif(not SWITCH_SENSOR.exists(), reason=f"{SWITCH_SENSOR} absent (setup-u1.sh)")
def test_the_runout_backs_up_the_filament_and_honours_the_sensors_switches():
    init = class_method(SWITCH_SENSOR, "RunoutHelper", "__init__")
    assigned = {
        n.targets[0].attr: n.value
        for n in ast.walk(init)
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Attribute)
    }
    assert ast.unparse(assigned["runout_pause"]) == "config.getboolean('pause_on_runout', True)"
    assert "sensor_enabled" in assigned
    note = class_method(SWITCH_SENSOR, "RunoutHelper", "note_filament_present")
    backups = calls_named(note, "backup_filament_info")
    assert [ast.unparse(c.args[0]) for c in backups] == ["self.extruder_index"]
    # a disabled sensor returns before the backup: no runout, nothing backed up
    early = [
        n
        for n in note.body
        if isinstance(n, ast.If)
        and "self.sensor_enabled" in ast.unparse(n.test)
        and isinstance(n.body[-1], ast.Return)
    ]
    assert early and early[0].lineno < backups[0].lineno
    # the pause only with pause_on_runout
    fn = class_method(SWITCH_SENSOR, "RunoutHelper", "_runout_event_handler")
    guards = [n for n in ast.walk(fn) if isinstance(n, ast.If)]
    assert any(ast.unparse(g.test) == "self.runout_pause" for g in guards)
    _, _, backup = module_def(TASK_CONFIG, "PrintTaskConfig", "backup_filament_info")
    assert [a.arg for a in backup.args.args] == ["self", "extruder_index"]


def test_the_extruder_position_is_read_as_the_u1s_tangle_check_reads_it():
    # the wheel follows a head's extruder through PrinterExtruder.find_past_position(print_time)
    # at the MCU's estimated print time — the U1's filament_entangle_detect's own reading
    module = ast.parse(EXTRUDER.read_text(encoding="utf-8"))
    cls = next(
        n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "PrinterExtruder"
    )
    find = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "find_past_position"
    )
    assert [a.arg for a in find.args.args] == ["self", "print_time"]
    entangle = (REAL.parent / "filament_entangle_detect.py").read_text(encoding="utf-8")
    assert "self.extruder.find_past_position(print_time)" in entangle
    assert "lookup_object('mcu').estimated_print_time" in entangle


def test_register_command_with_none_hands_back_the_old_handler():
    # the ACE_CLEAR escape replaces ace2k's handler the Klipper way: register_command(name,
    # None) removes it and returns it; the help text and the not-ready table read as named
    source = (REAL.parent.parent / "gcode.py").read_text(encoding="utf-8")
    with warnings.catch_warnings():  # the U1's file carries an invalid escape of its own
        warnings.simplefilter("ignore", SyntaxWarning)
        module = ast.parse(source)
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "GCodeDispatch")
    reg = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "register_command"
    )
    assert [a.arg for a in reg.args.args][:5] == ["self", "cmd", "func", "when_not_ready", "desc"]
    text = ast.get_source_segment(source, reg)
    assert "if func is None:" in text
    assert "old_cmd = self.ready_gcode_handlers.get(cmd)" in text
    assert "return old_cmd" in text
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    assert {"gcode_help", "base_gcode_handlers", "ready_gcode_handlers"} <= self_assigned(init)
