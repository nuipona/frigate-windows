"""TEMPORARY CI diagnostic: print suite failures as workflow annotations."""

import pathlib
import sys

log = pathlib.Path("suite.log")
if not log.exists():
    print("::error::suite.log was not created")
    sys.exit(0)

lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
tail = [ln.replace("%", "%25") for ln in lines[-12:]]
print("::error::suite tail:%0A" + "%0A".join(tail))
shown = 0
for ln in lines:
    if ln.startswith(("FAIL: ", "ERROR: ")) and shown < 9:
        print(f"::error::{ln}")
        shown += 1
sys.exit(0)
