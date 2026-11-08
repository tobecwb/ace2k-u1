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

