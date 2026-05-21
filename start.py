import json, os, socket, subprocess, sys
from pathlib import Path
from stop import stop

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"

# Defaults: 127.0.0.1 to keep dev surface off the LAN. Override via env to expose.
HOST = os.environ.get("HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("PORT", "8500"))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "5173"))


def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def start():
    stop()
    LOG_DIR.mkdir(exist_ok=True)

    bind_host = "127.0.0.1" if HOST in ("127.0.0.1", "localhost") else "0.0.0.0"
    for name, port in [("Backend", BACKEND_PORT), ("Frontend", FRONTEND_PORT)]:
        if not _port_free(bind_host, port):
            sys.exit(
                f"Error: {name} port {port} is already in use.\n"
                f"  - Stop the conflicting process, or\n"
                f"  - Set PORT / FRONTEND_PORT env vars to alternative values."
            )

    kwargs = {"start_new_session": True} if sys.platform != "win32" else \
             {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", HOST, "--port", str(BACKEND_PORT)],
        cwd=ROOT / "backend",
        stdout=open(LOG_DIR / "backend.log", "w"),
        stderr=subprocess.STDOUT, **kwargs,
    )

    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    # Tell Vite where the backend lives so its /api and /ws proxy targets follow PORT.
    frontend_env = {**os.environ, "VITE_BACKEND_URL": f"http://{HOST}:{BACKEND_PORT}"}
    frontend = subprocess.Popen(
        [npm, "run", "dev", "--", "--host", HOST, "--port", str(FRONTEND_PORT)],
        cwd=ROOT / "frontend",
        stdout=open(LOG_DIR / "frontend.log", "w"),
        stderr=subprocess.STDOUT,
        env=frontend_env,
        **kwargs,
    )

    (LOG_DIR / "pids.json").write_text(
        json.dumps({"backend": backend.pid, "frontend": frontend.pid})
    )
    print("Yuketang Helper is running")
    print(f"  Frontend: http://{HOST}:{FRONTEND_PORT}")
    print(f"  Backend:  http://{HOST}:{BACKEND_PORT}/docs")


if __name__ == "__main__":
    start()
