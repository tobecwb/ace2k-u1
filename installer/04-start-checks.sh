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

