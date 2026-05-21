import shutil
from pathlib import Path
from stop import stop

ROOT = Path(__file__).resolve().parent


def reset():
    stop()
    for d in ("logs", "store"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
        print(f"Removed {d}/")


if __name__ == "__main__":
    reset()
