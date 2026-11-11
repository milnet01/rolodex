#!/usr/bin/env bash
# Run the Windows update helper for real on the Windows test PC, with no GUI (ROLO-0042
# section 4.7, legs H1-H5; INV-5, INV-6, INV-7). Re-run it on any change to
# _windows_helper_command() in rolodex.py.
#
#   scripts/wintest-helper-probe.sh [H1 H2 ...]      # default: every leg
#
# Needs `ssh wintest` (shared know-how topic windows-test-machine) and Python on that PC.
# Builds each leg's command on Linux from rolodex.py itself, copies it over with the stand-in
# files, and runs scripts/wintest/helper_probe.py there. Prints PASS/FAIL per leg; exits 0
# only when every leg passes. H2 waits out the helper's 60 s cap, so a full run takes ~2 min.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
SCRATCH="/mnt/Emulators/.claude/Scratchpad/Rolodex/helper-probe"
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"
trap 'rm -rf "$SCRATCH"' EXIT

ssh -o BatchMode=yes wintest ver >/dev/null
WIN_HOME="$(ssh -o BatchMode=yes wintest "echo %USERPROFILE%" | tr -d '\r')"
WIN_BASE="$WIN_HOME\\rolodex-probe"

# Stand-ins: the PC's own cmd.exe as the old binary, and cmd.exe with bytes appended as the
# update, so the two digests differ and both still run.
scp -q "wintest:C:/Windows/System32/cmd.exe" "$SCRATCH/old.bin"
cp "$SCRATCH/old.bin" "$SCRATCH/new.bin"
printf 'ROLO-0042 helper probe update\n' >> "$SCRATCH/new.bin"

WIN_BASE="$WIN_BASE" python3 - "$SCRATCH" <<'EOF'
import hashlib, json, ntpath, os, sys
sys.path.insert(0, os.getcwd())
import rolodex

scratch = sys.argv[1]
base = os.environ["WIN_BASE"]
new_digest = hashlib.sha256(open(os.path.join(scratch, "new.bin"), "rb").read()).hexdigest()
LOG = ".rolodex-update-helper.log"
STAGED = ".rolodex-update-probe"


def leg(name, folder_name, *, seconds=5, hold=False, digest=new_digest, expect):
    folder = ntpath.join(base, folder_name)
    exe = ntpath.join(folder, "rolodex-windows-x86_64.exe")
    new = ntpath.join(folder, STAGED)
    command = rolodex._windows_helper_command(exe, new, digest)
    command[0] = "powershell.exe"  # resolved on Linux; let Windows find its own
    return {"name": name, "folder": folder, "exe": exe, "new": new, "command": command,
            "image_seconds": seconds, "hold_new": hold, "expect": expect}


legs = [
    leg("H1", "h1", expect={"target": "new", "log": "swapped", "leftovers": [LOG],
                            "relaunch": True}),
    leg("H2", "h2", seconds=100, expect={"target": "old", "log": "abort: image still running",
                                        "leftovers": [LOG], "relaunch": False}),
    leg("H3", "h3", hold=True, expect={"target": "old", "log": "restored",
                                       "leftovers": [LOG, STAGED], "relaunch": True}),
    leg("H4", "h4", digest="0" * 64, expect={"target": "old",
                                             "log": "abort: staged bytes changed",
                                             "leftovers": [LOG], "relaunch": True}),
    leg("H5", "it's a probe dir", expect={"target": "new", "log": "swapped",
                                          "leftovers": [LOG], "relaunch": True}),
]
with open(os.path.join(scratch, "legs.json"), "w", encoding="utf-8") as fh:
    json.dump(legs, fh, indent=1)
EOF

cp "$REPO/scripts/wintest/helper_probe.py" "$SCRATCH/"
ssh -o BatchMode=yes wintest "if exist \"$WIN_BASE\" rmdir /s /q \"$WIN_BASE\"& mkdir \"$WIN_BASE\""
scp -q "$SCRATCH"/{legs.json,old.bin,new.bin,helper_probe.py} "wintest:${WIN_BASE//\\//}/"
rc=0
ssh -o BatchMode=yes wintest "python \"$WIN_BASE\\helper_probe.py\" \"$WIN_BASE\" $*" || rc=$?
ssh -o BatchMode=yes wintest "rmdir /s /q \"$WIN_BASE\"" || true
exit "$rc"
