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

