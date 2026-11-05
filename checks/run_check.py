#!/usr/bin/env python3
"""Run the commands in a checks/request.yml and collect results under checks/results/<id>/.

Writes: log.txt (all output), meta.json (id, question, started/finished, exit codes, versions)
and every file listed under ``artifacts``. Secrets never go in request.yml; the workflow has
none configured on purpose.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


def main(path: str) -> int:
    req = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rid = str(req.get("id", "unnamed"))
    out = Path("checks/results") / rid
    out.mkdir(parents=True, exist_ok=True)
    log = out / "log.txt"
    env = {**os.environ, **{k: str(v) for k, v in (req.get("env") or {}).items()}}
    meta: dict[str, object] = {
        "id": rid,
        "question": req.get("question"),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "commands": [],
    }
    worst = 0
    with log.open("w", encoding="utf-8") as fh:
        for cmd in req.get("commands") or []:
            fh.write(f"\n$ {cmd}\n")
            fh.flush()
            t0 = time.monotonic()
            proc = subprocess.run(shlex.split(cmd), env=env, stdout=fh, stderr=subprocess.STDOUT)
            meta["commands"].append(  # type: ignore[attr-defined]
                {"cmd": cmd, "exit": proc.returncode, "seconds": round(time.monotonic() - t0, 1)}
            )
            worst = max(worst, proc.returncode)
    for art in req.get("artifacts") or []:
        src = Path(art)
        if src.is_file():
            shutil.copy2(src, out / src.name)
        elif src.is_dir():
            shutil.copytree(src, out / src.name, dirs_exist_ok=True)
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["exit"] = worst
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"results in {out} (exit {worst})")
    return 0  # the workflow always commits results, even on failure


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "checks/request.yml"))
