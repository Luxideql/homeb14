#!/usr/bin/env python3
"""Единый офлайн-прогон всех тестов UNI. Запуск: python3 tests_uni_offline.py."""
import glob
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.basename(__file__)


def main():
    files = sorted(f for f in glob.glob(os.path.join(BASE, "tests_*.py"))
                   if os.path.basename(f) != SELF)
    failed = []
    for f in files:
        name = os.path.basename(f)
        r = subprocess.run([sys.executable, f], cwd=BASE, capture_output=True, text=True)
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        print("%-26s %s" % (name, tail if r.returncode == 0 else "ПРОВАЛЕН"))
        if r.returncode != 0:
            failed.append(name)
            if r.stdout:
                print(r.stdout.strip())
            if r.stderr:
                print(r.stderr.strip())
    print("—" * 40)
    print("модулей тестов: %d, провалено: %d" % (len(files), len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
