#!/usr/bin/env python3
"""
Sentinel Orchestrator - Full Competitive Intelligence Pipeline

Usage:
    python orchestrator.py "Project management software for engineering teams"
    python orchestrator.py --competitors "Linear,Asana,ClickUp"
"""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime

from google import genai
from google.genai import types

from discovery import suggest_competitors, find_company_links, try_common_ats_urls
from ghost_probe import detect_ats, fetch_jobs, analyze_hiring_trends
from sentinel_probe import get_current_state, get_historical_state, analyze_diff

# Directory for storing job snapshots (for future comparison)
SNAPSHOTS_DIR = "snapshots"
REPORTS_DIR = "reports"


def ensure_dirs():
    """Create necessary directories."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)


def get_snapshot_path(company_name: str) -> str:
    """Get the path for a company's job snapshot file."""
    safe_name = company_name.lower().replace(" ", "_").replace(".", "")
    return os.path.join(SNAPSHOTS_DIR, f"{safe_name}_jobs.json")


def load_previous_snapshot(company_name: str) -> list[dict] | None:
    """Load previous job snapshot if it exists."""
    path = get_snapshot_path(company_name)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                return data.get('jobs', [])
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_snapshot(company_name: str, jobs: list[dict], ats_url: str):
    """Save current jobs as snapshot for future comparison."""
    path = get_snapshot_path(company_name)
    data = {
        'company': company_name,
        'ats_url': ats_url,
        'timestamp': datetime.now().isoformat(),
        'job_count': len(jobs),
        'jobs': jobs
    }
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  📸 Snapshot saved: {path}")


def analyze_jobs_with_ai(jobs: list[dict], company_name: str) -> dict:
    """
    Analyze current job listings to infer strategic direction.
    Returns insights about what the hiring patterns suggest.
    """
    if not jobs:
        return {"summary": "No job data available", "signals": []}

    # Count by department
    dept_counts = {}
    for job in jobs:
        dept = job.get('department', 'General')
        dept_counts[dept] = dept_counts.get(dept, 0) + 1

    # Look for strategic keywords
    keywords = {
        'AI/ML': ['ai', 'machine learning', 'ml', 'llm', 'gpt', 'neural'],
        'Enterprise': ['enterprise', 'b2b', 'sales', 'account executive'],
        'Platform': ['platform', 'infrastructure', 'devops', 'sre'],
        'Security': ['security', 'compliance', 'soc', 'privacy'],
        'Growth': ['growth', 'marketing', 'demand gen', 'content'],
        'International': ['emea', 'apac', 'international', 'remote'],
    }

    signals = []
    for category, terms in keywords.items():
        matches = sum(1 for job in jobs if any(
            term in job.get('title', '').lower() for term in terms
        ))
        if matches > 0:
            signals.append({
                'category': category,
                'count': matches,
                'percent': round(matches / len(jobs) * 100, 1)
            })

    # Sort by count
    signals.sort(key=lambda x: x['count'], reverse=True)

    # Top departments
    top_depts = sorted(dept_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        'total_jobs': len(jobs),
        'top_departments': [{'name': d, 'count': c} for d, c in top_depts],
        'strategic_signals': signals[:5],
        'summary': _generate_hiring_summary(company_name, len(jobs), top_depts, signals)
    }


def _generate_hiring_summary(company: str, total: int, depts: list, signals: list) -> str:
    """Generate a human-readable hiring summary."""
    lines = [f"{company} has {total} open positions."]

    if depts:
        top_dept = depts[0]
        lines.append(f"Heaviest hiring in {top_dept[0]} ({top_dept[1]} roles).")

    if signals:
        top_signal = signals[0]
        lines.append(f"Notable focus: {top_signal['category']} ({top_signal['count']} roles, {top_signal['percent']}%).")

    return " ".join(lines)


async def generate_executive_summary(result: dict, max_retries: int = 5) -> str:
    """
    Evaluator Agent: Generates a detailed 150-250 word executive summary
    synthesizing all competitive intelligence for a competitor.

    Args:
        result: Dict containing pricing_analysis, hiring_analysis, hiring_trends
        max_retries: Maximum retry attempts for API errors

    Returns:
        Detailed executive summary string
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Unable to generate executive summary: API key not configured."

    client = genai.Client(api_key=api_key)
    model_id = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    name = result.get('name', 'Unknown')
    pricing = result.get('pricing_analysis', {})
    hiring = result.get('hiring_analysis', {})
    trends = result.get('hiring_trends', {})

    # Build context for the evaluator
    context_parts = [f"Company: {name}"]

    # Pricing context
    if pricing and isinstance(pricing, dict):
        old_state = pricing.get('old_state', {})
        new_state = pricing.get('new_state', {})
        analysis = pricing.get('analysis', {})

        if old_state or new_state:
            context_parts.append("\n=== PRICING DATA ===")

            # Old pricing
            if old_state:
                old_plans = old_state.get('pricing_plans', [])
                if old_plans:
                    plans_str = ", ".join([f"{p.get('name', 'N/A')}: {p.get('price', 'N/A')}" for p in old_plans[:5]])
                    context_parts.append(f"6 months ago: {plans_str}")
                old_tagline = old_state.get('tagline', '')
                if old_tagline:
                    context_parts.append(f"Previous positioning: {old_tagline}")

            # New pricing
            if new_state:
                new_plans = new_state.get('pricing_plans', [])
                if new_plans:
                    plans_str = ", ".join([f"{p.get('name', 'N/A')}: {p.get('price', 'N/A')}" for p in new_plans[:5]])
                    context_parts.append(f"Current: {plans_str}")
                new_tagline = new_state.get('tagline', '')
                if new_tagline:
                    context_parts.append(f"Current positioning: {new_tagline}")

            # Analysis insights
            if analysis:
                change_detected = analysis.get('change_detected', False)
                context_parts.append(f"Pricing changed: {'Yes' if change_detected else 'No'}")

                evidence = analysis.get('evidence', {})
                if evidence:
                    for key, val in evidence.items():
                        if val and val != 'N/A':
                            context_parts.append(f"  {key}: {val}")

    # Hiring context
    if hiring and isinstance(hiring, dict):
        context_parts.append("\n=== HIRING DATA ===")
        total_jobs = hiring.get('total_jobs', 0)
        context_parts.append(f"Total open positions: {total_jobs}")

        top_depts = hiring.get('top_departments', [])
        if top_depts:
            depts_str = ", ".join([f"{d['name']} ({d['count']})" for d in top_depts[:5]])
            context_parts.append(f"Top departments: {depts_str}")

        signals = hiring.get('strategic_signals', [])
        if signals:
            signals_str = ", ".join([f"{s['category']} ({s['count']} roles, {s['percent']}%)" for s in signals[:4]])
            context_parts.append(f"Strategic signals: {signals_str}")

    # Trends context
    if trends and isinstance(trends, dict):
        context_parts.append("\n=== HIRING TRENDS ===")
        velocity = trends.get('velocity_change_percent', 0)
        old_count = trends.get('old_count', 0)
        new_count = trends.get('new_count', 0)
        context_parts.append(f"Hiring velocity change: {velocity:+.0f}% ({old_count} → {new_count} roles)")

        new_roles = trends.get('new_roles', [])
        if new_roles:
            roles_str = ", ".join([r.get('title', '')[:50] for r in new_roles[:5]])
            context_parts.append(f"New roles added: {roles_str}")

    context = "\n".join(context_parts)

    # Evaluator prompt
    system_instruction = """You are a senior competitive intelligence analyst writing executive briefings for C-level executives.

Your task is to synthesize all available data into a compelling, insight-rich executive summary.

Guidelines:
- Write 150-250 words (this is critical - not too short, not too long)
- Lead with the most important strategic insight
- Be specific with data points (numbers, percentages, changes)
- Identify strategic implications and potential threats/opportunities
- Use confident, direct language appropriate for executive audiences
- Do NOT use bullet points - write in flowing paragraphs
- Do NOT include headers or sections - one cohesive summary
- Avoid vague statements - be specific and actionable
- If data is limited, acknowledge it but still provide value from what's available"""

    user_prompt = f"""Write an executive summary for the following competitive intelligence on {name}:

{context}

Remember: 150-250 words, flowing paragraphs, lead with the key insight, be specific with data."""

    config = types.GenerateContentConfig(
        system_instruction=system_instruction
    )

    retryable_errors = ['429', '503', 'RESOURCE_EXHAUSTED', 'UNAVAILABLE', 'overloaded']

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=user_prompt,
                config=config
            )
            summary = response.text.strip()

            # Basic validation
            word_count = len(summary.split())
            if word_count < 50:
                return f"Analysis shows limited strategic changes for {name}. Insufficient data available for detailed assessment."

            return summary

        except Exception as e:
            error_str = str(e)
            is_retryable = any(code in error_str for code in retryable_errors)

            if is_retryable and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2
                print(f"  ⚠️  Evaluator API overloaded (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                print(f"  ✗ Evaluator failed: {error_str}")
                return f"Unable to generate executive summary due to API error."

    return "Executive summary generation failed after maximum retries."


async def analyze_competitor(competitor: dict, months_ago: int = 6) -> dict:
    """
    Run full analysis on a single competitor.
    Returns combined pricing + hiring intelligence.
    """
    name = competitor['name']
    pricing_url = competitor.get('pricing_url')
    ats_url = competitor.get('ats_url')
    ats_type = competitor.get('ats_type')

    print(f"\n{'='*60}")
    print(f"  ANALYZING: {name}")
    print(f"{'='*60}")

    result = {
        'name': name,
        'domain': competitor.get('domain'),
        'pricing_url': pricing_url,
        'ats_url': ats_url,
        'pricing_analysis': None,
        'hiring_analysis': None,
        'hiring_trends': None,
        'timestamp': datetime.now().isoformat()
    }

    # --- 1. Pricing/Positioning Analysis (Sentinel Probe) ---
    if pricing_url and competitor.get('pricing_verified', False):
        print(f"\n📊 Running Sentinel Probe on {pricing_url}...")
        try:
            # Get current state
            current_md = await get_current_state(pricing_url)

            # Get historical state
            old_md, snapshot_url = get_historical_state(pricing_url, months_ago)

            if old_md and current_md:
                print(f"  Found historical snapshot from ~{months_ago} months ago")
                # Run AI analysis
                analysis = await analyze_diff(
                    old_md=old_md,
                    new_md=current_md,
                    target_url=pricing_url
                )
                result['pricing_analysis'] = analysis
                result['historical_snapshot'] = snapshot_url
                print(f"  ✓ Pricing analysis complete")
            else:
                print(f"  ⚠ No historical data found for pricing comparison")
        except Exception as e:
            print(f"  ✗ Pricing analysis failed: {e}")
    else:
        print(f"\n📊 Skipping pricing analysis (URL not verified)")

    # --- 2. Job Listings Analysis (Ghost Probe) ---
    if ats_url and ats_type:
        print(f"\n👻 Running Ghost Probe on {ats_url}...")
        try:
            jobs = fetch_jobs(ats_url, ats_type)

            if not jobs:
                print(f"  ⚠ No jobs found (ATS may be invalid or empty)")
            else:
                print(f"  Found {len(jobs)} open positions")

                # Analyze current jobs
                result['hiring_analysis'] = analyze_jobs_with_ai(jobs, name)

                # Load previous snapshot for trend comparison
                previous_jobs = load_previous_snapshot(name)
                if previous_jobs:
                    print(f"  Comparing with previous snapshot ({len(previous_jobs)} jobs)")
                    result['hiring_trends'] = analyze_hiring_trends(previous_jobs, jobs)
                else:
                    print(f"  No previous snapshot (first run)")

                # Save current as new snapshot
                save_snapshot(name, jobs, ats_url)

        except Exception as e:
            print(f"  ✗ Job analysis failed: {e}")
    else:
        print(f"\n👻 Skipping job analysis (no ATS detected)")

    # --- 3. Executive Summary (Evaluator Agent) ---
    print(f"\n🎯 Running Evaluator Agent...")
    try:
        executive_summary = await generate_executive_summary(result)
        result['executive_summary'] = executive_summary
        print(f"  ✓ Executive summary generated ({len(executive_summary.split())} words)")
    except Exception as e:
        print(f"  ✗ Evaluator failed: {e}")
        result['executive_summary'] = "Executive summary unavailable."

    return result


async def run_pipeline(description: str = None, competitor_names: list[str] = None, months: int = 6) -> list[dict]:
    """
    Main orchestration pipeline.

    Args:
        description: Product description to find competitors for
        competitor_names: Or, provide explicit competitor names
        months: How far back to look for historical pricing data

    Returns:
        List of analysis results for each competitor
    """
    ensure_dirs()

    print("\n" + "="*60)
    print("  SENTINEL COMPETITIVE INTELLIGENCE PIPELINE")
    print("="*60)

    # --- Step 1: Discovery ---
    if competitor_names:
        # Manual competitor list provided - need to look up domains
        print(f"\n🎯 Using provided competitors: {competitor_names}")
        print("🧠 Looking up domains...")

        # Use Gemini to get domains for the provided names

        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            client = genai.Client(api_key=api_key)
            model_id = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

            prompt = f"""For each company name, provide their main website domain.
Return a JSON array of objects with "name" and "domain" fields.
Companies: {', '.join(competitor_names)}
Example: [{{"name": "Asana", "domain": "asana.com"}}]"""

            config = types.GenerateContentConfig(
                response_mime_type="application/json"
            )

            # Retry with exponential backoff
            retryable_errors = ['429', '503', 'RESOURCE_EXHAUSTED', 'UNAVAILABLE', 'overloaded']
            max_retries = 5
            comp_data = None

            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model=model_id, contents=prompt, config=config
                    )
                    comp_data = json.loads(response.text.strip())
                    break
                except Exception as e:
                    error_str = str(e)
                    is_retryable = any(code in error_str for code in retryable_errors)

                    if is_retryable and attempt < max_retries - 1:
                        wait_time = (2 ** attempt) * 2
                        print(f"  ⚠️  API overloaded (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        print(f"Failed to look up domains: {e}")
                        comp_data = [{'name': n, 'domain': None} for n in competitor_names]
                        break

            if comp_data is None:
                comp_data = [{'name': n, 'domain': None} for n in competitor_names]
        else:
            comp_data = [{'name': n, 'domain': None} for n in competitor_names]

        # Now run discovery for each
        competitors = []
        for comp in comp_data:
            if not comp.get('domain'):
                print(f"  ⚠ No domain for {comp.get('name')}, skipping")
                continue
            links = find_company_links(comp)
            if links:
                # Try to find ATS
                if links.get('careers_url'):
                    ats = detect_ats(links['careers_url'])
                    if not ats:
                        ats = try_common_ats_urls(links['name'])
                    if ats:
                        links['ats_url'] = ats['url']
                        links['ats_type'] = ats['type']
                competitors.append(links)
    else:
        # Auto-discover competitors
        print(f"\n🧠 Discovering competitors for: {description[:50]}...")
        from discovery import run_discovery
        competitors = run_discovery(description)

    if not competitors:
        print("❌ No competitors found. Exiting.")
        return []

    print(f"\n📋 Analyzing {len(competitors)} competitors...")

    # --- Step 2: Analyze Each Competitor ---
    results = []
    for comp in competitors:
        try:
            analysis = await analyze_competitor(comp, months)
            results.append(analysis)
        except Exception as e:
            print(f"❌ Failed to analyze {comp.get('name')}: {e}")
            results.append({
                'name': comp.get('name'),
                'error': str(e)
            })

    # --- Step 3: Save Combined Results ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(REPORTS_DIR, f"intelligence_{timestamp}.json")
    with open(output_file, 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'description': description,
            'competitor_count': len(results),
            'results': results
        }, f, indent=2)

    print(f"\n{'='*60}")
    print(f"  PIPELINE COMPLETE")
    print(f"  Results saved to: {output_file}")
    print(f"{'='*60}")

    return results


def print_summary(results: list[dict]):
    """Print a summary of the analysis results."""
    print("\n" + "="*60)
    print("  EXECUTIVE SUMMARY")
    print("="*60)

    for r in results:
        name = r.get('name', 'Unknown')
        print(f"\n[{name}]")

        # Executive summary from evaluator
        exec_summary = r.get('executive_summary', '')
        if exec_summary and exec_summary != "Executive summary unavailable.":
            # Show first 300 chars of executive summary
            if len(exec_summary) > 300:
                exec_summary = exec_summary[:300] + "..."
            print(f"  📋 {exec_summary}")
        else:
            # Fallback to individual summaries
            # Pricing summary
            pricing = r.get('pricing_analysis', {})
            if pricing and isinstance(pricing, dict):
                analysis = pricing.get('analysis', {})
                if isinstance(analysis, dict):
                    strategic = (
                        analysis.get('strategic_shift') or
                        analysis.get('strategic_analysis') or
                        analysis.get('summary')
                    )
                    if strategic and strategic != 'N/A':
                        if len(strategic) > 150:
                            strategic = strategic[:150] + "..."
                        print(f"  💰 Pricing: {strategic}")

            # Hiring summary
            hiring = r.get('hiring_analysis', {})
            if hiring and isinstance(hiring, dict):
                summary = hiring.get('summary')
                if summary:
                    print(f"  👥 Hiring: {summary}")

            # Trends
            trends = r.get('hiring_trends', {})
            if trends and isinstance(trends, dict):
                trend_summary = trends.get('summary')
                if trend_summary:
                    print(f"  📈 Trend: {trend_summary}")


def main():
    parser = argparse.ArgumentParser(
        description="Sentinel Competitive Intelligence Pipeline"
    )
    parser.add_argument(
        "description",
        nargs="?",
        help="Description of your product/company to find competitors"
    )
    parser.add_argument(
        "--competitors", "-c",
        help="Comma-separated list of competitor names (skip auto-discovery)"
    )
    parser.add_argument(
        "--months", "-m",
        type=int,
        default=6,
        help="Months of historical data to analyze (default: 6)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON file path"
    )

    args = parser.parse_args()

    if not args.description and not args.competitors:
        parser.error("Either provide a description or --competitors list")

    competitor_names = None
    if args.competitors:
        competitor_names = [c.strip() for c in args.competitors.split(",")]

    # Run the pipeline
    results = asyncio.run(run_pipeline(
        description=args.description,
        competitor_names=competitor_names,
        months=args.months
    ))

    # Print summary
    print_summary(results)


if __name__ == "__main__":
    main()
