from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timezone

app = FastAPI(title="Zeropoint Agent API", version="1.0.0")

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
