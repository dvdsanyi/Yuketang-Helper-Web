"""
Entry point for the PyInstaller-packaged Yuketang Helper application.
Starts the FastAPI server and opens the browser automatically.
"""

import os
import sys
import webbrowser
from pathlib import Path

# When frozen, add the backend directory (bundled inside _MEIPASS) to sys.path
# and set SSL certificate path so that outbound HTTPS/WSS connections work.
if getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(sys._MEIPASS) / "backend"))
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import uvicorn
from main import app  # noqa: E402

# 127.0.0.1 by default so the single-binary distribution doesn't expose the
# helper to the local network. Set HOST=0.0.0.0 to bind all interfaces.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8500"))

if __name__ == "__main__":
    webbrowser.open(f"http://localhost:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", use_colors=False)
