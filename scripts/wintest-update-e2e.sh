#!/usr/bin/env bash
# The live two-cycle Windows update test (ROLO-0042 section 4.7; INV-8, INV-9, INV-12).
#
#   scripts/wintest-update-e2e.sh
#
# Exit 0 = PASS (prints "PASS cycle 1" and "PASS cycle 2"), 1 = FAIL, 2 = BLOCKED (a
# precondition failed and nothing was tested). Run it before the first release carrying the
# Windows updater, and before any release that changes the Windows apply path, the helper,
# its environment or PLATFORM_ASSETS.
#
# Needs: `ssh wintest` with the desktop signed in and unlocked (shared know-how topics
# windows-test-machine and wintest-drive-qt-app-ui-automation); `gh` signed in with rights to
# dispatch workflows on milnet01/rolodex and to create releases on the public test feed
# milnet01/rolodex-update-test. Nobody needs to be at either machine.
#
# What it does: makes a throwaway signing key here; has update-test-build.yml build Windows
# test builds 0.0.1, 0.0.2 and 0.0.3 that trust it (A, B, C); signs B and C locally; installs A
# on the PC with a made-up vault; publishes B on the test feed and drives A through unlock and
# "Update Now"; checks the swap from here; then publishes C and does the same with the
# instance the helper relaunched. Cleans up everything it made, always.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
REPO="$PWD"
FEED="milnet01/rolodex-update-test"
SOURCE_REPO="milnet01/rolodex"
SCRATCH="/mnt/Emulators/.claude/Scratchpad/Rolodex/update-e2e"
SHOT="/mnt/Emulators/.claude/Scratchpad/Rolodex/update-e2e-failure.png"
TASK="RolodexUpdateE2E"
ASSET="rolodex-windows-x86_64.exe"
# Settled by measurement (2026-11-11): the offer dialog opens with focus on "Later"; Tab twice
# reaches "Update Now". Arrow keys do not move between its buttons.
OFFER_KEYS="{TAB}{TAB}{ENTER}"

say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
blocked() { say "BLOCKED: $*"; exit 2; }
wssh() { ssh -o BatchMode=yes wintest "$@"; }

# Each `if` in brackets: unbracketed, cmd makes everything after `&` part of the first `if`'s
# body, so the second folder was never checked when the first was absent.
DATA_DIRS_CMD='(if exist "%APPDATA%\Rolodex" echo %APPDATA%\Rolodex) & (if exist "%LOCALAPPDATA%\Rolodex" echo %LOCALAPPDATA%\Rolodex)'

# ---- 0. Preconditions ---------------------------------------------------------------------
command -v gh >/dev/null || blocked "needs the gh CLI"
python3 -c 'import cryptography' 2>/dev/null || blocked "needs python3 cryptography"
wssh ver >/dev/null 2>&1 || blocked "ssh wintest does not answer"
wssh 'tasklist /FI "IMAGENAME eq LogonUI.exe" /NH' | grep -qi logonui \
    && blocked "the Windows desktop is locked or signed out (LogonUI is running)"
wssh 'tasklist /FI "IMAGENAME eq rolodex*" /NH' | grep -qi rolodex \
    && blocked "a Rolodex process is already running on the PC"
existing="$(wssh "$DATA_DIRS_CMD" | tr -d '\r')"
[ -n "$existing" ] && blocked "a Rolodex data folder exists on the PC, which may hold a real vault: $existing"
gh repo view "$FEED" >/dev/null 2>&1 || blocked "the test feed $FEED does not exist"
[ -z "$(gh release list -R "$FEED" --json tagName -q '.[].tagName')" ] \
    || blocked "the test feed already has releases (a crashed run?); delete them first"

WIN_HOME="$(wssh 'echo %USERPROFILE%' | tr -d '\r')"
WIN_E2E="$WIN_HOME\\rolodex-e2e"
WIN_EXE="$WIN_E2E\\app\\$ASSET"
SCP_E2E="wintest:${WIN_E2E//\\//}"
DATA_DIR=""   # set once this run has created it; only then does cleanup delete it
RC=1

# shellcheck disable=SC2329  # runs from the EXIT trap
cleanup() {
    say "cleanup"
    wssh "taskkill /F /IM $ASSET" >/dev/null 2>&1
    sleep 2
    wssh "if exist \"$WIN_E2E\" rmdir /s /q \"$WIN_E2E\"" >/dev/null 2>&1
    if [ -n "$DATA_DIR" ]; then
        wssh "if exist \"$DATA_DIR\" rmdir /s /q \"$DATA_DIR\"" >/dev/null 2>&1
    fi
    wssh "schtasks /delete /tn $TASK /f" >/dev/null 2>&1
    for tag in v0.0.2 v0.0.3; do
        gh release delete "$tag" -R "$FEED" --cleanup-tag -y >/dev/null 2>&1
    done
    rm -rf "$SCRATCH"
}
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"
trap cleanup EXIT

# ---- 1. Test key and test builds ----------------------------------------------------------
say "1. throwaway key; dispatching test builds"
PUB="$(cd "$SCRATCH" && python3 "$REPO/scripts/gen-signing-key.py" \
       | sed -n 's/.*RELEASE_PUBLIC_KEY_B64 = "\(.*\)".*/\1/p')"
[ -n "$PUB" ] || { say "FAIL: could not make a test key"; exit 1; }
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# The workflow takes the key as hex: a base64 input made every dispatch fail at startup.
PUB_HEX="$(python3 -c 'import base64,sys;print(base64.b64decode(sys.argv[1]).hex())' "$PUB")"
gh workflow run update-test-build.yml -R "$SOURCE_REPO" --ref main -f key_hex="$PUB_HEX" \
    -f feed="$FEED" || { say "FAIL: dispatch"; exit 1; }
RUN=""
for _ in $(seq 30); do
    sleep 4
    RUN="$(gh run list -R "$SOURCE_REPO" --workflow update-test-build.yml --limit 5 \
           --json databaseId,createdAt -q "[.[] | select(.createdAt >= \"$since\")][0].databaseId")"
    [ -n "$RUN" ] && break
done
[ -n "$RUN" ] || { say "FAIL: the dispatched run never appeared"; exit 1; }
say "   run $RUN; waiting for the three Windows builds"
gh run watch "$RUN" -R "$SOURCE_REPO" --exit-status -i 30 >/dev/null \
    || { say "FAIL: test build run $RUN failed"; exit 1; }
gh run download "$RUN" -R "$SOURCE_REPO" -D "$SCRATCH/art" || { say "FAIL: download"; exit 1; }
for v in 0.0.2 0.0.3; do
    SIGNING_KEY_FILE="$SCRATCH/rolodex-signing.key" python3 - "$SCRATCH/art/rolodex-test-$v/$ASSET" <<'EOF'
import os, sys
from cryptography.hazmat.primitives import serialization
key = serialization.load_pem_private_key(open(os.environ["SIGNING_KEY_FILE"], "rb").read(), None)
path = sys.argv[1]
open(path + ".sig", "wb").write(key.sign(open(path, "rb").read()))
EOF
done

# ---- 2. Install A -------------------------------------------------------------------------
say "2. installing A (0.0.1)"
wssh "mkdir \"$WIN_E2E\\app\"" >/dev/null
scp -q "$SCRATCH/art/rolodex-test-0.0.1/$ASSET" "$SCP_E2E/app/$ASSET"
scp -q scripts/wintest/runner.ps1 scripts/wintest/e2e_check.py "$SCP_E2E/"

# ---- 3. Data folder, test vault and settings ----------------------------------------------
say "3. A --selftest, which creates the data folder"
wssh "start /wait \"\" \"$WIN_EXE\" --selftest && echo SELFTEST-OK" | grep -q SELFTEST-OK \
    || { say "FAIL: A --selftest"; exit 1; }
DATA_DIR="$(wssh "$DATA_DIRS_CMD" | tr -d '\r')"
case "$DATA_DIR" in
    *$'\n'*|"") say "FAIL: expected one data folder, found: '$DATA_DIR'"; DATA_DIR=""; exit 1 ;;
esac
say "   APP_DIR on Windows is $DATA_DIR"
PASSWORD="$(python3 -c 'import secrets,string;a=string.ascii_letters+string.digits;print("".join(secrets.choice(a) for _ in range(20)))')"
PASSWORD="$PASSWORD" python3 - "$SCRATCH" <<'EOF'
import json, os, sys, uuid
sys.path.insert(0, os.getcwd())
import rolodex
out = sys.argv[1]
vault = os.path.join(out, "contacts.vault")
data, salt = rolodex.create_vault(os.environ["PASSWORD"], vault)
for name, user in (("Example Mail", "jo@example.invalid"), ("Test Game", "player-one")):
    data["entries"][str(uuid.uuid4())] = {
        "name": name, "category": "", "notes": "made up for the ROLO-0042 test",
        "fields": [{"label": "Username", "value": user, "sensitive": False},
                   {"label": "Password", "value": "not-a-real-one", "sensitive": True}],
        "created": "2026-11-11T00:00:00", "modified": "2026-11-11T00:00:00"}
rolodex.save_vault(data, os.environ["PASSWORD"], salt, vault)
with open(os.path.join(out, ".rolodex.conf"), "w") as fh:
    json.dump({"check_for_updates": True}, fh)
EOF
SCP_DATA="wintest:${DATA_DIR//\\//}"
scp -q "$SCRATCH/contacts.vault" "$SCRATCH/.rolodex.conf" "$SCP_DATA/"
VAULT_SHA="$(sha256sum "$SCRATCH/contacts.vault" | cut -d' ' -f1)"

wssh "schtasks /create /tn $TASK /tr \"powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $WIN_E2E\\runner.ps1\" /sc once /st 23:59 /it /f" >/dev/null \
    || { say "FAIL: could not create the scheduled task"; exit 1; }

# Run one job .ps1 in the signed-in session and print its log.
run_job() {
    local ps1="$1" json="$2"
    cp "$ps1" "$SCRATCH/job.ps1"
    printf '%s' "$json" > "$SCRATCH/job.json"
    scp -q "$SCRATCH/job.ps1" "$SCRATCH/job.json" "$SCP_E2E/"
    wssh "if exist \"$WIN_E2E\\job.log\" del \"$WIN_E2E\\job.log\"" >/dev/null
    wssh "schtasks /run /tn $TASK" >/dev/null
    for _ in $(seq 120); do
        sleep 2
        if wssh "type \"$WIN_E2E\\job.log\"" 2>/dev/null | tr -d '\r' > "$SCRATCH/job.log" \
           && grep -q '^DONE$' "$SCRATCH/job.log"; then
            sed 's/^\xEF\xBB\xBF//; s/^/   | /' "$SCRATCH/job.log"
            grep -q 'FAIL' "$SCRATCH/job.log" && return 1
            return 0
        fi
    done
    say "   the job never finished"
    return 1
}

screenshot() {
    run_job scripts/wintest/screenshot.ps1 '{}' >/dev/null
    scp -q "$SCP_E2E/shot.png" "$SHOT" && say "   screenshot: $SHOT"
}

# ---- 4-6. The two cycles ------------------------------------------------------------------
cycle() {
    local n="$1" version="$2" launch="$3"
    local art="$SCRATCH/art/rolodex-test-$version"
    say "cycle $n: publishing v$version on $FEED"
    gh release create "v$version" -R "$FEED" --title "ROLO-0042 test $version" \
        --notes "Throwaway test release for the Rolodex Windows update test." \
        "$art/$ASSET" "$art/$ASSET.sig" >/dev/null || { say "FAIL: publish"; return 1; }
    for _ in $(seq 30); do
        [ "$(gh api "repos/$FEED/releases/latest" -q .tag_name 2>/dev/null)" = "v$version" ] && break
        sleep 2
    done
    wssh "python \"$WIN_E2E\\e2e_check.py\" mark \"$WIN_E2E\\mark.txt\""
    local job
    job="$(EXE="$WIN_EXE" PW="$PASSWORD" LAUNCH="$launch" KEYS="$OFFER_KEYS" python3 -c '
import json, os
print(json.dumps({"exe": os.environ["EXE"], "launch": os.environ["LAUNCH"] == "1",
                  "password": os.environ["PW"], "offer_wait_s": 15,
                  "offer_keys": os.environ["KEYS"]}))')"
    say "cycle $n: driving unlock and Update Now"
    run_job scripts/wintest/drive.ps1 "$job" || { screenshot; return 1; }
    EXE="$WIN_EXE" WANT="$(sha256sum "$art/$ASSET" | cut -d' ' -f1)" MARK="$WIN_E2E\\mark.txt" \
    VAULT="$DATA_DIR\\contacts.vault" VSHA="$VAULT_SHA" CONF="$DATA_DIR\\.rolodex.conf" \
        python3 -c '
import json, os
print(json.dumps({"exe": os.environ["EXE"], "want_sha256": os.environ["WANT"],
                  "mark": os.environ["MARK"], "vault": os.environ["VAULT"],
                  "vault_sha256": os.environ["VSHA"], "conf": os.environ["CONF"]}))' \
        > "$SCRATCH/check.json"
    scp -q "$SCRATCH/check.json" "$SCP_E2E/check.json"
    say "cycle $n: checking from Linux (up to 120 s)"
    if wssh "python \"$WIN_E2E\\e2e_check.py\" check \"$WIN_E2E\\check.json\"" | tr -d '\r' \
            | sed 's/^/   | /' | tee "$SCRATCH/check.out" && grep -q '| PASS$' "$SCRATCH/check.out"; then
        echo "PASS cycle $n"
        return 0
    fi
    screenshot
    echo "FAIL cycle $n"
    return 1
}

if cycle 1 0.0.2 1 && cycle 2 0.0.3 0; then
    RC=0
fi
exit "$RC"
