"""A small tree of harmless local processes for test_hidden_desktop_lifecycle, holding what a browser's would. It never starts a browser, and each process ends by itself within a minute."""
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time

LIFETIME = 60


def say(path, **fields):
    draft = path.with_suffix(".draft")
    draft.write_text(json.dumps(fields))
    os.replace(draft, path)


def wait_for(path):
    deadline = time.monotonic() + LIFETIME
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Nobody answered in time.")
        time.sleep(0.01)


def start(mode, root):
    return subprocess.Popen([sys.executable, "-B", __file__, mode, str(root)], creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    mode, root = sys.argv[1], Path(sys.argv[2])
    if mode == "grandchild":
        # What a browser holds on its profile, apart from CrossCast's own lock.
        with (root / "browser.lock").open("w+b") as lock:
            lock.write(b"x")
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            say(root / "grandchild.json", pid=os.getpid())
            time.sleep(LIFETIME)
    elif mode == "child":
        start("grandchild", root)
        wait_for(root / "grandchild.json")
        say(root / "child.json", pid=os.getpid())
        time.sleep(LIFETIME)
    else:
        from video_publisher.browser_runtime import profile_lock
        with profile_lock("douyin", "lifecycle-test"):
            start("child", root)
            wait_for(root / "child.json")
            say(root / "worker.json", pid=os.getpid())
            wait_for(root / "finish")
        say(root / "finished.json", pid=os.getpid())


if __name__ == "__main__":
    main()
