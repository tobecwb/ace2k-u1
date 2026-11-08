// The shapes that the page reads, as JSDoc typedefs for `tsc --checkJs`. This file has no runtime code.
// The status shapes are the ones that the host publishes. ace2k_u1 comes from klippy/extras/ace2k_u1_status.py,
// each unit comes from get_status of its ace2k object, and the rest from the objects of Klipper.
// The status is merged from changes, so a key may be missing until its first frame.
// So every key is optional. `null` is a None on the host.

/** @typedef {{ unit: string | null, lane: number }} LaneRef  A lane as the adapter names it. lane is 1-based. */
/** @typedef {{ brand?: string | null, material?: string | null, color_rgba?: string | null }} TagRecord */
/** @typedef {{ state?: string | null, uid?: string | null, record?: TagRecord | null }} Tag */
/**
 * @typedef {{ kind?: string | null, mode?: string | null, seq?: number | null, motor_mm?: number | null,
 *   filament_mm?: number | null }} LaneEvent
 */
/**
 * @typedef {object} Lane  One lane of a unit, from the ace2k object's lanes.
 * @property {string | null} [mode]
 * @property {boolean | null} [insert]
 * @property {boolean | null} [rest]
 * @property {boolean | null} [pushed]
 * @property {string | null} [error]
 * @property {number | null} [encoder_mm]
 * @property {boolean} [ff_on]
 * @property {LaneEvent | null} [last_event]
 * @property {Tag} [tag]
 */
/**
 * @typedef {object} Dryer
 * @property {string | null} [state]
 * @property {number | null} [target]
 * @property {number | null} [drive]
 * @property {number | null} [remaining]
 * @property {string | null} [fault]
 * @property {{ left?: boolean | null, right?: boolean | null } | null} [fans]
 * @property {Record<string, string> | null} [flaps]
 * @property {unknown[]} [notices]
 * @property {string | null} [mode]
 * @property {boolean} [humidity_allowed]
 * @property {number | null} [limit]
 * @property {number | null} [failures]
 * @property {number | null} [max_failures]
 * @property {boolean} [stopped]
 * @property {boolean} [cold]
 * @property {boolean} [own]
 * @property {boolean} [bound]
 * @property {number | null} [rh]  the unit's last idle RH, whole %
 */
/**
 * @typedef {object} Unit  A unit's ace2k object.
 * @property {string | null} [version]
 * @property {boolean | null} [link_proven]
 * @property {{ ok?: boolean | null, failing?: string[] | null }} [health]
 * @property {number | null} [chamber]
 * @property {number | null} [humidity]
 * @property {number | null} [ptc_left]
 * @property {number | null} [ptc_right]
 * @property {boolean} [env_valid]
 * @property {Lane[]} [lanes]
 * @property {Dryer} [dryer]
 */
/**
 * A tool of the plan.
 * @typedef {{ colour: string | null, material: string | null, temp: number | null, first_temp: number | null,
 *   lane: string | null, why: string }} Tool
 */
/**
 * A lane of the plan. materialSet comes from the adapter's live status (ace2k_u1.lanes), not from the plan:
 * false blocks Print; null (status unreadable) or undefined (an older adapter) does not.
 * spool and unknown come from liveLanes: spool is null for an empty bay, and unknown is true for a
 * spool with no material, which the planner never uses.
 * @typedef {{ key: string, colour: string | null, material: string | null, empty: boolean,
 *   head: number | null, materialSet?: boolean | null, colour_name?: string | null,
 *   spool?: PlanSpool | null, unknown?: boolean }} PlanLane
 */
/**
 * A spool in a bay, as liveLanes reads it. id names the spool: "<key>:<tag uid>", else
 * "<key>:<material>:<colour>". tag is true when the lane's tag speaks for it. from is the bay that
 * "eject all" took a held spool out of.
 * @typedef {{ id: string, colour: string | null, material: string | null, tag: boolean, uid?: string | null,
 *   from?: string }} PlanSpool
 */
/**
 * The spool a pick is pinned on: its tag uid and its look (lookOf: material and colour), as far as known. was is
 * the spool the bay held when the pin was made by "Save and use", while the status may still show it.
 * @typedef {{ uid: string | null, look: string | null, was?: { uid: string | null, look: string | null } | null }} Pin
 */
/**
 * @typedef {object} Plan  The adapter's plan for a file (ace2k_u1.plan).
 * @property {string} file
 * @property {number} mtime
 * @property {Tool[]} tools
 * @property {number[]} order
 * @property {number[]} raw
 * @property {number[]} [order_at]  Byte offset in the file of each order entry (absent from an older adapter).
 * @property {number} [size]  The file size the offsets refer to.
 * @property {Record<number, number>} heads  Lane swaps for each head.
 * @property {boolean} set
 * @property {Record<number, string> | null} choice
 * @property {PlanLane[]} lanes
 * @property {boolean} [end_unload]
 */
/** @typedef {{ type?: string, vendor?: string | null, color: string }} SpoolRecord  An ACEK_SPOOL record. */
/**
 * @typedef {object} U1Adapter  The part of the status that comes from the adapter (ace2k_u1).
 * @property {(LaneRef[] | LaneRef | null)[]} [heads]
 * @property {(LaneRef | null)[]} [current]
 * @property {(LaneRef | null)[]} [setting_for]
 * @property {string | null} [pause_reason]
 * @property {boolean} [resuming]
 * @property {boolean} [hooked]
 * @property {Plan | null} [plan]
 * @property {Record<string, SpoolRecord>} [spools]
 * @property {Record<string, { tag_forgotten?: boolean, push_end?: string | null, material_set?: boolean | null, eject_no_material?: boolean }>} [lanes]
 * @property {(boolean | null)[]} [probe_cold]  For each shared head, whether the probe finds it cold; else null.
 * @property {number | null} [probe_max_temp]  The probe's cold limit in °C.
 */
/**
 * @typedef {object} PrintTaskConfig  The U1's filament settings, with one entry per head.
 * @property {boolean[]} [filament_exist]
 * @property {string[]} [filament_vendor]
 * @property {string[]} [filament_type]
 * @property {string[]} [filament_sub_type]
 * @property {string[]} [filament_color_rgba]
 */
/**
 * @typedef {object} Status  The merged Moonraker status.
 * @property {Unit} [ace2k]
 * @property {U1Adapter} [ace2k_u1]
 * @property {{ units?: string[] }} [ace2k_units]  The units' object names ('ace2k a').
 * @property {{ keep_days?: number, error?: string | null }} [ace2k_u1_history]
 * @property {PrintTaskConfig} [print_task_config]
 * @property {{ state?: string, filename?: string, print_duration?: number }} [print_stats]
 * @property {{ file_position?: number, progress?: number, file_size?: number }} [virtual_sdcard]
 * @property {string | null} [page_unit]  The unit that a slice shows. sliceFor sets it.
 */

/**
 * @typedef {{ t: number, k: string, u?: string, ch: number | null, rh: number | null, hl: number | null,
 *   hr: number | null, dry: string | null, tgt?: number | null, rem?: number | null }} Sample
 */
/** @typedef {{ t: number, k: string, lvl: string, src: string, msg: string }} Event */
/** @typedef {{ samples: Sample[], events: Event[] }} Day */
/** @typedef {{ seq: number | null }} Seen */
/**
 * What the day loaders need from the page.
 * @typedef {{ fetch: typeof fetch, render: () => void, keepDays: () => number }} DayDeps
 */
/** @typedef {Pick<AppState, 'gen' | 'days' | 'loaded' | 'daysLoaded' | 'samples' | 'events'>} DayState */
/**
 * @typedef {{ err: string | null, insert: boolean | null,
 *   event: { seq: number | null, kind: string | null, mode: string | null, mm: string | null } | null,
 *   tag: string | null, brand: string | null, material: string | null, seen: Seen | null }} LaneSnapshot
 */
/** @typedef {{ dryer: string | null, fault: string | null, lanes: LaneSnapshot[] }} Snapshot */

/** @typedef {Record<string, Unit | undefined>} UnitMap  The status read by a unit's object name ('ace2k a'). */
/**
 * SamplesOf's cache, per unit. It is kept until app.samples is replaced.
 * @typedef {{ src: Sample[] | null, by: Map<string | null, Sample[]> }} SampleCache
 */
/**
 * @typedef {object} Ctx  What the flows of the page (send, printflow) share: the state and the effects.
 * @property {AppState} app
 * @property {boolean} demo
 * @property {() => void} render
 * @property {(msg: string) => void} toast
 * @property {(key: string, vars?: Record<string, unknown>) => string} t
 * @property {() => number} now  The clock in seconds.
 * @property {(ms: number) => Promise<void>} sleep
 * @property {(script: string, n: number | null, opts: SendOpts & { key: string }) => Promise<boolean>} sendScript
 * @property {Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null} [session]  The tab's session storage, which
 *   outlives a reload of the page. Null when the browser blocks it.
 */
/**
 * @typedef {Ctx & {
 *   saveHidden: () => void,
 *   saveTheme: () => void,
 *   saveTab: () => void,
 *   loadDays: (count: number) => Promise<void>,
 *   send: (id: string, n: number | null, args: import('./model.js').GcodeArgs, opts?: SendOpts) => Promise<boolean>,
 *   openPrint: () => Promise<void>,
 *   planFile: (file: string | null | undefined) => void,
 *   loadCard: (file: string) => Promise<void>,
 *   startPrint: () => Promise<void>,
 *   confirm: (msg: string) => boolean,
 *   blur: () => void,
 *   eventCount: () => number,
 * }} ActionCtx  What the actions read and do. It is Ctx plus the other effects of the page.
 */
/**
 * The control that a change event came from, as the actions read it.
 * @typedef {{ id: string, value: string, checked?: boolean, dataset: DOMStringMap, blur: () => void }} ChangeEl
 */
/**
 * @typedef {{ key?: string, quiet?: ((msg: string) => boolean) | null, unit?: string | null,
 *   once?: boolean }} SendOpts
 */
/** @typedef {{ unit: string | null, lane: number }} Selection  The bay shown, as the name of its unit and lane 0-3. */
/**
 * @typedef {{ bedLevel: boolean, flow: boolean, flowHeads: number[], timeLapse: boolean,
 *   endUnload: boolean }} PrintOptions
 */
/**
 * What a file card shows (Connection.fileCard). card is null while it loads. gcodeStart and gcodeEnd are the bytes
 * where the file's G-code starts and ends (Moonraker's gcode_start_byte / gcode_end_byte), when known.
 * @typedef {{ modified: number, card: { colours: import('./slicer-file.js').FileColour[] | null,
 *   time: number | null, thumb: string | null, modified: number | null,
 *   gcodeStart?: number | null, gcodeEnd?: number | null } | null }} CardEntry
 */
/**
 * The "eject all" run: its confirmation open, the bay being ejected (tail: its tail waits at the bay for the
 * operator), or the bay where it stopped and why.
 * @typedef {{ phase: 'ask' } | { phase: 'run', key: string, tail?: boolean }
 *   | { phase: 'failed', key: string, msg: string }} EjectRun
 */
/**
 * The Print panel's state. merges maps a tool to the tool it joins (same material only). held lists the
 * spools that "eject all" took out and that go to another bay. step is the index of the guided step shown, in
 * trail: the guide's steps (steps.js stepId) in the order they were first shown, and shown the current one's
 * name and kind when it was last kept (a put can turn into a check once its spool is in). eject is the "eject all" run:
 * its confirmation open, the bay being ejected, or the bay where it stopped and the host's message. allSteps shows
 * the list of every step under the guide.
 * dismissed lists the files whose similar-colour hint the user kept apart. pinned maps a picked tool to the pin of
 * the spool it was picked on, so the pick follows that spool to another bay (rekeyPicks). auto lists the colours
 * (merge roots) whose pick the page settled itself on a matching spool (steps.js settleMatches), not the user.
 * overrides lists the colours whose bay the user set in the colour picker: that bay is what the colour prints from,
 * whatever the suggestion says (a pick a guided step's Done made is not one: it carries out the suggestion).
 * reading: the file whose plan the host is reading, since at (ms), or its refusal.
 * files hold each file's path, modified time and size in bytes (for the reading bar's estimate). scrolled is the file
 * the page scrolled to its colours when it opened (PlanView.js scrollPlan), so it is not scrolled to again.
 * @typedef {{ file: string, at: number, error?: string }} Reading
 * @typedef {{ open: boolean, files: { path: string, modified: number, size?: number | null }[],
 *   reading?: Reading | null, file: string | null, choice: Record<number, string | null>,
 *   choiceId: string | null, choiceTools?: Tool[] | null,
 *   thumb?: { file: string, url: string } | null, merges?: Record<number, number>,
 *   held?: PlanSpool[], step?: number, trail?: string[], shown?: { id: string, kind: string } | null,
 *   eject?: EjectRun | null, allSteps?: boolean,
 *   dismissed?: string[], pinned?: Record<number, Pin>, auto?: number[], overrides?: number[], q?: string, sort?: 'new' | 'name',
 *   cards?: Record<string, CardEntry>, scrolled?: string | null }} PrintState
 */
/**
 * A lane's spool as the page shows it.
 * @typedef {{ vendor: string | null, type: string | null, subtype: string | null, rgba: string | null,
 *   fromTag: boolean }} Spool
 */
/** @typedef {{ id: string, enabled: boolean, reason: string | null, confirm: boolean }} LaneAction */
/**
 * What the Edit spool form holds (editPrefill). unit is the lane's unit, set when the form opens. use is the
 * tool (0-based) whose pick the saved spool becomes, when the colour picker opened the form; lock is true when
 * the colour's material fills the form and may not change.
 * @typedef {{ lane: number, head: number | null, type: string, vendor: string, color: string,
 *   unit?: string | null, use?: number, lock?: boolean }} EditForm
 */
/** @typedef {{ material: string, temp: number, hours: number }} Preset  A dryer preset (presets.json). */
/** @typedef {{ start: number | null, total: number | null } | null} Progress  The running cycle (cycleProgress). */
/**
 * The dryer's form. It holds the values of a preset, or the raw text once the user types. hm is h:mm.
 * @typedef {{ preset: number | null, temp: number | string, hm: string }} DryForm
 */
/**
 * @typedef {{ hidden: false, className: string, key: string, vars: Record<string, unknown> } |
 *   { hidden: true, className: null, key: null, vars: Record<string, unknown> }} BannerView
 */
/** @typedef {{ key: string, vars: Record<string, unknown> }} TitleView */

/**
 * @typedef {object} DryerView
 * @property {string} state
 * @property {number | null} target
 * @property {number | null} drive
 * @property {number | null} remaining
 * @property {string | null} fault
 * @property {boolean} fansOn
 * @property {Record<string, string>} flaps
 * @property {number | null} outletL
 * @property {number | null} outletR
 * @property {number | null} chamber
 * @property {number | null} humidity
 * @property {boolean} manual
 * @property {string | null} mode  One of DRYER_MODES in model.js; null before the unit's first report.
 * @property {boolean} humidityAllowed
 * @property {number | null} limit
 * @property {number} failures
 * @property {number | null} maxFailures
 * @property {boolean} stopped
 * @property {boolean} cold
 * @property {boolean} own
 * @property {boolean} bound
 * @property {number | null} rh  the unit's last idle RH, whole %; the live RH before the first report
 */
/** @typedef {{ label: string, confirm: string, vars: { unit: string } }} StopWords */
/**
 * @typedef {object} TopbarView
 * @property {string | null} version
 * @property {boolean} link
 * @property {boolean} health
 * @property {string[] | null} failing
 * @property {string | null} history
 * @property {string | null} paused
 * @property {boolean} online
 * @property {number[] | null} stopLanes
 * @property {StopWords} stopWords
 */

/**
 * The dryer log of one unit (ACEK_DRYER_LOG): lines is null while the page waits for the reply, then the counters
 * line and each fault or interrupted cycle. unit is the unit's name, null for [ace2k]. token tells one request from
 * the next, so the timeout of an old request never ends a new one.
 * @typedef {{ unit: string | null, lines: string[] | null, token: number }} DryerLog
 */
/**
 * @typedef {object} AppState  The single state object of the page. main.js builds it.
 * @property {Status} status
 * @property {boolean} online
 * @property {string | null | undefined} klippy
 * @property {string | undefined} [linkMessage]  Why the page's handshake failed (klippy 'page_error').
 * @property {Selection | null} selected
 * @property {Sample[]} samples
 * @property {Event[]} events
 * @property {Record<string, Snapshot>} snaps  Each unit object's last snapshot.
 * @property {string | null} dryerUnit
 * @property {string | null} chartUnit
 * @property {string[]} days
 * @property {number} daysLoaded
 * @property {Set<string>} loaded
 * @property {number} gen
 * @property {Set<string>} pending
 * @property {import('./conn.js').Connection | null} conn
 * @property {Record<string, boolean>} folds
 * @property {string} moveMm
 * @property {boolean} modeWarn  True while the humidity mode's warning panel is open.
 * @property {DryerLog | null} [dryerLog]  The dryer log asked for, and its reply once it came.
 * @property {Preset[]} presets
 * @property {DryForm} form  The starting values of a unit's dryer form.
 * @property {Record<string, DryForm>} forms  The forms the user changed, by unit name ('' for the unnamed unit).
 * @property {EditForm | null} edit
 * @property {number | null} hours
 * @property {string[]} hidden
 * @property {import('./state.js').Theme} theme  The theme of the page. It is the system theme,
 *   or the one chosen with the button in the top bar.
 * @property {string} filter
 * @property {number} limit
 * @property {PrintState} print
 * @property {PrintOptions} printOptions
 * @property {UiState} ui  The tab shown, the side panel open, whether another overlay is open, and the phone width.
 */
/**
 * The page's own state. panel names what the side panel shows ({ kind: 'bay', key } and the like); null when it
 * is closed. sheet is true while another overlay of the page is open over the side panel (a bottom sheet of its
 * own); Escape is then that overlay's, not the panel's. It is never the side panel's own phone form: narrow is true
 * on a phone's width, where the side panel itself is drawn as a bottom sheet, and Escape still closes it.
 * @typedef {{ tab: import('./state.js').Tab, panel: Record<string, string> | null, sheet: boolean,
 *   narrow?: boolean, listMode?: 'head' | 'unit', enter?: 'left' | 'right' | null }} UiState
 */
/**
 * The bridge from a component to the page's actions: the action's id, the values the old markup carried in
 * data-*, and for a control its value and checked state.
 * @typedef {(id: string, data?: Record<string, string>, value?: string, checked?: boolean) => unknown} RunFn
 */
/**
 * What the components read and call: the state, the catalogue, and the bridge to the actions.
 * @typedef {{ app: AppState, t: Ctx['t'], run: RunFn }} UiCtx
 */

export {};
