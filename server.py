import os
import sys
from pathlib import Path

# Ensure `src/` is on sys.path so we can import our package during development
sys.path.insert(0, str(Path(__file__).resolve().parent.joinpath("src")))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timezone

from zeropoint_agent.state_store import StateStore

app = FastAPI(title="Zeropoint Agent API", version="1.0.0")


@app.on_event("startup")
def _init_state_store():
    """Initialize the StateStore at ./data/store and attach to app.state."""
    store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
    store_dir = Path(store_path) / "data" / "store"
    # create state store (will initialize git repo if missing)
    state_store = StateStore(path=str(store_dir))
    # attach for handlers and executors to use
    app.state.state_store = state_store

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# Serve the built web UI from `webui/dist` at the application root.
# If a file isn't found, StaticFiles will fall back to `index.html` when
# `html=True`, enabling SPA client-side routing.
app.mount("/", StaticFiles(directory="webui/dist", html=True), name="webui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=2370)
