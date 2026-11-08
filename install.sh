#!/bin/sh
# Generated from installer/*.sh by make installer; edit the parts, not this file.
# install.sh — install, update or remove ace2k and its Snapmaker U1 adapter, on the U1 itself.
#   curl -fsSL https://raw.githubusercontent.com/tobecwb/ace2k-u1/main/install.sh | sh
# docs/install.md is the operator's page; --help lists the options.  Every frame to the unit goes
# through ace2k's own flasher, ace2k_flash.pyz.  The script is functions and the last line runs
# main, so a download cut short runs nothing.  Before a file changes it is copied into the run's
# backup directory; on a failure the run stops, says what failed and where the backup is, and
# running the installer again is the fix.  The ACE2K_* variables below exist for the tests only.
set -u

U1_REPO=tobecwb/ace2k-u1
ACE2K_REPO=tobecwb/ace2k
TESTED_PAXX=2.0.0-paxx12-24
FACTORY_VERSION=1.1.31
DEBUG_MSG="Created /oem/.debug. No reboot is needed: the files written now survive the next boot, because the U1 wipes its overlay at boot only when this flag is missing."

# ace2k_flash.pyz's exit status (ace2k's docs/flashing.md): 0 verified, 1 failed, 2 written but not
# verifiable here, 3 refused with nothing changed, 4 nothing answered (Klipper maybe restarted).
FLASH_OK=0 FLASH_UNVERIFIED=2 FLASH_UNCHANGED=3 FLASH_NO_ANSWER=4
# The flasher's name in an install made by an older installer, before the flasher was one .pyz.
OLD_FLASHER=ace2k_flash.py
# An unnamed [ace2k]: the name every message and plan line gives it, and the name it takes once a
# unit is added (the cfg tool's rename-a and its RENAMED).
UNNAMED=ace2k
UNNAMED_RENAMED=a
# The unit names a new install gives, in order, and the separator the unit records are read
# with: read with a tab in IFS joins empty fields.
NAMES_ALL="a b c d"
US=$(printf '\037')

setup_paths() {
    ROOT=${ACE2K_ROOT:-}
    MOON=${ACE2K_MOONRAKER:-http://localhost:7125}
    GH_API=${ACE2K_GITHUB_API:-https://api.github.com}
    GH_DL=${ACE2K_GITHUB_DL:-https://github.com}
    TTY=${ACE2K_TTY:-/dev/tty}
    NGINX=${ACE2K_NGINX:-nginx}
    PYTHON=${ACE2K_PYTHON:-python3}
    FLASHER=${ACE2K_FLASHER:-}
    POLL_S=${ACE2K_POLL_S:-2}
    WAIT_TRIES=${ACE2K_WAIT_TRIES:-60}
    CHECK_TRIES=${ACE2K_CHECK_TRIES:-15}
    BLINK_S=${ACE2K_BLINK_S:-10} # how long a unit blinks: ace2k 10 s, the factory firmware less
    FACTORY_SHA256=${ACE2K_FACTORY_SHA256:-a468e366226e7c483d3dd96b444223e89cbf0d1b10573d42b6263843ffd6be3e}
    FACTORY_SIZE=${ACE2K_FACTORY_SIZE:-71592}
    KLIPPER_INIT=$ROOT/etc/init.d/S60klipper
    BUILD_VERSION=$ROOT/etc/BUILD_VERSION
    DEBUG_FLAG=$ROOT/oem/.debug
    EXTRAS=$ROOT/home/lava/klipper/klippy/extras
    CFG=$ROOT/home/lava/printer_data/config
    KCFG=$CFG/extended/klipper
    WEB=$ROOT/home/lava/ace2k-web
    NGINX_CONF=$ROOT/etc/nginx/fluidd.d/ace2k.conf
    STATE_DIR=$ROOT/home/lava/printer_data/ace2k
    BYID=$ROOT/dev/serial/by-id
    CFGTOOL=${ACE2K_CFGTOOL:-}
    LOCK=$ROOT/tmp/ace2k-install.lock
}

say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*"; }

comma_join() { tr '\n' , | sed 's/,$//'; } # the lines on stdin as one line: a,b,c
word_list() { tr '\n' ' ' | sed 's/ $//; s/ /, /g'; } # the words on stdin as one line: a, b, c

# src dest — dest replaced by a copy of src: the copy is written beside dest, then renamed over
# it, so nothing ever reads half a file; 1 when either step fails
put_atomic() { cp "$1" "$2.new.$$" && mv "$2.new.$$" "$2"; }

# Once the run has changed something, every stop also names the backup and the way out.  A stop
# while Klipper is stopped for the flashes starts it again, except on a signal: a flash may have
# been cut there, so the operator starts it.
die() {
    printf '%s\n' "$*" >&2
    if [ "${KLIPPER_STOPPED:-0}" -eq 1 ]; then
        KLIPPER_STOPPED=0
        if [ "${SIGNALLED:-0}" -eq 1 ]; then
            printf 'Klipper is stopped: start it with /etc/init.d/S60klipper start, or run the installer again.\n' >&2
        else
            printf 'Starting Klipper again (/etc/init.d/S60klipper start): it was stopped for the flashes.\n' >&2
            "$KLIPPER_INIT" start >/dev/null 2>&1 || printf 'WARNING: S60klipper start exited with an error.\n' >&2
        fi
    fi
    [ -z "${BK:-}" ] || printf 'The files this run changed were copied first to %s.\n' "$BK" >&2
    [ -z "${BK:-}${AFTER:-}" ] || printf '%s\n' "${AFTER:-Fix the cause above, then run the installer again: it picks up from what is on the printer.}" >&2
    exit 1
}

usage() {
    cat <<'EOF'
install.sh — install, update or remove ace2k and its Snapmaker U1 adapter (docs/install.md).

  curl -fsSL https://raw.githubusercontent.com/tobecwb/ace2k-u1/main/install.sh | sh
  curl -fsSL https://raw.githubusercontent.com/tobecwb/ace2k-u1/main/install.sh | sh -s -- OPTIONS

With no option it installs, or, when ace2k is installed, asks: update or remove.
  --update               update without asking
  --remove               remove without asking
  --restore-stock FILE   with --remove: first put every unit back on the original Anycubic
                         firmware from FILE (its SHA-256 is checked)
  --force                with --restore-stock: accept a FILE of another SHA-256, with a warning
  --yes                  answer yes to every question (the flasher's too)
  --port PATH            a new install: one unit, on this port (/dev/serial/by-id/usb-1a86_...)
  --from DIR_OR_URL      take the release files from there instead of GitHub
  --pre                  include pre-releases when looking for the latest release
  --version TAG          install this ace2k-u1 release instead of the latest
  --ace2k-version TAG    test only: this ace2k release instead of the one ace2k-u1 names
  -h, --help             this text
Run it from an interactive ssh session (it reads its answers from the terminal), or give every
answer as an option.
EOF
}

need_arg() {
    if [ "$2" -lt 2 ] || [ -z "$3" ]; then die "$1 needs a value (see --help)"; fi
}

parse_args() {
    ACTION='' YES=0 PORT='' FROM='' PRE=0 U1_TAG='' ACE2K_TAG='' STOCK='' FORCE=0
    CREATE_DEBUG=0 CHANGED=0 MODE='' WEB_PENDING=0 CONTINUED='' BK='' AFTER='' WORK='' LOCKED=0
    KPID_BEFORE='' SEEN_DOWN=0 CONSOLE_MARK=0 ERROR_RESTARTED=0 KLIPPER_STOPPED=0 FLASH_PORT=''
    SIGNALLED=0 OLD_RESTORE=0
    while [ $# -gt 0 ]; do
        case $1 in
        --update) ACTION=update ;;
        --remove) ACTION=remove ;;
        --yes) YES=1 ;;
        --pre) PRE=1 ;;
        --force) FORCE=1 ;;
        --port) need_arg "$1" $# "${2:-}"; PORT=$2; shift ;;
        --from) need_arg "$1" $# "${2:-}"; FROM=$2; shift ;;
        --version) need_arg "$1" $# "${2:-}"; U1_TAG=$2; shift ;;
        --ace2k-version) need_arg "$1" $# "${2:-}"; ACE2K_TAG=$2; shift ;;
        --restore-stock) need_arg "$1" $# "${2:-}"; STOCK=$2; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
        esac
        shift
    done
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

need_tools() {
    missing=''
    for t in curl tar gzip awk sed mktemp "$PYTHON" "$NGINX"; do
        command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
    done
    command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || missing="$missing sha256sum"
    [ -z "$missing" ] || die "Missing on this printer:$missing; nothing changed"
}

# The answers come from the terminal on fd 3: under `curl | sh` the standard input is the script.
open_tty() {
    HAVE_TTY=0
    if (exec 3<"$TTY") 2>/dev/null; then exec 3<"$TTY" && HAVE_TTY=1; fi
}

ask() { # prompt → ANSWER; 1 when the terminal's input has ended (ANSWER is then empty)
    [ "$HAVE_TTY" -eq 1 ] || die "There is no terminal to answer \"$1\". Run this from an interactive ssh session, or give every answer as an option: --yes, --update or --remove, --port PATH (see --help); nothing changed."
    printf '%s ' "$1" >&2
    IFS= read -r ANSWER <&3 && return 0
    ANSWER=''
    return 1
}

ask_again() { # prompt — for a question asked until it gets a good answer: its end of input stops
    ask "$1" || die "The terminal's input ended before an answer to \"$1\"; nothing changed."
}

confirm() { # prompt → 0 on yes
    if [ "$YES" -eq 1 ]; then say "$1 yes (--yes)" && return 0; fi
    ask "$1 [y/N]"
    case $ANSWER in y | Y | yes | YES) return 0 ;; esac
    return 1
}

take_lock() {
    mkdir -p "$(dirname "$LOCK")"
    if ! mkdir "$LOCK" 2>/dev/null; then
        pid=$(cat "$LOCK/pid" 2>/dev/null)
        { [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; } || die "another install.sh runs (pid $pid); nothing changed"
        rm -rf "$LOCK"
        mkdir "$LOCK" || die "cannot take the lock $LOCK; nothing changed"
    fi
    echo $$ >"$LOCK/pid"
    LOCKED=1
}

cleanup() { # the only thing an exit undoes: the work directory and the lock
    [ "${LOCKED:-0}" -eq 0 ] || rm -rf "$LOCK"
    [ -z "${WORK:-}" ] || rm -rf "$WORK"
}

# --- Moonraker and the state file ---

pyjson() { # EXPR… — read JSON on stdin, print each EXPR evaluated with the value as d, one per
    # line ('' for one that fails), in one Python start
    "$PYTHON" -I -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = None
for e in sys.argv[1:]:
    try:
        v = "" if d is None else eval(e)
    except Exception:
        v = ""
    print("" if v is None else v)' "$@"
}

mr() { curl -fsS --max-time 10 "$MOON$1" 2>/dev/null; }
klippy_state() { mr /server/info | pyjson 'd["result"]["klippy_state"]'; }

# → OBJECTS_Q, the query's part that names each unit, as Klipper names its object: the section's own
# spelling, as [ace2k A].  Klipper is polled many times: the names are read again only when
# ace2k.cfg's checksum changes, as a Python start is slow on the U1.  It sets a variable, so it is
# never called inside $(...).
unit_objects() {
    sum=$(sha256 "$KCFG/ace2k.cfg")
    if [ "$sum" != "${OBJECTS_SUM:-}" ]; then
        OBJECTS_Q=$(cfgtool objects "$KCFG/ace2k.cfg" 2>/dev/null | sed '/^ace2k$/d; s/ /%20/g; s/^/\&/' | tr -d '\n')
        OBJECTS_SUM=$sum
    fi
}

read_status() { # one query → PRINT_STATE RUNNING OTHER HAS_ACE2K STATUS_NAMED, and $WORK/kunits
    # Moonraker answers only the objects it is asked for: each unit of ace2k.cfg is named.
    q='/printer/objects/query?print_stats&configfile=settings&ace2k'
    STATUS_NAMED=0 # 1 once the query could name the units (the cfg tool comes with the release)
    if [ -n "$CFGTOOL" ] && [ -f "$CFGTOOL" ]; then STATUS_NAMED=1; fi
    if [ "$STATUS_NAMED" -eq 1 ] && [ -f "$KCFG/ace2k.cfg" ]; then unit_objects && q="$q$OBJECTS_Q"; fi
    # RUNNING is the first unit's; $WORK/kunits gets every unit Klipper reports,
    # as name, version, link proven, uid (name empty for an unnamed [ace2k]).  Inline Python, not
    # the cfg tool: the start checks run before the release is fetched.  unit() is the tool's
    # unit_of applied to a Klipper object, in lower case as ace2k names a unit.
    st=$(mr "$q" | "$PYTHON" -I -c '
import json, sys
try:
    s = json.load(sys.stdin)["result"]["status"]
except Exception:
    s = {}
cfg = (s.get("configfile") or {}).get("settings") or {}
def unit(k):  # the unit an object holds, in lower case as ace2k names it (empty for [ace2k])
    return " ".join(k.split()[1:]).lower()
units = sorted((k for k in s if k == "ace2k" or k.lower().startswith("ace2k ")), key=unit)
print((s.get("print_stats") or {}).get("state") or "")
print(next(((s[k] or {}).get("version") or "" for k in units), ""))
print(",".join(k for k in cfg if k == "ace" or k.startswith("ace ")))
print("yes" if any(k == "ace2k" or k.startswith("ace2k ") for k in cfg) else "")
for k in units:
    o = s[k] or {}
    print("U\t%s\t%s\t%s\t%s" % (unit(k), o.get("version") or "",
          "yes" if o.get("link_proven") else "", o.get("uid") or ""))')
    { IFS= read -r PRINT_STATE; IFS= read -r RUNNING; IFS= read -r OTHER; IFS= read -r HAS_ACE2K; } <<EOF
$st
EOF
    if [ -n "${WORK:-}" ] && [ -d "$WORK" ]; then
        printf '%s\n' "$st" | awk -F '\t' -v OFS='\t' '$1 == "U" { print $2, $3, $4, $5 }' >"$WORK/kunits"
    fi
    return 0 # the last, empty lines end the input early
}

# The state file: the releases the last finished run installed, and in_progress=<flow> (install,
# update or remove) from a run's first change until it finishes, so the next run can continue it.
state_get() { # KEY → its value in the state file ('' when absent)
    [ -f "$STATE_DIR/state" ] || return 0
    sed -n "s/^$1=//p" "$STATE_DIR/state" | tail -n 1
}

state_write() { # a finished run
    mkdir -p "$STATE_DIR"
    printf 'ace2k=%s\nace2k_u1=%s\ndate=%s\n' "$ACE2K_TAG" "$U1_TAG" "$(date +%Y-%m-%dT%H:%M:%S)" >"$STATE_DIR/state"
}

mark_in_progress() { # flow ('' clears the mark) — the versions already recorded stay
    if ! { grep -v '^in_progress=' "$STATE_DIR/state" 2>/dev/null; [ -z "$1" ] || echo "in_progress=$1"; } >"$STATE_DIR/state.new" ||
        ! mv "$STATE_DIR/state.new" "$STATE_DIR/state"; then
        die "cannot write $STATE_DIR/state"
    fi
}

# --- the units ---
#
# A unit is one [ace2k NAME] section of ace2k.cfg (NAME empty for an unnamed [ace2k]) and the
# serial: of its [mcu]; a cable is one /dev/serial/by-id path of the kind in the unit's cable.
# The files below hold one tab-separated record per line.  A cable path has no spaces, so a list
# of cables splits on whitespace safely.

cfgtool() { "$PYTHON" -I "$CFGTOOL" "$@"; }

# Reads [ace2k NAME] and follows its mcu: (the cfg tool's units); cfg_mcu_serials reads the
# [mcu ace2k…] sections instead, for the start checks and the removal, which have no cfg tool.
read_units() { # [after] — ace2k.cfg's units, joined with Klipper's view → $WORK/units
    # after: the run has changed something already, so its stop does not say "nothing changed"
    : >"$WORK/units"
    [ -f "$KCFG/ace2k.cfg" ] || return 0
    sfx='; nothing changed'
    [ -z "${1:-}" ] || sfx=''
    cfgtool units "$KCFG/ace2k.cfg" >"$WORK/units.cfg" || die "cannot read $KCFG/ace2k.cfg$sfx"
    [ -f "$WORK/kunits" ] || : >"$WORK/kunits"
    # name, serial, uid from the file; version, link proven from Klipper when it reports the unit
    awk -F '\t' -v OFS='\t' 'FILENAME == ARGV[1] { k[$1] = $2 OFS $3; next }
        { print $1, $2, $3, ($1 in k) ? k[$1] : OFS }' "$WORK/kunits" "$WORK/units.cfg" >"$WORK/units"
}

# ace2k.cfg → one "name<US>serial" record per [mcu ace2k…] section with a serial:, in name order
# (name empty for [mcu ace2k]).  Shell, not the cfg tool: a removal does not fetch the release,
# and the start checks (units_firmware) run before it is fetched.  It reads the [mcu] sections and
# names a unit after its mcu section; read_units (cfgtool units) reads the [ace2k NAME] sections
# and follows their mcu:.  The two agree while every [ace2k NAME] has mcu: ace2k_NAME.
cfg_mcu_serials() {
    [ -f "$KCFG/ace2k.cfg" ] || return 0
    awk -v us="$US" '
        /^\[/ { s = tolower($0); sub(/[#;].*$/, "", s); gsub(/[ \t]+/, " ", s); sub(/ $/, "", s)
                m = (s ~ /^\[mcu ace2k(_[a-z0-9_-]+)?\]$/)
                n = s; sub(/^\[mcu ace2k_?/, "", n); sub(/\]$/, "", n) }
        m && /^serial[ \t]*[:=]/ { v = $0; sub(/^serial[ \t]*[:=][ \t]*/, "", v)
                                   sub(/[ \t]+[#;].*$/, "", v); sub(/[ \t]+$/, "", v); print n us v }
    ' "$KCFG/ace2k.cfg" | LC_ALL=C sort
}

read_cables() { # the cables of the kind in the unit's cable → CABLES, one per line
    CABLES=''
    for p in "$BYID"/usb-1a86_USB_Single_Serial_*; do
        [ -e "$p" ] && CABLES="$CABLES$p
"
    done
}

real_paths() { # one path per line on stdin → its real path per line ('' for an empty line)
    "$PYTHON" -I -c '
import os, sys
for line in sys.stdin:
    p = line.rstrip("\n")
    print(os.path.realpath(p) if p else "")'
}

match_units() { # → GONE (names with no cable, - for an unnamed unit), NEW (cables no unit names)
    # Each serial and each cable is resolved once, all in one Python start (slow on the U1), then
    # the real paths are compared as strings.  awk reads the files by name: an empty one is skipped.
    printf '%s' "$CABLES" | sed '/^$/d' >"$WORK/cables"
    { cut -f2 "$WORK/units" && cat "$WORK/cables"; } | real_paths >"$WORK/paths.real"
    nser=$(grep -c '' "$WORK/units")
    head -n "$nser" "$WORK/paths.real" >"$WORK/serials.real"
    tail -n +"$((nser + 1))" "$WORK/paths.real" >"$WORK/cables.real"
    GONE=$(awk -F '\t' 'FILENAME == ARGV[1] { c[$0] = 1; next }
        FILENAME == ARGV[2] { r[FNR] = $0; next }
        ($1 $2) != "" && (r[FNR] == "" || !(r[FNR] in c)) { print ($1 == "" ? "-" : $1) }' \
        "$WORK/cables.real" "$WORK/serials.real" "$WORK/units"
        echo .)
    NEW=$(awk 'FILENAME == ARGV[1] { if ($0 != "") s[$0] = 1; next }
        FILENAME == ARGV[2] { r[FNR] = $0; next }
        !(r[FNR] in s) { print }' "$WORK/serials.real" "$WORK/cables.real" "$WORK/cables"
        echo .)
    GONE=${GONE%.} NEW=${NEW%.} # the dot kept the last newline through $(...)
}

free_names() { # → the names a–d no unit takes, in order (TAKEN: the names in use, comma-separated)
    for n in $NAMES_ALL; do case ",${TAKEN:-}," in *",$n,"*) ;; *) printf '%s ' "$n" ;; esac; done
}

ask_name() { # cable → appends "name<TAB>cable" to $WORK/names, the name added to TAKEN
    cable=$1
    # shellcheck disable=SC2046 # the free names are single letters
    set -- $(free_names)
    list=$(echo "$@" | sed 's/ /, /g')
    while :; do
        ask_again "The unit whose LEDs blink now: which name? ($list) [$1]"
        n=$(printf '%s' "${ANSWER:-$1}" | tr -d ' ' | tr '[:upper:]' '[:lower:]')
        case " $* " in *" $n "*) [ -z "$n" ] || break ;; esac
        case ",${TAKEN:-}," in
        *",$n,"*) say "$n is taken." ;;
        *) say "$n is not one of: $list." ;;
        esac
    done
    printf '%s\t%s\n' "$n" "$cable" >>"$WORK/names"
    TAKEN="${TAKEN:-},$n"
}

# The release's flasher: the one this run installs, there before anything is written.
release_flasher() { printf '%s\n' "${FLASHER:-$WORK/host/firmware/tools/ace2k_flash.pyz}"; }

# cable → IDENT_RC: the flasher's --identify status (0 blinks, 5 cannot blink).  A unit blinks
# for at most BLINK_S seconds once the flasher returns, so the next blink waits until that is
# over: two units blinking at once could not be told apart.  BLINK_END is in whole seconds, one
# more than needed, so a return late in its second still waits the whole blink.
run_identify() {
    if [ -n "${BLINK_END:-}" ] && [ "$(date +%s)" -lt "$BLINK_END" ]; then
        say "Waiting for the LEDs of the last unit to stop."
        while [ "$(date +%s)" -lt "$BLINK_END" ]; do sleep 1; done
    fi
    PYTHONUNBUFFERED=1 "$PYTHON" "$(release_flasher)" --identify --port "$1" </dev/null
    IDENT_RC=$?
    if [ "$IDENT_RC" -eq 0 ] && [ "$BLINK_S" -gt 0 ]; then BLINK_END=$(($(date +%s) + BLINK_S + 1)); fi
}

name_new() { # NEW cables, TAKEN → $WORK/names ("name<TAB>cable", sorted by name), TAKEN
    # shellcheck disable=SC2086 # cable paths have no spaces
    set -- $NEW
    if [ $# -ge 2 ] && [ "$YES" -eq 1 ]; then
        die "$# units have no name yet: run this from a terminal to name the units (their LEDs blink); nothing changed"
    fi
    : >"$WORK/names"
    [ $# -gt 0 ] || return 0
    noblink=''
    while [ $# -gt 1 ]; do
        say "Blinking the unit on $1."
        run_identify "$1"
        case $IDENT_RC in
        0) ask_name "$1" ;;
        5) noblink="$noblink $1" ;;
        "$FLASH_NO_ANSWER") die "Nothing answered on $1: check that unit's power and its cable; nothing changed" ;;
        *) die "The flasher could not reach the unit on $1 (status $IDENT_RC, see above); nothing changed" ;;
        esac
        shift
    done
    pool="$noblink $1" # the last cable, never blinked: it may be told by its cable too
    # shellcheck disable=SC2086 # cable paths have no spaces
    set -- $pool
    while [ $# -gt 1 ]; do
        name_by_unplug "$pool"
        named=$(tail -n 1 "$WORK/names" | cut -f2)
        pool=$(for c in $pool; do [ "$c" = "$named" ] || printf '%s ' "$c"; done)
        # shellcheck disable=SC2086 # cable paths have no spaces
        set -- $pool
    done
    rest=$1
    # shellcheck disable=SC2046 # the free names are single letters
    set -- $(free_names)
    printf '%s\t%s\n' "$1" "$rest" >>"$WORK/names" # one cable left: the first free name, no question
    TAKEN="${TAKEN:-},$1"
    say "The unit on $rest is $1."
    sort -o "$WORK/names" "$WORK/names"
}

name_by_unplug() { # candidates (space-separated) → one of them named by the cable the user unplugs
    candidates=$1
    # shellcheck disable=SC2046 # the free names are single letters
    set -- $(free_names)
    want=$1
    say "A unit here cannot blink (recovery, or an older ace2k)."
    while :; do
        ask_again "Unplug the USB cable of the unit that should be $want, then press Enter."
        gone=''
        for c in $candidates; do [ -e "$c" ] || gone="$gone $c"; done
        # shellcheck disable=SC2086 # cable paths have no spaces
        set -- $gone
        [ $# -ne 1 ] || break
        if [ $# -eq 0 ]; then
            say "No cable of a unit without a name was unplugged."
        else
            say "$# cables were unplugged: plug them back in, then unplug only one."
        fi
    done
    printf '%s\t%s\n' "$want" "$1" >>"$WORK/names"
    TAKEN="${TAKEN:-},$want"
    until [ -e "$1" ]; do ask_again "Plug it back in, then press Enter."; done
}

ask_lanes() { # UNITS_NAMES (a,b) [HEADS_NOW: four entries, one per line] → LANES
    if [ "$YES" -eq 0 ]; then
        say "Which lanes feed each head? One lane per head, as a:1. Two or more lanes on the same"
        say "head, through a splitter, are joined by +, as a:1+a:2. - leaves the head to the U1."
    fi
    used='' LANES='' h=1
    while [ "$h" -le 4 ]; do
        today=$(printf '%s\n' "${HEADS_NOW:-}" | sed -n "${h}p")
        # today's entry while it is still taken (a unit gone, a lane taken: not), else the next
        # free lane; one cfg tool start per head, as a Python start is slow on the U1
        sug=$(cfgtool head-default "$today" "$UNITS_NAMES" "$used") || die "lanes: cannot work out head $h"
        if [ "$YES" -eq 1 ]; then
            ANSWER=''
        else
            ask_again "Head $h (T$((h - 1))): which lanes feed it? [$sug]"
        fi
        if [ -z "$ANSWER" ]; then
            got=$sug # checked already
        elif ! got=$(cfgtool check-head "$ANSWER" "$UNITS_NAMES" "$used" 2>"$WORK/why"); then
            say "$(cat "$WORK/why")"
            continue
        fi
        [ "$got" = - ] || used="$used,$(printf '%s' "$got" | tr + ,)"
        LANES="${LANES:+$LANES, }$got"
        h=$((h + 1))
    done
}

# --- the start checks ---

check_paxx() {
    paxx=$(cat "$BUILD_VERSION" 2>/dev/null)
    case $paxx in
    "$TESTED_PAXX" | "$TESTED_PAXX"-*) ;;
    *paxx*)
        warn "paxx $paxx: only $TESTED_PAXX was tested."
        confirm "Continue on an untested paxx version?" || die "Stopped; nothing changed."
        ;;
    *) die "This printer does not run the paxx extended firmware (${paxx:-no $BUILD_VERSION}). ace2k needs it: docs/install.md, Prerequisites; nothing changed." ;;
    esac
}

check_debug_flag() { # asked now, created after the final confirmation
    [ ! -e "$DEBUG_FLAG" ] || return 0
    say "/oem/.debug is missing. Without it the U1 wipes ace2k's files from its overlay at the next boot."
    say "The installer can create it now; no reboot is needed afterwards."
    confirm "Create /oem/.debug?" || die "Without /oem/.debug the files would be lost at the next boot; nothing changed."
    CREATE_DEBUG=1
}

create_debug_flag() {
    [ "$CREATE_DEBUG" -eq 1 ] && [ ! -e "$DEBUG_FLAG" ] || return 0
    : >"$DEBUG_FLAG" || die "cannot create /oem/.debug"
    say "$DEBUG_MSG"
}

detect_installed() { # → INSTALLED, UNFINISHED, and what was found said
    INSTALLED=0 U1_OLD=$(state_get ace2k_u1)
    U1_OLD=${U1_OLD#v}
    for p in "$EXTRAS/ace2k.py" "$EXTRAS/ace2k_u1.py" "$KCFG/ace2k.cfg" "$KCFG/ace2k-u1.cfg" "$WEB" "$NGINX_CONF"; do
        [ ! -e "$p" ] || INSTALLED=1
    done
    UNFINISHED=$(state_get in_progress)
    if [ -n "$UNFINISHED" ]; then
        INSTALLED=1
    elif [ "$INSTALLED" -eq 1 ] && [ ! -f "$STATE_DIR/state" ]; then
        say "ace2k is installed, but not by this installer (a manual install). Firmware on $(units_firmware)."
    elif [ "$INSTALLED" -eq 1 ]; then
        say "ace2k is installed: ace2k-u1 ${U1_OLD:-unknown}, firmware on $(units_firmware)."
    fi
}

# → "the unit: VERSION" for an unnamed unit; "the units: a VERSION, b VERSION" for named ones, or
# "not read yet" when Klipper was not asked for them by name (the cfg tool comes with the release)
units_firmware() {
    [ -f "$WORK/kunits" ] || : >"$WORK/kunits"
    cfg_mcu_serials >"$WORK/fw.units"
    list=$(awk -v us="$US" 'FILENAME == ARGV[1] { split($0, f, "\t"); v[f[1]] = f[2]; next }
        { split($0, f, us); if (f[1] == "") next
          n++; if (f[1] in v) k++
          out = out (n > 1 ? ", " : "") f[1] " " ((f[1] in v && v[f[1]] != "") ? v[f[1]] : "unknown") }
        END { if (n) print (k ? out : "-") }' "$WORK/kunits" "$WORK/fw.units")
    case $list in
    '') printf 'the unit: %s' "${RUNNING:-unknown (Klipper is $KSTATE)}" ;;
    -) printf 'the units: not read yet (Klipper was not asked for them by name)' ;;
    *) printf 'the units: %s' "$list" ;;
    esac
}

choose_action() {
    case $ACTION in
    update) [ "$INSTALLED" -eq 1 ] || die "ace2k is not installed: run this without --update to install it; nothing changed" ;;
    remove) [ "$INSTALLED" -eq 1 ] || { say "ace2k is not installed: nothing to remove." && exit 0; } ;;
    '')
        if [ "$INSTALLED" -eq 0 ]; then
            ACTION=install
        elif [ "$UNFINISHED" = remove ]; then
            say "A previous removal did not finish."
            confirm "Continue it?" || die "Stopped; nothing changed."
            ACTION=remove CONTINUED=remove
        elif [ -n "$UNFINISHED" ]; then
            say "A previous run did not finish."
            confirm "Continue it?" || die "Stopped; nothing changed. To take ace2k off instead, run this with --remove."
            ACTION=update
            # an install that stopped before its .cfg files were written is finished as one
            if [ "$UNFINISHED" = install ] && { [ ! -f "$KCFG/ace2k.cfg" ] || [ ! -f "$KCFG/ace2k-u1.cfg" ]; }; then
                ACTION=install
            fi
        elif [ "$YES" -eq 1 ]; then
            die "ace2k is installed: give --update or --remove together with --yes; nothing changed"
        else
            ask "Update (u), remove (r) or cancel (c)? [c]"
            case $ANSWER in
            u | U) ACTION=update ;;
            r | R) ACTION=remove ;;
            *) die "Update or removal canceled; nothing changed." ;;
            esac
        fi
        ;;
    esac
}

preflight() {
    check_paxx
    KSTATE=$(klippy_state)
    [ -n "$KSTATE" ] || die "Moonraker does not answer at $MOON: wait until the printer has started, then try again; nothing changed"
    read_status
    case $PRINT_STATE in
    printing | paused) die "A print is running or paused: run this when the printer is idle; nothing changed" ;;
    esac
    [ -z "$OTHER" ] || die "Another ACE driver is configured in Klipper ([$OTHER]). Remove it with its own uninstaller first (docs/install.md, \"Remove another ACE driver first\"); nothing changed"
    [ -n "$FLASHER" ] || "$PYTHON" -c 'import serial' 2>/dev/null || die "python3 has no pyserial here (Klipper's own dependency); nothing changed"
    detect_installed
    choose_action
    [ "$ACTION" = remove ] || check_debug_flag
}

# --- the release ---

fetch_file() { # base name dest — one file from a directory or a URL
    case $1 in
    http://* | https://* | file://*) curl -fsSL --max-time 120 -o "$3" "$1/$2" 2>/dev/null ;;
    *) [ -f "$1/$2" ] && cp "$1/$2" "$3" ;;
    esac
}

not_installable() { # what file
    die "Release $1 cannot be installed by install.sh: it lacks $2. Follow docs/install.md by hand for it; nothing changed."
}

# Not called inside $(...): its die must end the run, not a subshell.
latest_tag() { # repo → LATEST, the newest release's tag ('' when none): drafts dropped, pre-releases with --pre
    code=$(curl -sSL --max-time 30 -o "$REL/releases.json" -w '%{http_code}' "$GH_API/repos/$1/releases?per_page=20" 2>/dev/null)
    case $code in
    200) ;;
    403 | 429) die "GitHub's API refused the request (HTTP $code): its hourly limit of unauthenticated calls was probably reached. Try again in an hour; nothing changed" ;;
    *) die "GitHub's API did not answer for $1 (HTTP ${code:-none}); nothing changed" ;;
    esac
    LATEST=$(pyjson "[r['tag_name'] for r in d if not r.get('draft') and ($PRE or not r.get('prerelease'))][0]" <"$REL/releases.json")
}

get_checked() { # base sums name what — fetch one file and check it against its SHA256SUMS line
    fetch_file "$1" "$3" "$REL/$3" || not_installable "$4" "$3"
    want=$(awk -v f="$3" '$2 == f || $2 == "*" f { print $1; exit }' "$2")
    [ -n "$want" ] || die "$3 has no line in the SHA256SUMS of $4; nothing changed"
    got=$(sha256 "$REL/$3")
    [ "$got" = "$want" ] || die "$3: checksum mismatch (got $got, SHA256SUMS says $want); nothing changed"
    say "$3: checksum OK"
}

check_tarball() { # file — refuse absolute paths and any '..' component
    list=$(tar -tzf "$1" 2>/dev/null) || die "$(basename "$1") is not a readable tarball; nothing changed"
    bad=$(printf '%s\n' "$list" | awk '/^\// || /(^|\/)\.\.(\/|$)/' | head -n 3 | tr '\n' ' ')
    [ -z "$bad" ] || die "$(basename "$1") holds paths outside its own tree ($bad); nothing changed"
}

fetch_ace2k_half() {
    ACE2K_V=${ACE2K_TAG#v}
    if [ -z "$FROM" ]; then
        ACE2K_BASE=$GH_DL/$ACE2K_REPO/releases/download/$ACE2K_TAG
        fetch_file "$ACE2K_BASE" SHA256SUMS "$REL/ace2k.sums" || not_installable "$ACE2K_REPO $ACE2K_TAG" SHA256SUMS
    fi
    get_checked "$ACE2K_BASE" "$REL/ace2k.sums" "ace2k-$ACE2K_V.bin" "$ACE2K_REPO $ACE2K_TAG"
    get_checked "$ACE2K_BASE" "$REL/ace2k.sums" "ace2k-host-$ACE2K_V.tar.gz" "$ACE2K_REPO $ACE2K_TAG"
}

fetch_release() { # the release's files in $WORK/release, each checked; then both bundles unpacked
    REL=$WORK/release
    mkdir -p "$REL"
    if [ -n "$FROM" ]; then
        U1_BASE=$FROM ACE2K_BASE=$FROM
        fetch_file "$FROM" SHA256SUMS "$REL/u1.sums" || die "$FROM has no SHA256SUMS; nothing changed"
        cp "$REL/u1.sums" "$REL/ace2k.sums"
        req=$(sed -n 's/^[0-9a-f]\{64\}  *\*\{0,1\}\(ace2k-u1-.*\)\.requires$/\1/p' "$REL/u1.sums" | head -n 1)
        [ -n "$req" ] || die "The SHA256SUMS in $FROM names no ace2k-u1-*.requires file; nothing changed"
        U1_V=${req#ace2k-u1-}
        if [ -n "$U1_TAG" ] && [ "${U1_TAG#v}" != "$U1_V" ]; then
            die "--version $U1_TAG does not match $FROM, which holds ace2k-u1 v$U1_V: drop --version or point --from at that release; nothing changed"
        fi
        U1_TAG=v$U1_V
    else
        if [ -z "$U1_TAG" ]; then
            latest_tag "$U1_REPO"
            U1_TAG=$LATEST
            if [ -z "$U1_TAG" ] && [ "$PRE" -eq 1 ]; then
                die "No release of $U1_REPO was found; nothing changed"
            fi
            [ -n "$U1_TAG" ] || die "No release of $U1_REPO that is not a pre-release was found; --pre includes pre-releases; nothing changed."
        fi
        U1_V=${U1_TAG#v}
        U1_BASE=$GH_DL/$U1_REPO/releases/download/$U1_TAG
        [ -z "$ACE2K_TAG" ] || fetch_ace2k_half
        fetch_file "$U1_BASE" SHA256SUMS "$REL/u1.sums" || not_installable "$U1_REPO $U1_TAG" SHA256SUMS
    fi
    get_checked "$U1_BASE" "$REL/u1.sums" "ace2k-u1-$U1_V.requires" "$U1_REPO $U1_TAG"
    [ -n "$ACE2K_TAG" ] || ACE2K_TAG=$(awk '$1 == "ace2k" { print $2; exit }' "$REL/ace2k-u1-$U1_V.requires")
    [ -n "$ACE2K_TAG" ] || die "ace2k-u1-$U1_V.requires names no ace2k release; nothing changed"
    get_checked "$U1_BASE" "$REL/u1.sums" "ace2k-u1-$U1_V.tar.gz" "$U1_REPO $U1_TAG"
    [ -f "$REL/ace2k-host-${ACE2K_TAG#v}.tar.gz" ] || fetch_ace2k_half
    ACE2K_V=${ACE2K_TAG#v}
    BIN=$REL/ace2k-$ACE2K_V.bin
    check_tarball "$REL/ace2k-host-$ACE2K_V.tar.gz"
    check_tarball "$REL/ace2k-u1-$U1_V.tar.gz"
    mkdir -p "$WORK/host" "$WORK/u1"
    { tar -xzf "$REL/ace2k-host-$ACE2K_V.tar.gz" -C "$WORK/host" && tar -xzf "$REL/ace2k-u1-$U1_V.tar.gz" -C "$WORK/u1"; } ||
        die "the release's bundles do not extract; nothing changed"
    [ -n "$CFGTOOL" ] || CFGTOOL=$WORK/u1/tools/ace2k_cfg.py
    [ -f "$CFGTOOL" ] || not_installable "$U1_REPO $U1_TAG" tools/ace2k_cfg.py
    [ -f "$WORK/host/firmware/tools/ace2k_flash.pyz" ] ||
        die "Release $ACE2K_REPO $ACE2K_TAG has no ace2k_flash.pyz in ace2k-host-$ACE2K_V.tar.gz. This installer needs a release whose ace2k carries ace2k_flash.pyz. Nothing changed."
    { cp -R "$WORK/u1/web" "$WORK/web" && mv "$WORK/web/nginx/ace2k.conf" "$WORK/nginx-ace2k.conf" && rm -rf "$WORK/web/nginx"; } ||
        die "the release's web page is incomplete; nothing changed"
    say "Release: ace2k $ACE2K_V ($ACE2K_REPO $ACE2K_TAG) and ace2k-u1 $U1_V ($U1_REPO $U1_TAG)."
}

# --- the files ---
#
# sync_files compares the release with the printer once and lists each difference in
# $WORK/changes (kind, source, destination); write_files makes exactly those changes, each file
# copied into the run's backup first.

own_lava() { [ -n "$ROOT" ] || chown lava:lava "$@" 2>/dev/null || true; }

start_changes() { # flow — after the confirmation: the backup directory, the mark, /oem/.debug
    bk=$STATE_DIR/backup/$(date +%Y%m%d-%H%M%S)-$$
    mkdir -p "$bk" || die "cannot create $bk"
    BK=$bk
    mark_in_progress "$1"
    create_debug_flag
}

backup() { # path — copy it into the backup, below its full path, before it changes
    [ -e "$1" ] || return 0
    [ ! -e "$BK$1" ] || return 0
    { mkdir -p "$BK$(dirname "$1")" && cp -pR "$1" "$BK$1"; } || die "cannot back up $1"
}

plan() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$WORK/changes"; } # kind src dest

put_file() { # src dest
    if [ -f "$2" ] && [ "$(sha256 "$1")" = "$(sha256 "$2")" ]; then return 0; fi
    plan file "$1" "$2"
}

tree_sum() { # dir → one line per file: its path inside dir and its SHA-256
    [ -d "$1" ] || return 0
    (cd "$1" && find . -type f ! -path '*/__pycache__/*' | LC_ALL=C sort |
        while IFS= read -r f; do printf '%s %s\n' "$f" "$(sha256 "$f")"; done)
}

put_tree() { # src dest — dest replaced by a copy of src
    [ "$(tree_sum "$1")" != "$(tree_sum "$2")" ] || return 0
    plan tree "$1" "$2"
}

put_modules() { # src_dir glob [exclude_glob] — the release's modules in, the ones it dropped out
    for f in "$1"/$2; do
        [ ! -f "$f" ] || put_file "$f" "$EXTRAS/$(basename "$f")"
    done
    for f in "$EXTRAS"/$2; do
        [ -f "$f" ] || continue
        n=$(basename "$f")
        # shellcheck disable=SC2254 # the exclusion is a glob on purpose ('/' matches no file name)
        case $n in ${3:-/}) continue ;; esac
        [ -f "$1/$n" ] || plan remove - "$f"
    done
}

# The release cfg's sections that the operator's cfg lacks, each with the comment block above
# its header (one with a blank line before it, so not the end of the section above), separated
# by one blank line. A header the operator
# commented out (#[...]) counts as present: removing a section was the operator's choice.
cfg_lacks() { # release_cfg operators_cfg → stdout
    # the operator's file is told apart by its name: FNR == NR would also take an empty one's
    # place with the release's lines. Through the environment: -v would read a backslash in it
    CFG_MINE="$2" awk '
        function name(l) { sub(/\].*/, "]", l); gsub(/[ \t]+/, " ", l); sub(/^\[ /, "[", l); sub(/ \]$/, "]", l); return l }
        FILENAME == ENVIRON["CFG_MINE"] { l = $0; sub(/^[#; \t]*/, "", l); if (l ~ /^\[/) have[name(l)] = 1; next }
        { line[++n] = $0; if ($0 ~ /^\[/) hdr[++m] = n }
        END {
            for (k = 1; k <= m; k++) {
                s = hdr[k]
                b = s  # blank lines between a free-standing comment block and the header
                while (b > 1 && line[b - 1] ~ /^[ \t]*$/) b--
                if (b < s && b > 1 && line[b - 1] ~ /^#/) s = b
                while (s > 1 && line[s - 1] ~ /^#/) s--
                if (s < hdr[k] && s > 1 && line[s - 1] !~ /^[ \t]*$/) s = hdr[k]  # comments of the section above
                start[k] = s
            }
            for (k = 1; k <= m; k++) {
                if (name(line[hdr[k]]) in have) continue
                e = (k < m) ? start[k + 1] - 1 : n
                while (e > hdr[k] && line[e] ~ /^[ \t]*$/) e--
                print ""
                for (j = start[k]; j <= e; j++) print line[j]
            }
        }' "$2" "$1"
}

# A new install writes no .cfg here: paxx includes every .cfg in extended/klipper, and an
# [mcu ace2k] with no ace2k unit behind it stops Klipper at its next start.  write_cfgs writes
# them once every unit is flashed.
sync_files() { # new | update → $WORK/changes and CHANGED; writes nothing on the printer
    : >"$WORK/changes"
    put_modules "$WORK/host/klippy/extras" 'ace2k*.py' 'ace2k_u1*'
    put_tree "$WORK/host/klippy/extras/ace2k_tags" "$EXTRAS/ace2k_tags"
    put_modules "$WORK/u1/klippy/extras" 'ace2k_u1*.py'
    if [ "$1" = update ]; then # the operator's .cfg files are kept; a missing ace2k-u1.cfg is written
        put_file "$WORK/host/config/ace2k_tags.cfg" "$KCFG/ace2k_tags.cfg"
        : >"$WORK/u1-cfg-adds"
        if [ -f "$KCFG/ace2k-u1.cfg" ]; then # the sections it lacks added at its end
            cfg_lacks "$WORK/u1/config/ace2k-u1.cfg" "$KCFG/ace2k-u1.cfg" >"$WORK/u1-cfg-adds"
            if [ -s "$WORK/u1-cfg-adds" ]; then
                { awk 1 "$KCFG/ace2k-u1.cfg" && cat "$WORK/u1-cfg-adds"; } >"$WORK/ace2k-u1.cfg"
                put_file "$WORK/ace2k-u1.cfg" "$KCFG/ace2k-u1.cfg"
            fi
        else
            put_file "$WORK/u1/config/ace2k-u1.cfg" "$KCFG/ace2k-u1.cfg"
        fi
    fi
    put_tree "$WORK/web" "$WEB"
    put_file "$WORK/nginx-ace2k.conf" "$NGINX_CONF"
    CHANGED=0
    [ ! -s "$WORK/changes" ] || CHANGED=1
}

# The nginx snippet goes in only while nginx's own check passes; if it fails because of the new
# snippet, the previous one is put back (or the new one removed).
put_nginx() { # src
    if ! "$NGINX" -t >/dev/null 2>&1; then
        warn "nginx -t fails before any change: nginx's configuration is already broken elsewhere. The web page's snippet was not changed."
        WEB_PENDING=1
        return 0
    fi
    backup "$NGINX_CONF"
    put_atomic "$1" "$NGINX_CONF" || die "cannot write $NGINX_CONF"
    if ! "$NGINX" -t >/dev/null 2>&1; then
        if [ -e "$BK$NGINX_CONF" ]; then cp -p "$BK$NGINX_CONF" "$NGINX_CONF"; else rm -f "$NGINX_CONF"; fi
        die "nginx -t refuses the new snippet: the previous one was put back, so nginx stays valid."
    fi
    "$NGINX" -s reload >/dev/null 2>&1 || warn "nginx did not reload: run nginx -s reload"
}

apply_changes() { # the changes listed in $WORK/changes, each backed up first
    tab=$(printf '\t')
    while IFS=$tab read -r kind src dest; do
        if [ "$dest" = "$NGINX_CONF" ]; then
            put_nginx "$src"
            continue
        fi
        backup "$dest"
        case $kind in
        # a+r, a+rX: root's umask on the printer is 0077, and neither Klipper (it runs as lava) nor
        # nginx can read a file that only root reads
        file) { mkdir -p "$(dirname "$dest")" && put_atomic "$src" "$dest" && chmod a+r "$dest"; } ||
            die "cannot write $dest" ;;
        tree) { rm -rf "$dest" && cp -R "$src" "$dest" && chmod -R a+rX "$dest"; } || die "cannot write $dest" ;;
        remove) rm -f "$dest" || die "cannot remove $dest" ;;
        esac
    done <"$WORK/changes"
    page_readable
}

# The page's modes are set on every install and update, also with nothing to do. put_tree compares
# only contents, so a page that an older install left to root alone would never be repaired.
page_readable() {
    [ ! -d "$WEB" ] || chmod -R a+rX "$WEB" || die "cannot set the modes of $WEB"
}

write_cfgs() { # new install, after the flashes: ace2k.cfg (the units), ace2k_tags.cfg, ace2k-u1.cfg
    : >"$WORK/changes"
    set --
    while IFS=$US read -r n c; do [ -z "$n" ] || set -- "$@" "$n=$c"; done <<EOF
$(tr '\t' "$US" <"$WORK/names")
EOF
    cfgtool render "$WORK/host/config/ace2k.cfg" "$@" >"$WORK/ace2k.cfg" || die "cannot write ace2k.cfg's units"
    cfgtool set-lanes "$WORK/u1/config/ace2k-u1.cfg" "$LANES" >"$WORK/ace2k-u1.cfg" || die "cannot write lanes: into ace2k-u1.cfg"
    put_file "$WORK/ace2k.cfg" "$KCFG/ace2k.cfg"
    put_file "$WORK/host/config/ace2k_tags.cfg" "$KCFG/ace2k_tags.cfg"
    put_file "$WORK/ace2k-u1.cfg" "$KCFG/ace2k-u1.cfg"
    apply_changes
    own_lava "$KCFG/ace2k_tags.cfg" "$KCFG/ace2k.cfg" "$KCFG/ace2k-u1.cfg"
    read_units after # $WORK/units now lists the units just written
}

write_uids() { # once Klipper reports the units: uid: into every unit section without one
    cp "$KCFG/ace2k.cfg" "$WORK/uid.cfg" || die "cannot read $KCFG/ace2k.cfg"
    changed=''
    while IFS=$US read -r n _s u _v _p; do
        [ -z "$u" ] || continue # a uid: that is there never changes
        k=$(awk -F '\t' -v n="$n" '$1 == n { print $4 }' "$WORK/kunits")
        if [ -z "$k" ]; then
            warn "Klipper does not report the uid of unit ${n:-$UNNAMED}: its uid: is not written."
            continue
        fi
        { cfgtool uid "$WORK/uid.cfg" "$n" "$k" >"$WORK/uid.new" && mv "$WORK/uid.new" "$WORK/uid.cfg"; } ||
            die "cannot write the uid: of unit ${n:-$UNNAMED}"
        changed="$changed ${n:-$UNNAMED}"
    done <<EOF
$(tr '\t' "$US" <"$WORK/units")
EOF
    [ -n "$changed" ] || return 0
    if [ "$MODE" != new ]; then # a new install's own file needs no copy
        [ -n "$BK" ] || start_changes update # an update with nothing else to do
        backup "$KCFG/ace2k.cfg"
    fi
    put_atomic "$WORK/uid.cfg" "$KCFG/ace2k.cfg" || die "cannot write $KCFG/ace2k.cfg"
    own_lava "$KCFG/ace2k.cfg"
    say "uid: written for $(printf '%s\n' "${changed# }" | word_list) (Klipper checks it at its next start; no restart needed)."
}

calibration_copy_in() { # copies kept elsewhere before this installer existed (never moved)
    mkdir -p "$STATE_DIR/calibration"
    for f in "$ROOT"/root/ace-calibration-*.txt "$ROOT"/root/ace2k-flash/ace-calibration-*.txt \
        "$ROOT"/root/ace-calibration/ace-calibration-*.txt; do
        [ -f "$f" ] || continue
        [ -e "$STATE_DIR/calibration/$(basename "$f")" ] || cp -p "$f" "$STATE_DIR/calibration/"
    done
}

write_files() { # the planned changes, then the flasher and the image
    apply_changes
    rm -rf "$EXTRAS/__pycache__"/ace2k* "$EXTRAS/ace2k_tags/__pycache__"
    own_lava "$KCFG/ace2k_tags.cfg" "$KCFG/ace2k-u1.cfg" # an update may write either
    mkdir -p "$STATE_DIR/flash"
    cp "$WORK/host/firmware/tools/ace2k_flash.pyz" "$BIN" "$STATE_DIR/flash/" ||
        die "cannot copy the flasher and the image to $STATE_DIR/flash"
    rm -f "$STATE_DIR/flash/$OLD_FLASHER" # an older install's flasher: the .pyz replaces it
    IMG=$STATE_DIR/flash/ace2k-$ACE2K_V.bin
    calibration_copy_in
}

# --- Klipper, the flasher, the checks ---

klipper_info() { # → KSTATE_NOW and KPID ('' when Klipper is down); one Python start for both
    j=$(mr /printer/info | pyjson 'd["result"]["state"]' 'd["result"].get("process_id") or ""')
    { IFS= read -r KSTATE_NOW; IFS= read -r KPID; } <<EOF
$j
EOF
    return 0 # the last, empty line ends the input early
}

console_newest() { mr '/server/gcode_store?count=100' | pyjson 'max([e["time"] for e in d["result"]["gcode_store"]] or [0])'; }

console_since() { # mark → the console lines newer than it, one per line, without Klipper's "// "
    mr '/server/gcode_store?count=100' |
        pyjson "chr(10).join(e['message'] for e in d['result']['gcode_store'] if e['time'] > $1)" |
        sed 's#^// ##'
}

# Before anything that restarts Klipper: the process that runs now, and the newest console line.
# Afterwards only another Klipper counts: another process, or the same process seen not ready in
# between (FIRMWARE_RESTART and RESTART reload Klipper inside its own process).
mark_klipper() {
    klipper_info
    KPID_BEFORE=$KPID SEEN_DOWN=0
    CONSOLE_MARK=$(console_newest)
    [ -n "$CONSOLE_MARK" ] || CONSOLE_MARK=0
}

# poll TRIES CMD… — run CMD until it returns 0 (done) or 2 (a final no), at most TRIES times
poll() {
    poll_n=$1 poll_i=0
    shift
    while [ "$poll_i" -lt "$poll_n" ]; do
        "$@"
        poll_rc=$?
        [ "$poll_rc" -eq 1 ] || return "$poll_rc"
        sleep "$POLL_S"
        poll_i=$((poll_i + 1))
    done
    return 1
}

klipper_look() { # one look: 0 when Klipper is ready and is not the marked one; its status read
    klipper_info
    if [ "$KSTATE_NOW" != ready ]; then
        SEEN_DOWN=1
        restart_after_error
        return 1
    fi
    [ -z "$KPID_BEFORE" ] || [ "$KPID" != "$KPID_BEFORE" ] || [ "$SEEN_DOWN" -eq 1 ] || return 1
    read_status
}

removed_seen() { klipper_look && [ -z "$HAS_ACE2K" ]; }

klipper_gone() { klipper_info && [ -z "$KPID" ]; }

stop_klipper_once() { # before the first flash: the flasher then finds each port free
    [ "${KLIPPER_STOPPED:-0}" -eq 0 ] || return 0
    say "Stopping Klipper (/etc/init.d/S60klipper stop)."
    "$KLIPPER_INIT" stop >/dev/null 2>&1 || warn "S60klipper stop exited with an error."
    # a Klipper still running would hold the ports, and the flasher would stop and start it itself
    if ! poll "$CHECK_TRIES" klipper_gone; then
        AFTER="Klipper was not stopped, so it is not started again. Find out why /etc/init.d/S60klipper stop leaves it running, then run the installer again."
        die "Klipper still answers after its stop (/etc/init.d/S60klipper stop): no unit was written."
    fi
    KLIPPER_STOPPED=1
}

start_klipper() { # after the config: the one start of the run
    mark_klipper
    ERROR_RESTARTED=0
    say "Starting Klipper (/etc/init.d/S60klipper start)."
    "$KLIPPER_INIT" start >/dev/null 2>&1 || warn "S60klipper start exited with an error."
    KLIPPER_STOPPED=0
}


# The units of $WORK/units that Klipper does not report at version $1 (and, with $2, with their
# link proven), one name per line ('ace2k' for an unnamed unit).
units_not_at() {
    awk -F '\t' -v want="$1" -v proven="${2:-}" -v unnamed="$UNNAMED" 'FILENAME == ARGV[1] { v[$1] = $2; p[$1] = $3; next }
        !(($1 in v) && v[$1] == want && (proven == "" || p[$1] == "yes")) { print ($1 == "" ? unnamed : $1) }' \
        "$WORK/kunits" "$WORK/units"
}

units_seen() { # want — one look: 0 once Klipper is ready and reports every unit at want
    klipper_look || return 1
    [ -z "$(units_not_at "$1")" ] || return 2 # a new Klipper, ready: its answer is final
}
wait_units() { poll "$WAIT_TRIES" units_seen "$1"; }

proven_now() { # want — 0 when $WORK/kunits has every unit at want with its link proven → BAD
    BAD=$(units_not_at "$1" proven | word_list)
    [ -z "$BAD" ]
}
units_proven() { read_status && proven_now "$1"; } # want — one query, then proven_now

klipper_restart() {
    mark_klipper
    ERROR_RESTARTED=0 # each start may need the one more restart
    say "Restarting Klipper (/etc/init.d/S60klipper restart)."
    "$KLIPPER_INIT" restart >/dev/null 2>&1 || warn "S60klipper restart exited with an error."
}

# A Klipper that finds the unit holding another Klipper's config resets the unit and reloads
# inside its own process; on the U1 that reload stops with an error (a pin of the U1's own reader
# still held).  The unit is fresh by then, so one more start through the init script connects.
restart_after_error() {
    [ "$KSTATE_NOW" = error ] && [ "$ERROR_RESTARTED" -eq 0 ] && [ -n "$KPID" ] && [ "$KPID" != "$KPID_BEFORE" ] || return 0
    say "Klipper stopped with an error while starting; on the U1 this follows a reset of the unit from inside Klipper. One more restart."
    klipper_restart
    ERROR_RESTARTED=1
    poll_i=-1 # poll counts this look next: the new start gets the whole wait
}

run_flasher() { # args… → FLASH_RC; the flasher reads the operator's answers from the terminal
    set -- "${FLASHER:-$STATE_DIR/flash/ace2k_flash.pyz}" "$@" --calibration-dir "$STATE_DIR/calibration"
    [ "$YES" -eq 0 ] || set -- "$@" --yes
    mkdir -p "$STATE_DIR/calibration"
    if [ "$HAVE_TTY" -eq 1 ]; then
        PYTHONUNBUFFERED=1 "$PYTHON" "$@" <&3
    else
        PYTHONUNBUFFERED=1 "$PYTHON" "$@" </dev/null
    fi
    FLASH_RC=$?
}

flasher_takes() { # option [flasher] — 0 when the flasher's --help lists it (an older one lacks some)
    "$PYTHON" "${2:-${FLASHER:-$STATE_DIR/flash/ace2k_flash.pyz}}" --help 2>/dev/null | grep -qF -e "$1"
}

flash_failed() { # die starts Klipper again when the run stopped it, so it is usable on its old config
    [ "$FLASH_RC" -ne "$FLASH_NO_ANSWER" ] || die "Nothing answered on $FLASH_PORT: the unit may be off or unplugged, or not on this port. The flasher changed nothing on it."
    [ "$FLASH_RC" -ne "$FLASH_UNCHANGED" ] || die "The flasher changed nothing on the unit (see above)."
    die "The flash did not finish (the flasher's status $FLASH_RC). The flasher's message above says what state the unit is in. If it says to power-cycle the unit, do that first."
}

flash_units() { # $WORK/flash ("name<TAB>cable", in name order) → each flashed, or the run stops
    flashed=''
    while IFS=$US read -r n c; do
        [ -n "$c" ] || continue
        n=${n:-$UNNAMED}
        if [ ! -e "$c" ]; then # the installer's own words: not the flasher's for a missing path
            flashed_say
            die "The cable of unit $n ($c) is no longer plugged in: plug it back in. The flasher changed nothing on that unit."
        fi
        say "Flashing unit $n ($c) with ace2k $ACE2K_V."
        FLASH_PORT=$c
        run_flasher "$IMG" --port "$c" --config-follows
        case $FLASH_RC in
        "$FLASH_OK" | "$FLASH_UNVERIFIED") flashed="$flashed $n" ;;
        *)
            flashed_say
            flash_failed
            ;;
        esac
    done <<EOF
$(tr '\t' "$US" <"$WORK/flash")
EOF
}

flashed_say() { # the units flash_units flashed before its stop
    # shellcheck disable=SC2086 # the names are single words
    for d in $flashed; do say "Before this stop, unit $d is flashed with ace2k $ACE2K_V."; done
}

gcode() { # script → GCODE_OUT, the console lines it added; 1 with GCODE_ERR when it did not run
    GCODE_OUT='' GCODE_ERR=''
    m=$(console_newest)
    code=$(curl -sS --max-time 30 -o "$WORK/gcode.json" -w '%{http_code}' -X POST "$MOON/printer/gcode/script?script=$1" 2>/dev/null)
    case $code in
    200) GCODE_OUT=$(console_since "${m:-0}") ;;
    000 | '') GCODE_ERR="Moonraker did not answer" ;;
    *) GCODE_ERR="Moonraker answered HTTP $code: $(pyjson 'd["error"]["message"]' <"$WORK/gcode.json")" ;;
    esac
    [ -z "$GCODE_ERR" ]
}

# Right after a start ACEK_HEALTH answers "not run yet", so a check is asked again until it
# settles: the expected line, or FAILING, for about 30 s at most.
# Several units answer one line each: a check settles once that many lines are good, or on a
# FAILING line.
acek_try() { # script sed_expr good count — one ask; 0 once settled
    if gcode "$1"; then
        err=''
        got=$(printf '%s\n' "$GCODE_OUT" | sed -n "$2")
        [ -z "$got" ] || lines=$got
        good=$(printf '%s\n' "$lines" | grep -cxF -e "$3")
        [ "$good" -lt "$4" ] || return 0
        ! printf '%s\n' "$lines" | grep -q '^FAILING' || return 0
    else
        err=$GCODE_ERR
    fi
    return 1
}

acek_check() { # script sed_expr good count — asked until settled, then its outcome reported
    lines='' err='' good=0
    poll "$CHECK_TRIES" acek_try "$@"
    if [ "$good" -ge "$4" ]; then
        say "$1: $3"
    elif [ -n "$lines" ]; then
        printf '%s\n' "$lines" | grep -vxF -e "$3" >"$WORK/acek.bad"
        while IFS= read -r l; do
            warn "$1: $l (expected: $3; run $1 in Klipper's console for the details)"
        done <"$WORK/acek.bad"
        [ -s "$WORK/acek.bad" ] || warn "$1: $good of $4 units report $3 (run $1 in Klipper's console for the details)"
    elif [ -n "$err" ]; then
        warn "$1 did not run: $err"
    else
        warn "$1 gave no answer: run it in Klipper's console."
    fi
}

# --- the end of a run ---

prune_backups() { # drop the empty ones (runs that changed no file), then keep the five newest
    for d in "$STATE_DIR"/backup/*/; do [ "${d%/}" = "$BK" ] || rmdir "$d" 2>/dev/null; done
    # shellcheck disable=SC2012
    ls -1d "$STATE_DIR"/backup/*/ 2>/dev/null | LC_ALL=C sort -r | awk 'NR > 5' |
        while IFS= read -r d; do [ "${d%/}" = "$BK" ] || rm -rf "$d"; done
}

finish() {
    state_write
    prune_backups
    [ -f "$WORK/units" ] || read_units after
    count=$(grep -c '' "$WORK/units")
    [ "$count" -ge 1 ] || count=1
    names=$(cut -f1 "$WORK/units" | sed "s/^\$/$UNNAMED/" | word_list)
    acek_check ACEK_HEALTH 's/^\(ace2k [^:]*: \)\{0,1\}health: //p' OK "$count"
    # the look that saw the version may already show every link proven: no query then
    if proven_now "$ACE2K_V" || poll "$CHECK_TRIES" units_proven "$ACE2K_V"; then
        say "Klipper reports ace2k $ACE2K_V with its link proven on every unit ($names)."
    else
        warn "Not at ace2k $ACE2K_V with its link proven: $BAD (run ACEK_STATUS in Klipper's console)."
    fi
    if console_since "$CONSOLE_MARK" | grep -q 'ace2k_u1: hooked lanes'; then
        say "The adapter is attached (ace2k_u1: hooked lanes)."
    else
        warn "The console does not show 'ace2k_u1: hooked lanes' yet: check ACEK_ADAPTER_STATUS."
    fi
    say "Done: Klipper reports ace2k $ACE2K_V on every unit ($names), with ace2k-u1 $U1_V."
    if [ "$WEB_PENDING" -eq 1 ]; then # the next run finishes the page
        mark_in_progress update
        warn "The web page was not installed: nginx's configuration is broken. Fix it (nginx -t), then run the installer again."
    else
        say "The web page: http://<printer>/ace2k/"
    fi
    say "Check the adapter with ACEK_ADAPTER_STATUS in Klipper's console."
    if shared_head "$KCFG/ace2k-u1.cfg"; then
        say "Measure each shared head's path to its splitter and set headN_splitter_mm (see docs/configuration.md)."
    fi
    say "Keep a copy of the unit's factory calibration on your computer:"
    say "  scp 'root@<printer>:/home/lava/printer_data/ace2k/calibration/ace-calibration-*.txt' ."
}

shared_head() { # true when the cfg's lanes: puts two lanes on one head, as Klipper reads it
    # the unit's name only names a bare lane; any name does to find a +
    cfgtool heads "$1" "$UNNAMED_RENAMED" 2>/dev/null | grep -q '+'
}

# --- the new install ---

# The release's flasher must reach ace2k over the cable with Klipper stopped, which came with
# --identify.  An older one ends on the free port with its 4, or on an option it does not know
# with argparse's 2, read as "flashed".
need_cable_flasher() {
    flasher_takes --identify "$(release_flasher)" ||
        die "The flasher of $ACE2K_REPO $ACE2K_TAG cannot reach ace2k over the cable (it has no --identify): this installer needs a newer ace2k release; nothing changed"
}

flow_install() {
    MODE=new
    fetch_release
    need_cable_flasher
    if [ -n "$PORT" ]; then CABLES=$PORT; else read_cables; fi # --port: one unit, on that cable
    [ -n "$CABLES" ] || die "No adapter of the kind in the unit's cable under /dev/serial/by-id (usb-1a86_USB_Single_Serial_*): plug in the unit's cable and turn the unit on (ace2k's docs/cable.md); nothing changed"
    n=$(printf %s "$CABLES" | grep -c .)
    [ "$n" -le 4 ] || die "$n unit cables under /dev/serial/by-id (usb-1a86_USB_Single_Serial_*): ace2k drives at most four. Unplug the ones to leave out and run this again; nothing changed."
    NEW=$CABLES TAKEN=''
    name_new
    UNITS_NAMES=$(cut -f1 "$WORK/names" | comma_join)
    HEADS_NOW=''
    ask_lanes
    cp "$WORK/names" "$WORK/flash"
    say "Plan:"
    while IFS=$US read -r n c; do [ -z "$n" ] || say "  - unit $n: $c, flashed with ace2k $ACE2K_V"; done <<EOF
$(tr '\t' "$US" <"$WORK/names")
EOF
    say "  - write ace2k $ACE2K_V's host modules and ace2k-u1 $U1_V's adapter into $EXTRAS"
    say "  - write the web page into $WEB and its nginx snippet into $NGINX_CONF, then reload nginx"
    [ "$CREATE_DEBUG" -eq 0 ] || say "  - create /oem/.debug"
    say "  - stop Klipper, then flash the units one at a time (the flasher shows what it found and asks again)"
    say "  - then write ace2k.cfg (one section per unit), ace2k_tags.cfg and ace2k-u1.cfg (lanes: $LANES) into $KCFG"
    say "  - start Klipper once, check every unit, then write each unit's uid:"
    confirm "Install?" || die "Not confirmed; nothing changed."
    start_changes install
    sync_files new
    write_files
    stop_klipper_once
    flash_units
    write_cfgs
    start_klipper
    wait_units "$ACE2K_V" || die "Klipper did not report ace2k $ACE2K_V on every unit ($UNITS_NAMES). Klipper's log: /oem/klippylogs/klippy.log."
    write_uids
    finish
}

# --- the update ---
#
# The units come from ace2k.cfg, so a Klipper that is down hides none.  Every question comes
# before the plan: a unit that is gone, a new unit to add, lanes: when one is added.  The config
# edits wait in $WORK/edits ("drop NAME" or "add NAME CABLE") until the flashes are done.

handle_gone() { # GONE → stop, or (yes) each one out of the config: $WORK/edits gains "drop NAME"
    [ -n "$GONE" ] || return 0
    for n in $GONE; do
        s=$(awk -F '\t' -v n="${n#-}" '$1 == n { print $2 }' "$WORK/units")
        [ "$n" != - ] || n=$UNNAMED
        say "Unit $n ($s) is not plugged in, or is off."
    done
    # shellcheck disable=SC2086 # the names are single words
    if [ "$(printf '%s\n' $GONE | grep -c .)" -eq "$(grep -c '' "$WORK/units")" ]; then
        die "No cable of a unit in $KCFG/ace2k.cfg was found: check the units' cables and their power, then run this again. To take ace2k off instead, run this with --remove; nothing changed"
    fi
    [ "$YES" -eq 0 ] || die "A unit of the config is not plugged in: plug it in and turn it on, then run this again; nothing changed"
    for n in $GONE; do
        [ "$n" != - ] || die "The unit of an unnamed [ace2k] is not plugged in: plug it in and turn it on, then run this again; nothing changed"
        confirm "Take unit $n out of the config?" || die "Plug unit $n in and turn it on, then run this again; nothing changed"
        echo "drop $n" >>"$WORK/edits"
    done
}

offer_new() { # NEW → each one blinked and offered; yes: $WORK/edits gains "add NAME CABLE"
    [ -n "$NEW" ] || return 0
    TAKEN=$(cut -f1 "$WORK/units" | sed "s/^\$/$UNNAMED_RENAMED/" | comma_join)
    for c in $NEW; do
        if [ "$YES" -eq 1 ]; then
            warn "The unit on $c is not in the config: it is left out. Run this from a terminal to add it."
            continue
        fi
        # shellcheck disable=SC2046 # the free names are single letters
        set -- $(free_names)
        [ $# -gt 0 ] || { warn "Four units already: the unit on $c is left out."; continue; }
        say "A unit on $c is not in the config. Blinking it."
        run_identify "$c"
        case $IDENT_RC in
        0) ask "Add the unit whose LEDs blink now as $1? [y/N]" ;;
        5) if confirm_by_unplug "$c" "$1"; then ANSWER=y; else ANSWER=n; fi ;;
        "$FLASH_NO_ANSWER") warn "Nothing answered on $c: that unit is left out."; continue ;;
        *) warn "The flasher could not reach the unit on $c (status $IDENT_RC, see above): it is left out."; continue ;;
        esac
        case $ANSWER in
        y | Y | yes | YES)
            echo "add $1 $c" >>"$WORK/edits"
            TAKEN="$TAKEN,$1" ADDED=1
            ;;
        *) say "The unit on $c is left out." ;;
        esac
    done
}

confirm_by_unplug() { # cable name → 0 when the user unplugs that cable to add it as name
    say "The unit on $1 cannot blink (recovery, or an older ace2k)."
    ask "To add it as $2, unplug the USB cable of the unit to add, then press Enter (or n, then Enter, to leave it out)."
    case $ANSWER in n | N | no | NO) return 1 ;; esac
    out=''
    for p in $CABLES; do [ -e "$p" ] || out="$out $p"; done
    if [ "$out" != " $1" ]; then # not exactly the new cable: no second try in this run
        say "That was not the cable of the new unit alone: it is left out."
        for p in $out; do # whatever was unplugged goes back before the update goes on
            until [ -e "$p" ]; do ask_again "Plug $p back in, then press Enter."; done
        done
        NOT_ADDED="$NOT_ADDED $1"
        return 1
    fi
    until [ -e "$1" ]; do ask_again "Plug it back in, then press Enter."; done
}

not_added_say() { # the closing lines for the units the unplug path left out
    for c in $NOT_ADDED; do say "The unit on $c was not added: run this with --update again to be offered it again."; done
}

# $WORK/units and $WORK/edits → $WORK/flash ("name<TAB>cable", in name order: the units Klipper
# does not report at the release's version, then the added ones) and one plan line per unit
plan_flashes() {
    : >"$WORK/flash"
    while IFS=$US read -r n s _u v _p; do
        if [ -z "$s" ] || grep -qx "drop $n" "$WORK/edits"; then continue; fi
        if [ -z "$n" ]; then # an unnamed [ace2k]: a once a unit is added
            n=$UNNAMED
            [ "$ADDED" -eq 0 ] || n=$UNNAMED_RENAMED
        fi
        if [ "$v" = "$ACE2K_V" ]; then
            say "  - $n: $v (already)"
        else
            say "  - $n: ${v:-unknown} → $ACE2K_V (flash)"
            printf '%s\t%s\n' "$n" "$s" >>"$WORK/flash"
        fi
    done <<EOF
$(sort "$WORK/units" | tr '\t' "$US")
EOF
    while read -r op n c; do
        [ "$op" = add ] || continue
        say "  - $n: new, on $c → $ACE2K_V (flash)"
        printf '%s\t%s\n' "$n" "$c" >>"$WORK/flash"
    done <"$WORK/edits"
}

edit_cfg() { # file command [args…] — the file rewritten in place by the cfg tool
    f=$1 cmd=$2
    shift 2
    { cfgtool "$cmd" "$f" "$@" >"$f.new" && mv "$f.new" "$f"; } || die "cannot edit $(basename "$f") ($cmd)"
}

# After the flashes: ace2k.cfg, ace2k-u1.cfg and printer.cfg edited under $WORK, then each one
# that differs backed up and written
apply_cfg_edits() {
    [ -s "$WORK/edits" ] || return 0
    { cp "$KCFG/ace2k.cfg" "$WORK/e.cfg" && cp "$KCFG/ace2k-u1.cfg" "$WORK/e-u1.cfg"; } ||
        die "cannot read the .cfg files in $KCFG"
    cp "$CFG/printer.cfg" "$WORK/e-printer.cfg" 2>/dev/null || : >"$WORK/e-printer.cfg"
    if grep -q '^add ' "$WORK/edits" && cut -f1 "$WORK/units" | grep -qx ''; then
        edit_cfg "$WORK/e.cfg" rename-a # an unnamed [ace2k] becomes unit a
        saved_rename "$WORK/e-printer.cfg"
    fi
    while read -r op n c; do
        case $op in
        add) edit_cfg "$WORK/e.cfg" add "$n" "$c" ;;
        drop)
            edit_cfg "$WORK/e.cfg" drop "$n"
            edit_cfg "$WORK/e-u1.cfg" drop-lanes "$n"
            saved_drop "$WORK/e-printer.cfg" "$n"
            ;;
        esac
    done <"$WORK/edits"
    [ -z "$LANES" ] || edit_cfg "$WORK/e-u1.cfg" set-lanes "$LANES"
    for pair in "e.cfg:$KCFG/ace2k.cfg" "e-u1.cfg:$KCFG/ace2k-u1.cfg" "e-printer.cfg:$CFG/printer.cfg"; do
        src=$WORK/${pair%%:*} dest=${pair#*:}
        if cmp -s "$src" "$dest" || { [ ! -e "$dest" ] && [ ! -s "$src" ]; }; then continue; fi
        backup "$dest"
        put_atomic "$src" "$dest" || die "cannot write $dest"
        own_lava "$dest"
    done
    read_units after
}

# [uidless] → the units' names once $WORK/edits is applied, comma-separated (an unnamed unit is a
# when a unit is added, else ace2k); uidless: only the ones whose section has no uid: yet
names_after() {
    awk -v renamed="$ADDED" -v uidless="${1:-}" -v unnamed="$UNNAMED" -v to="$UNNAMED_RENAMED" '
        FILENAME == ARGV[1] { if ($1 == "drop") d[$2] = 1; else if ($1 == "add") print $2; next }
        { split($0, f, "\t"); n = (f[1] != "") ? f[1] : (renamed ? to : unnamed)
          if (!(n in d) && (uidless == "" || f[3] == "")) print n }' "$WORK/edits" "$WORK/units" |
        sort | comma_join
}

update_plan() { # the plan's text, from what flow_update found
    say "Plan:"
    say "  - ace2k-u1 ${U1_OLD:-unknown} → $U1_V; ace2k on each unit:"
    cat "$WORK/plan.lines"
    sed -n 's/^add \([a-d]\) \(.*\)/  - add unit \1 (\2) to the config/p
        s/^drop \([a-d]\)$/  - take unit \1 out of the config (its tag cache and calibration copies stay)/p' "$WORK/edits"
    [ -z "$LANES" ] || say "  - lanes: $LANES"
    if [ "$CHANGED" -eq 1 ]; then
        kept="your ace2k.cfg is kept"
        [ ! -s "$WORK/edits" ] || kept="$kept, apart from the units above"
        if [ -s "$WORK/u1-cfg-adds" ]; then
            kept="$kept; your ace2k-u1.cfg is kept and gains $(grep '^\[' "$WORK/u1-cfg-adds" | tr '\n' ' ' | sed 's/ $//') at its end"
        elif [ -f "$KCFG/ace2k-u1.cfg" ]; then
            kept="$kept; your ace2k-u1.cfg is kept"
        else
            kept="$kept; ace2k-u1.cfg is written (none was there)"
        fi
        say "  - $kept; these files change:"
        awk -F '\t' '{ print "      " ($1 == "remove" ? "remove " : "write ") $3 }' "$WORK/changes"
    else
        say "  - The files already match."
    fi
    [ "$CREATE_DEBUG" -eq 0 ] || say "  - create /oem/.debug"
    if [ -s "$WORK/flash" ]; then
        say "  - stop Klipper, flash the units marked (flash) one at a time, start Klipper once"
    else
        say "  - restart Klipper once"
    fi
    uids=$(names_after uidless)
    [ -z "$uids" ] || say "  - once Klipper reports the units, write uid: into $(echo "$uids" | sed 's/,/, /g')"
}

flow_update() {
    MODE=update
    fetch_release
    need_cable_flasher
    [ -z "$PORT" ] || warn "--port is ignored by --update; the units come from ace2k.cfg"
    [ "$STATUS_NAMED" -eq 1 ] || read_status # the start checks could not name the units yet
    : >"$WORK/edits"
    ADDED=0 LANES='' NOT_ADDED=''
    read_units
    [ -s "$WORK/units" ] || die "No unit in $KCFG/ace2k.cfg: run this with --remove, then install again; nothing changed"
    read_cables
    match_units
    handle_gone
    offer_new
    if [ "$ADDED" -eq 1 ]; then # lanes: asked again, today's entries as the suggestions
        UNITS_NAMES=$(names_after)
        # bare lanes feed the one unit there was: its name, or a for an unnamed unit (renamed)
        bare=$(head -n 1 "$WORK/units" | cut -f1)
        HEADS_NOW=$(cfgtool heads "$KCFG/ace2k-u1.cfg" "${bare:-$UNNAMED_RENAMED}" 2>/dev/null)
        ask_lanes
    fi
    sync_files update
    plan_flashes >"$WORK/plan.lines"
    if [ "$CHANGED" -eq 0 ] && [ ! -s "$WORK/flash" ] && [ ! -s "$WORK/edits" ]; then
        create_debug_flag
        page_readable
        write_uids
        state_write
        prune_backups # write_uids may have made one
        say "Already at ace2k $ACE2K_V and ace2k-u1 $U1_V: nothing to do."
        not_added_say
        exit 0
    fi
    update_plan
    confirm "Update?" || die "Not confirmed; nothing changed."
    start_changes update
    write_files
    if [ -s "$WORK/flash" ]; then
        stop_klipper_once
        flash_units
        apply_cfg_edits
        start_klipper
    else
        apply_cfg_edits
        klipper_restart
    fi
    wait_units "$ACE2K_V" || die "Klipper did not report ace2k $ACE2K_V on every unit. Klipper's log: /oem/klippylogs/klippy.log."
    write_uids
    finish
    not_added_say
}

# --- the removal ---

# printer.cfg [section] → each line tagged k (kept) or d (dropped: the #*# [section] of its
# SAVE_CONFIG block, every [ace2k] and [ace2k NAME] when none is given, with the #*# separator
# before it)
ace2k_saved_tagged() {
    awk -v want="${2:+#*# [$2]}" '
        function flush() { if (held != "") { print "k" held; held = "" } }
        function hit() { return (want == "") ? ($0 ~ /^#\*# \[ace2k( [^]]+)?\]$/) : ($0 == want) }
        /^#\*# <-+ SAVE_CONFIG -+>$/ { block = 1 }
        !block { print "k" $0; next }
        $0 == "#*#" { flush(); held = $0; skip = 0; next }
        /^#\*# \[/ { if (hit()) { if (held != "") print "d" held; held = ""; skip = 1 } else skip = 0 }
        skip { print "d" $0; next }
        { flush(); print "k" $0 }
        END { flush() }' "$1"
}

saved_drop() { # printer.cfg name — in place: its SAVE_CONFIG block without [ace2k name]
    { ace2k_saved_tagged "$1" "ace2k $2" >"$WORK/saved.tagged" &&
        sed -n 's/^k//p' "$WORK/saved.tagged" >"$WORK/saved.new" && mv "$WORK/saved.new" "$1"; } ||
        die "cannot edit printer.cfg's SAVE_CONFIG block"
}

saved_rename() { # printer.cfg — in place: its SAVE_CONFIG block's [ace2k] becomes [ace2k a]
    { awk -v to="#*# [ace2k $UNNAMED_RENAMED]" '{ if ($0 == "#*# [ace2k]") $0 = to; print }' "$1" >"$WORK/saved.new" &&
        mv "$WORK/saved.new" "$1"; } || die "cannot edit printer.cfg's SAVE_CONFIG block"
}

strip_ace2k_saved() { # printer.cfg → stdout without them; no pipeline, so a failed awk is a failure
    tagged=$(ace2k_saved_tagged "$1") || return 1
    printf '%s\n' "$tagged" | sed -n 's/^k//p'
}

check_factory_image() {
    [ -f "$STOCK" ] || die "$STOCK: no such file; nothing changed"
    size=$(wc -c <"$STOCK" | tr -d ' ')
    sum=$(sha256 "$STOCK")
    if [ "$size" = "$FACTORY_SIZE" ] && [ "$sum" = "$FACTORY_SHA256" ]; then
        say "$STOCK: the original Anycubic firmware $FACTORY_VERSION (checksum OK)."
        return 0
    fi
    msg="$STOCK is not the original Anycubic firmware $FACTORY_VERSION this installer knows ($size bytes, SHA-256 $sum; expected $FACTORY_SIZE bytes, $FACTORY_SHA256)"
    [ "$FORCE" -eq 1 ] || die "$msg. --force accepts it with a warning; nothing changed"
    warn "$msg. Going on because of --force."
}

ensure_flasher() { # the flasher: the state directory's, an older install's, or else the release's
    [ -z "$FLASHER" ] || return 0
    [ ! -f "$STATE_DIR/flash/ace2k_flash.pyz" ] || return 0
    # an older install's flasher restores the factory firmware too, and needs no network
    if [ -f "$STATE_DIR/flash/$OLD_FLASHER" ]; then
        FLASHER=$STATE_DIR/flash/$OLD_FLASHER
        return 0
    fi
    say "No flasher in $STATE_DIR/flash: taking it from the release."
    fetch_release
    FLASHER=$WORK/host/firmware/tools/ace2k_flash.pyz
}

remove_list() { # every path ace2k and the adapter put on the printer, present ones only
    for p in "$EXTRAS"/ace2k*.py "$EXTRAS/ace2k_tags" "$KCFG/ace2k.cfg" "$KCFG/ace2k_tags.cfg" \
        "$KCFG/ace2k-u1.cfg" "$CFG"/ace2k_tags_cache*.json "$WEB" "$NGINX_CONF"; do
        [ ! -e "$p" ] || printf '%s\n' "$p"
    done
}

# Before the plan: $WORK/restore, every unit plugged in, and OLD_RESTORE; or a stop, nothing changed.
# An older install's flasher (no --identify) cannot reach ace2k with Klipper stopped: it stops
# Klipper itself and restores the one unit Klipper's config names, as it always did.
restore_check() {
    cfg_mcu_serials >"$WORK/restore"
    [ -s "$WORK/restore" ] || die "No unit with a serial: in $KCFG/ace2k.cfg to restore; nothing changed"
    while IFS=$US read -r n c; do
        [ -e "$c" ] || die "Unit ${n:-$UNNAMED} ($c) is not plugged in: plug it in, or remove without --restore-stock; nothing changed"
    done <"$WORK/restore"
    flasher_takes --identify && return 0
    [ "$(sed -n '$=' "$WORK/restore")" = 1 ] ||
        die "The flasher in $STATE_DIR/flash is from an older ace2k and restores one unit only: run this with --update first, then remove; nothing changed"
    OLD_RESTORE=1
}

restore_plan() {
    if [ "$OLD_RESTORE" -eq 1 ]; then
        say "  - flash the unit with the original Anycubic firmware $FACTORY_VERSION (the flasher asks again and compares the calibration)"
        return 0
    fi
    say "  - stop Klipper"
    while IFS=$US read -r n c; do
        say "  - flash unit ${n:-$UNNAMED} ($c) with the original Anycubic firmware $FACTORY_VERSION (the flasher asks again and compares the calibration)"
    done <"$WORK/restore"
}

# After the confirmation: each unit back on the factory firmware, in name order, with Klipper
# stopped once; or a stop with nothing removed.  No removal mark until every unit is restored
# (flow_remove's start_changes writes none): a continued removal never flashes, so a stop before
# that (a signal too) must leave the restore to the same command again, with ace2k.cfg still
# naming every unit.
restore_units() {
    calibration_copy_in
    restored=''
    if [ "$OLD_RESTORE" -eq 1 ]; then
        run_flasher "$STOCK" --version "$FACTORY_VERSION"
        if [ "$FLASH_RC" -ne "$FLASH_OK" ]; then
            AFTER="Nothing was removed, so ACEK_RESTORE_STOCK is still available: run the same command again."
            die "The restore did not finish (see the flasher's message above)."
        fi
        mark_in_progress remove
        AFTER="The unit already runs the original Anycubic firmware $FACTORY_VERSION, and Klipper is stopped (the flasher leaves it so). Run the installer again: it continues the removal and starts Klipper."
        return 0
    fi
    stop_klipper_once
    while IFS=$US read -r n c; do
        n=${n:-$UNNAMED}
        say "Restoring unit $n ($c) to the original Anycubic firmware $FACTORY_VERSION."
        run_flasher "$STOCK" --version "$FACTORY_VERSION" --port "$c"
        [ "$FLASH_RC" -eq "$FLASH_OK" ] || restore_failed "$n"
        restored="$restored $n"
    done <"$WORK/restore"
    mark_in_progress remove
    KLIPPER_STOPPED=0 # a later stop leaves Klipper stopped: no unit speaks its protocol any more
    AFTER="Every unit runs the original Anycubic firmware $FACTORY_VERSION, and Klipper is stopped. Run the installer again: it continues the removal and starts Klipper."
}

# name — that unit's restore failed.  die starts Klipper again only when no unit changed: a unit on
# the factory firmware or in recovery does not speak Klipper's protocol, so a start cannot connect.
restore_failed() {
    # shellcheck disable=SC2086 # the names are single words
    for d in $restored; do say "Before this stop, unit $d is restored to the original Anycubic firmware $FACTORY_VERSION."; done
    if [ -z "$restored" ] && { [ "$FLASH_RC" -eq "$FLASH_UNCHANGED" ] || [ "$FLASH_RC" -eq "$FLASH_NO_ANSWER" ]; }; then
        AFTER="Nothing was removed, and Klipper runs again on its config: run the same command again."
    else
        KLIPPER_STOPPED=0
        AFTER="Nothing was removed, and Klipper is left stopped: a unit may be on the original firmware or in recovery, which does not speak Klipper's protocol. Run the same command again: a restored unit is found on the original firmware and written again. Or run the installer without --remove: it puts every unit back on ace2k."
    fi
    die "The restore of unit $1 did not finish (see the flasher's message above)."
}

saved_lines() { # the SAVE_CONFIG lines the removal deletes
    [ -f "$CFG/printer.cfg" ] || return 0
    ace2k_saved_tagged "$CFG/printer.cfg" | sed -n 's/^d//p'
}

remove_files() { # no pipeline: a failed backup or removal must stop the run, not a subshell
    remove_list >"$WORK/remove.list"
    while IFS= read -r p; do
        backup "$p"
        rm -rf "$p" || die "cannot remove $p"
    done <"$WORK/remove.list"
    rm -rf "$EXTRAS/__pycache__"/ace2k*
    if [ -n "$(saved_lines)" ]; then
        backup "$CFG/printer.cfg"
        { strip_ace2k_saved "$CFG/printer.cfg" >"$WORK/printer.stripped" &&
            put_atomic "$WORK/printer.stripped" "$CFG/printer.cfg"; } || die "cannot rewrite printer.cfg"
        own_lava "$CFG/printer.cfg"
    fi
    if grep -qxF "$NGINX_CONF" "$WORK/remove.list"; then
        "$NGINX" -s reload >/dev/null 2>&1 || warn "nginx did not reload: run nginx -s reload"
    fi
}

flow_remove() {
    if [ "$CONTINUED" = remove ]; then # never a flash when a removal is continued
        STOCK=''
        say "The removal continues without a restore. If a unit's lane LEDs strobe fast, it is in recovery: run this with --remove --restore-stock FILE instead."
    elif [ -z "$STOCK" ] && [ "$YES" -eq 0 ]; then
        if confirm "Put every unit back on the original Anycubic firmware first?"; then
            ask "The path of the original Anycubic firmware file (version $FACTORY_VERSION, .bin):"
            STOCK=$ANSWER
        fi
    fi
    if [ -n "$STOCK" ]; then
        check_factory_image
        ensure_flasher
        restore_check
    fi
    say "Plan:"
    [ -z "$STOCK" ] || restore_plan
    remove_list | sed 's/^/  - remove /'
    lines=$(saved_lines)
    [ -z "$lines" ] || { say "  - delete from printer.cfg's SAVE_CONFIG block:"; printf '%s\n' "$lines" | sed 's/^/      /'; }
    if [ "$OLD_RESTORE" -eq 1 ]; then
        say "  - start Klipper (the flasher leaves it stopped)"
    elif [ -n "$STOCK" ]; then
        say "  - start Klipper"
    else
        say "  - restart Klipper once"
    fi
    confirm "Remove?" || die "Not confirmed; nothing changed."
    # with a restore, the removal mark comes only once every unit is restored (restore_units)
    if [ -n "$STOCK" ]; then start_changes ''; else start_changes remove; fi
    [ -z "$STOCK" ] || restore_units
    remove_files
    if [ -n "$STOCK" ]; then
        start_klipper
    else
        klipper_restart
    fi
    poll "$WAIT_TRIES" removed_seen || warn "Klipper is not ready without ace2k yet: see /oem/klippylogs/klippy.log"
    rm -f "$STATE_DIR/state"
    prune_backups
    say "Removed. Kept on the printer:"
    say "  - the web page's history: /home/lava/printer_data/logs/ace2k/ (delete it if you do not want it)"
    say "  - the unit's calibration copies: /home/lava/printer_data/ace2k/calibration/"
    say "  - the backup of what was removed: $BK"
}

# --- main ---

on_signal() {
    trap '' INT TERM HUP
    SIGNALLED=1
    die "Interrupted. Files may have been half written, or the unit may have been in the middle of a flash. Run the installer again. If the unit's lane LEDs strobe fast, it is in the bootloader's recovery mode: the new run (or --remove --restore-stock) handles that."
}

main() {
    # The printer's root has umask 0077, and Klipper (it runs as lava) and nginx read what this
    # writes: every file and directory is made readable by all
    umask 022
    setup_paths
    parse_args "$@"
    need_tools
    [ -n "$ROOT" ] || [ "$(id -u)" = 0 ] || die "Run this as root (ssh root@<printer>); nothing changed"
    WORK=$(mktemp -d) || die "cannot create a work directory; nothing changed"
    trap cleanup EXIT
    trap on_signal INT TERM HUP
    open_tty
    take_lock
    preflight
    "flow_$ACTION" # flow_install, flow_update or flow_remove
}

[ -n "${ACE2K_SOURCE_ONLY:-}" ] || main "$@"
