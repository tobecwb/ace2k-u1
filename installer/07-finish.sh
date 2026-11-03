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

