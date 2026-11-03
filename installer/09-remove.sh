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

