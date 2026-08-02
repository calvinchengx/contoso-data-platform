"""List the Makefile targets and their `##` descriptions."""
import pathlib
import re

mk = (pathlib.Path(__file__).resolve().parent.parent / "Makefile").read_text()
targets = re.findall(r"^([a-z][a-z0-9-]*):.*?##\s*(.+)$", mk, re.M)
width = max(len(t) for t, _ in targets)
print("make <target>\n")
for name, desc in targets:
    print(f"  {name.ljust(width)}  {desc}")
print("\nFirst run:  make doctor")
