from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import asyncio
import os

load_dotenv()

from agent import run_agent

app = FastAPI(title="Trip Planner Agent")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Request schema ─────────────────────────────────────────────────────────────

class TripRequest(BaseModel):
    destination: str
    days: int
    budget: str
    currency: str = "₹"
    style: str = "Comfort"


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.post("/plan")
async def plan_trip(req: TripRequest):
    queue: asyncio.Queue = asyncio.Queue()

    async def stream():
        # kick off agent in background
        task = asyncio.create_task(
            run_agent(
                destination=req.destination,
                days=req.days,
                budget=req.budget,
                currency=req.currency,
                style=req.style,
                queue=queue,
            )
        )

        while True:
            token = await queue.get()
            if token is None:          # sentinel — agent finished
                yield "data: [DONE]\n\n"
                break
            # SSE format
            safe = token.replace("\n", "\\n")
            yield f"data: {safe}\n\n"

        await task

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Dev runner ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    key = os.getenv("GOOGLE_API_KEY")
    if not key or key == "your_gemini_api_key_here":
        print("⚠️  No GOOGLE_API_KEY found. Copy .env.example to .env and add your key.")
        print("   Get a free key at: https://aistudio.google.com/app/apikey")
    else:
        print("✅ Gemini API key loaded.")
    print("\n🌍 Trip Planner starting at http://localhost:8000\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)