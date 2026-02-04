import argparse
import asyncio
import json
import sys
import yaml # Requires PyYAML: pip install pyyaml

# Import core logic from your library file
from sentinel_probe import (
    get_current_state, 
    get_historical_state,
    analyze_diff
)

async def main() -> int:
    # parser = argparse.ArgumentParser(
    #     description="Sentinel Probe: The Agentic Strategic Watchdog"
    # )
    
    # # Argument: The target website
    # parser.add_argument(
    #     "url", 
    #     help="Target URL to probe (e.g., https://linear.app/pricing)"
    # )
    
    # # Argument: Time travel depth
    # parser.add_argument(
    #     "--months", 
    #     type=int, 
    #     default=6,
    #     help="Months ago for historical snapshot (default: 6)"
    # )

    # args = parser.parse_args()
    
    url = "https://vercel.com/pricing"
    months = 6

    print(f"Sentinel is locking onto: {url}")
    print(f"Searching archives for data from {months} months ago...\n")

    # 1. Fetch Current State (Async)
    try:
        new_md = await get_current_state(url)
    except Exception as e:
        print(f"Error fetching current state: {e}")
        return 2

    # 2. Fetch Historical State (Sync/API wrapper)
    # Returns tuple: (markdown_text, snapshot_url)
    old_md, snap = get_historical_state(url, months)

    # save old and new to files
    with open("old_state.md", "w", encoding="utf-8") as f:
        f.write(old_md or "")
    with open("new_state.md", "w", encoding="utf-8") as f:
        f.write(new_md or "")

    if old_md is None:
        print(f"No Wayback snapshot found for ~{months} months ago.")
        # Optional: Decide if you want to abort here or compare against an empty string
        # For now, we continue to see if we can just analyze current state context
        # but typically comparison requires two states.
    
    if snap:
        print(f"Wayback snapshot acquired: {snap}")

    # 3. Basic Diff Check (Logging)
    await analyze_diff(old_md, new_md)

    # 4. The Analyst (Gemini + Prompt)
    print("\nSending data to Analyst (Gemini)...")
    
    try:
        # Note: analyze_diff should handle reading 'analysisPrompt.yaml' internally
        # or be passed the prompt text. Assuming internal handling for now.
        gemini_resp = await analyze_diff(old_md, new_md)
        
        print("\n--- GEMINI RESPONSE ---\n")
        
        if isinstance(gemini_resp, dict):
            # Check for standard text keys or just dump the whole JSON
            text = gemini_resp.get("text") or gemini_resp.get("result")
            if text:
                print(text)
            else:
                # Nice JSON formatting for the strategic output
                print(json.dumps(gemini_resp, indent=2, ensure_ascii=False))
        else:
            # Fallback if response is raw string
            print(gemini_resp)
            
    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return 1

    return 0

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nSentinel probe aborted by user.")
        sys.exit(0)