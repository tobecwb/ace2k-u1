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

