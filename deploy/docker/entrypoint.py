#!/usr/bin/env python3
"""Prepare /data, then drop root before the bridge process starts.

The image entrypoint is root so a Fly volume mounted at /data can be chowned.
Nothing in the environment is printed.
"""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    os.chown(root, uid, gid)
    for path in root.rglob("*"):
        try:
            os.chown(path, uid, gid)
        except OSError:
            continue


def main() -> None:
    data = Path(os.environ.get("MUSE_BRIDGE_DATA_DIR", "/data"))
    data.mkdir(parents=True, exist_ok=True)
    (data / "inbox").mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        user = pwd.getpwnam("app")
        _chown_tree(data, user.pw_uid, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
        os.environ["HOME"] = user.pw_dir
    os.execvp(sys.executable, [sys.executable, "/app/server.py"])


if __name__ == "__main__":
    main()
