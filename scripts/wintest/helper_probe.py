"""Windows side of scripts/wintest-helper-probe.sh (ROLO-0042 section 4.7, legs H1-H5).

Runs on the Windows test PC under its own Python, over ssh, with no GUI. Each leg runs the
exact command rolodex._windows_helper_command() built on Linux (legs.json), spawned with the
same creation flags as _apply_update_windows, against stand-in files:

  * the "running app" is a copy of cmd.exe started as `cmd /c ping -n N 127.0.0.1`, so it
    exits after about N seconds. Started bare, as the helper's relaunch does, it waits on its
    console for ever, so a relaunch shows up as a process still running that image;
  * the "update" is cmd.exe with bytes appended, so its digest differs and it still runs.

usage: python helper_probe.py <probe dir>   (holds legs.json, old.bin, new.bin)
Prints one PASS/FAIL line per leg; exits 0 only when every leg passes.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

FLAGS = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def running(exe):
    """PIDs of processes whose image is *exe* (case-insensitive, as Windows paths are)."""
    script = ("Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $env:P }"
              " | ForEach-Object { $_.Id }")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         env=dict(os.environ, P=exe), capture_output=True, text=True,
                         check=False).stdout
    return [int(x) for x in out.split()]


def kill(pids):
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)


def run_leg(base, leg):
    folder, exe, new = leg["folder"], leg["exe"], leg["new"]
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    shutil.copyfile(os.path.join(base, "old.bin"), exe)
    shutil.copyfile(os.path.join(base, "new.bin"), new)
    old_digest, new_digest = sha(exe), sha(new)
    log_path = os.path.join(folder, ".rolodex-update-helper.log")

    image = subprocess.Popen([exe, "/c", f"ping -n {leg['image_seconds']} 127.0.0.1 >nul"],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    held = open(new, "rb") if leg["hold_new"] else None  # no FILE_SHARE_DELETE: blocks a rename
    started = time.time()
    with open(log_path, "wb") as log:
        helper = subprocess.Popen(leg["command"], env=dict(os.environ,
                                  PYINSTALLER_RESET_ENVIRONMENT="1"), creationflags=FLAGS,
                                  close_fds=True, stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT)
        try:
            helper.wait(timeout=150)
        except subprocess.TimeoutExpired:
            helper.kill()
    elapsed = time.time() - started
    if held:
        held.close()
    time.sleep(1.5)  # let a relaunched process register
    alive = running(exe)
    image_alive = image.poll() is None
    with open(log_path, "rb") as fh:
        lines = [x for x in fh.read().decode("utf-8", "replace").splitlines() if x.strip()]
    last = lines[-1].strip() if lines else ""
    left = sorted(n for n in os.listdir(folder) if n.startswith(".rolodex-update-"))
    target = sha(exe) if os.path.exists(exe) else None

    facts = {"elapsed_s": round(elapsed, 1), "exit": helper.returncode, "log_last": last,
             "leftovers": left, "alive": len(alive), "image_alive": image_alive,
             "target_is": "new" if target == new_digest else "old" if target == old_digest
             else target}
    expect = leg["expect"]
    problems = []
    if facts["target_is"] != expect["target"]:
        problems.append(f"target is {facts['target_is']}, want {expect['target']}")
    if last != expect["log"]:
        problems.append(f"log ends {last!r}, want {expect['log']!r}")
    want_left = sorted(expect["leftovers"])
    if left != want_left:
        problems.append(f"left {left}, want {want_left}")
    relaunched = len(alive) - (1 if image_alive else 0) > 0
    if relaunched != expect["relaunch"]:
        problems.append(f"relaunched={relaunched}, want {expect['relaunch']}")
    kill(alive)
    image.kill()
    print(f"{'PASS' if not problems else 'FAIL'} {leg['name']}: {json.dumps(facts)}"
          + ("" if not problems else "  <- " + "; ".join(problems)), flush=True)
    if not problems:
        shutil.rmtree(folder, ignore_errors=True)
    return not problems


def main():
    base = sys.argv[1]
    with open(os.path.join(base, "legs.json"), encoding="utf-8") as fh:
        legs = json.load(fh)
    only = set(sys.argv[2:])
    results = [run_leg(base, leg) for leg in legs if not only or leg["name"] in only]
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
