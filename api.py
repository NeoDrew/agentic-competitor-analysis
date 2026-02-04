# api.py
import uuid
import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
import logging

# Set up logging to see what happens inside the thread
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sentinel_api")

# --- Import from your updated sentinel_probe.py ---
from sentinel_probe import get_current_state, get_historical_state, analyze_diff

app = FastAPI(title="Sentinel API")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_STATE_PATH = os.path.join(BASE_DIR, "prompt_state.yaml")
PROMPT_DIFF_PATH = os.path.join(BASE_DIR, "prompt_diff.yaml")

JOBS_DB = {}

class AnalyzeRequest(BaseModel):
    url: str
    months: int = 6

class JobResponse(BaseModel):
    job_id: str
    status: str
    submitted_at: str

# --- Wrapper to debug the threaded call ---
def _sync_fetch_history(url: str, months: int):
    """
    Wrapper to run get_historical_state safely in a thread.
    """
    try:
        logger.info(f"Thread: Fetching history for {url} ({months} months ago)...")
        # Call the function from sentinel_probe
        old_md, snapshot = get_historical_state(url, months)
        
        if old_md:
            logger.info(f"Thread: Found snapshot! Length: {len(old_md)} chars. URL: {snapshot}")
        else:
            logger.warning(f"Thread: get_historical_state returned None for {url}")
            
        return old_md, snapshot
    except Exception as e:
        logger.error(f"Thread: Critical failure in get_historical_state: {e}")
        return None, None

# --- The Worker Function ---
async def run_sentinel_worker(job_id: str, url: str, months: int):
    print(f"[{job_id}] Starting Sentinel job for {url}")
    JOBS_DB[job_id]["status"] = "scraping_current"

    try:
        # 1. Scrape Current
        current_md = await get_current_state(url)

        # 2. Scrape History
        JOBS_DB[job_id]["status"] = "scraping_history"
        
        # Use the wrapper inside the executor
        loop = asyncio.get_running_loop()
        old_md, snapshot = await loop.run_in_executor(
            None, 
            _sync_fetch_history, # Call the wrapper, not the raw function
            url, 
            months
        )

        if not old_md:
            print(f"[{job_id}] No history found.")
            JOBS_DB[job_id]["status"] = "failed"
            JOBS_DB[job_id]["error"] = "No historical snapshot found for comparison. (Check logs for 'Thread' warnings)"
            return

        # 3. Analyze
        JOBS_DB[job_id]["status"] = "analyzing_intelligence"
        
        analysis_result = await analyze_diff(
            old_md=old_md, 
            new_md=current_md,
            state_prompt_path=PROMPT_STATE_PATH,
            diff_prompt_path=PROMPT_DIFF_PATH
        )

        # 4. Save
        JOBS_DB[job_id]["status"] = "completed"
        JOBS_DB[job_id]["result"] = analysis_result
        JOBS_DB[job_id]["snapshot_url"] = snapshot
        JOBS_DB[job_id]["completed_at"] = datetime.now().isoformat()
        
        print(f"[{job_id}] Job Complete.")

    except Exception as e:
        print(f"[{job_id}] Failed: {e}")
        JOBS_DB[job_id]["status"] = "failed"
        JOBS_DB[job_id]["error"] = str(e)

# --- Endpoints ---
@app.post("/analyze", response_model=JobResponse)
async def start_analysis(request: AnalyzeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    JOBS_DB[job_id] = {
        "id": job_id, "url": request.url, "status": "pending", 
        "submitted_at": datetime.now().isoformat()
    }
    background_tasks.add_task(run_sentinel_worker, job_id, request.url, request.months)
    return {"job_id": job_id, "status": "pending", "submitted_at": JOBS_DB[job_id]["submitted_at"]}

@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    if job_id not in JOBS_DB:
        raise HTTPException(status_code=404, detail="Job not found")
    return JOBS_DB[job_id]