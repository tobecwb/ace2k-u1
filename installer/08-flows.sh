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

