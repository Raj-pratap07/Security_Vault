"""Check that the frozen demo files are unchanged (compares against MANIFEST.sha256)."""
import hashlib
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent.parent.parent
bad = 0
for line in (pathlib.Path(__file__).with_name("MANIFEST.sha256")).read_text().splitlines():
    digest, name = line.split(" ", 1)
    name = name.lstrip("*")  # sha256sum marks binary-mode entries with a leading *
    path = root / name
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
    status = "ok     " if actual == digest else "CHANGED"
    bad += status != "ok     "
    print(status, name)
print("\nAll frozen files unchanged." if not bad else f"\n{bad} file(s) differ from the frozen version.")
sys.exit(1 if bad else 0)
