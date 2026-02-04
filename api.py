# api.py
import uuid
import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional
import os

# --- Import from your updated sentinel_probe.py ---
# Make sure sentinel_probe.py is in the same directory
from sentinel_probe import get_current_state, get_historical_state, analyze_diff

app = FastAPI(title="Sentinel API")

# --- Configuration ---
# Define where your prompt YAML files live. 
# In production, these might be loaded from S3 or a config map.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_STATE_PATH = os.path.join(BASE_DIR, "prompt_state.yaml")
PROMPT_DIFF_PATH = os.path.join(BASE_DIR, "prompt_diff.yaml")

# --- Simple Database (In-Memory for Vibe Coding Phase) ---
JOBS_DB = {}

class AnalyzeRequest(BaseModel):
    url: str
    months: int = 6

class JobResponse(BaseModel):
    job_id: str
    status: str
    submitted_at: str

# --- The Worker Function ---
async def run_sentinel_worker(job_id: str, url: str, months: int):
    """
    Background worker that orchestrates the scraping and AI analysis.
    """
    print(f"[{job_id}] Starting Sentinel job for {url}")
    JOBS_DB[job_id]["status"] = "scraping_current"

    try:
        # 1. Scrape Current State (Async)
        # using the async function from sentinel_probe
        current_md = await get_current_state(url)

        # 2. Scrape Historical State (Sync -> Async Wrapper)
        JOBS_DB[job_id]["status"] = "scraping_history"
        
        # Since get_historical_state uses 'requests' (synchronous), 
        # we run it in a thread to avoid blocking the API event loop.
        loop = asyncio.get_running_loop()
        old_md, snapshot = await loop.run_in_executor(
            None, 
            get_historical_state, 
            url, 
            months
        )

        if not old_md:
            # Note: You might want to proceed with just current_md in a future version
            # to establish a baseline, but for now we report the error.
            print(f"[{job_id}] No history found.")
            JOBS_DB[job_id]["status"] = "failed"
            JOBS_DB[job_id]["error"] = "No historical snapshot found for comparison."
            return

        # 3. Analyze (Async)
        JOBS_DB[job_id]["status"] = "analyzing_intelligence"
        
        # Call the new 3-step async pipeline
        analysis_result = await analyze_diff(
            old_md=old_md, 
            new_md=current_md,
            state_prompt_path=PROMPT_STATE_PATH,
            diff_prompt_path=PROMPT_DIFF_PATH
        )

        # 4. Save Results
        JOBS_DB[job_id]["status"] = "completed"
        JOBS_DB[job_id]["result"] = analysis_result
        JOBS_DB[job_id]["snapshot_url"] = snapshot
        JOBS_DB[job_id]["completed_at"] = datetime.now().isoformat()
        
        print(f"[{job_id}] Job Complete. Strategic Shift Detected: {analysis_result.get('analysis', {}).get('change_detected', 'Unknown')}")

    except Exception as e:
        print(f"[{job_id}] Failed: {e}")
        JOBS_DB[job_id]["status"] = "failed"
        JOBS_DB[job_id]["error"] = str(e)

# --- Endpoints ---

@app.post("/analyze", response_model=JobResponse)
async def start_analysis(request: AnalyzeRequest, background_tasks: BackgroundTasks):
    """
    Kick off a new Sentinel analysis job.
    """
    job_id = str(uuid.uuid4())

    # Initialize Job Record
    JOBS_DB[job_id] = {
        "id": job_id,
        "url": request.url,
        "status": "pending",
        "submitted_at": datetime.now().isoformat()
    }

    # Hand off to background worker (User gets immediate response)
    background_tasks.add_task(
        run_sentinel_worker, job_id, request.url, request.months
    )

    return {
        "job_id": job_id,
        "status": "pending",
        "submitted_at": JOBS_DB[job_id]["submitted_at"]
    }

@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """
    Poll this endpoint to check if the analysis is done.
    """
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JOBS_DB[job_id]

# --- Root/Health Check ---
@app.get("/")
def health_check():
    return {"status": "Sentinel is online", "version": "0.2-async"}