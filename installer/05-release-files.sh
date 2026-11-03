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

