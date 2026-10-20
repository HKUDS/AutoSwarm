"""Build a standalone executable on the target OS using PyInstaller."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", default="dist/standalone")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "autoswarm", "--specpath", str(root / "build/standalone"), "--workpath", str(root / "build/standalone/work"), "--paths", str(root / "src"), "--add-data", str(root / "src/autoswarm/assets") + os.pathsep + "autoswarm/assets", "--distpath", args.output, str(root / "scripts/standalone_entry.py")], check=True)
