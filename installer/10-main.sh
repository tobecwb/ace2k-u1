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
