"""Windows side of scripts/wintest-update-e2e.sh: the checks of ROLO-0042 section 4.7 step 5.

    python e2e_check.py mark  <mark file>          record this PC's clock before a cycle
    python e2e_check.py check <settings.json>      poll up to 120 s, then print the verdict

settings.json: exe, want_sha256, mark, vault, vault_sha256, conf. Prints one JSON line of
facts, then PASS or FAIL with the reasons; exits 0 only on PASS.
"""

import hashlib
import json
import os
import subprocess
import sys
import time


def sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def processes(exe):
    """[(pid, start time as a Unix timestamp)] of processes whose image is *exe*."""
    script = ("Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $env:P }"
              " | ForEach-Object { '{0} {1}' -f $_.Id,"
              " ([DateTimeOffset]$_.StartTime).ToUnixTimeSeconds() }")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         env=dict(os.environ, P=exe), capture_output=True, text=True,
                         check=False).stdout
    return [tuple(int(v) for v in line.split()) for line in out.splitlines() if line.strip()]


def facts(s, since):
    folder = os.path.dirname(s["exe"])
    log = os.path.join(folder, ".rolodex-update-helper.log")
    try:
        with open(log, "rb") as fh:
            lines = [x.strip() for x in fh.read().decode("utf-8", "replace").splitlines()
                     if x.strip()]
    except OSError:
        lines = None
    try:
        with open(s["conf"], encoding="utf-8") as fh:
            conf = json.load(fh)
    except (OSError, ValueError) as exc:
        conf = {"unreadable": str(exc)}
    procs = processes(s["exe"])
    return {
        "exe_is_wanted": sha(s["exe"]) == s["want_sha256"],
        "new_processes": [p for p, started in procs if started >= since],
        "leftovers": sorted(n for n in os.listdir(folder) if n.startswith(".rolodex-update-")),
        "log": lines,
        "vault_unchanged": sha(s["vault"]) == s["vault_sha256"],
        "conf": conf,
    }


def verdict(f):
    problems = []
    if not f["exe_is_wanted"]:
        problems.append("the .exe is not the new build")
    if not f["new_processes"]:
        problems.append("no process started from the .exe since the cycle began")
    if f["leftovers"] != [".rolodex-update-helper.log"]:
        problems.append(f"leftovers {f['leftovers']}")
    if not f["log"] or f["log"][-1] != "swapped":
        problems.append(f"helper log {f['log']}")
    if not f["vault_unchanged"]:
        problems.append("the vault changed")
    if f["conf"].get("check_for_updates") is not True or "skipped_update_version" in f["conf"]:
        problems.append(f"settings {f['conf']}")
    return problems


def main():
    mode, arg = sys.argv[1], sys.argv[2]
    if mode == "mark":
        with open(arg, "w") as fh:
            fh.write(str(int(time.time())))
        return 0
    with open(arg, encoding="utf-8") as fh:
        s = json.load(fh)
    with open(s["mark"]) as fh:
        since = int(fh.read())
    end = time.time() + 120
    while True:
        f = facts(s, since)
        problems = verdict(f)
        if not problems or time.time() > end:
            break
        time.sleep(5)
    print(json.dumps(f))
    print("PASS" if not problems else "FAIL: " + "; ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
