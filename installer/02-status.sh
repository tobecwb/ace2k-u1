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

