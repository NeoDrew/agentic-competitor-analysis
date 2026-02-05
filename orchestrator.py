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
            print(f"  Found {len(jobs)} open positions")

            if jobs:
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
        # Manual competitor list provided
        print(f"\n🎯 Using provided competitors: {competitor_names}")
        competitors = []
        for name in competitor_names:
            # Minimal discovery for each
            comp = {'name': name, 'domain': None}
            links = find_company_links(comp)
            if links:
                # Try to find ATS
                if links.get('careers_url'):
                    ats = detect_ats(links['careers_url'])
                    if not ats:
                        ats = try_common_ats_urls(name)
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

        # Pricing summary
        pricing = r.get('pricing_analysis', {})
        if pricing and 'analysis' in pricing:
            analysis = pricing.get('analysis', {})
            strategic = analysis.get('strategic_analysis') or analysis.get('summary_of_changes', 'N/A')
            print(f"  💰 Pricing: {strategic[:100]}...")

        # Hiring summary
        hiring = r.get('hiring_analysis', {})
        if hiring:
            print(f"  👥 Hiring: {hiring.get('summary', 'N/A')}")

        # Trends
        trends = r.get('hiring_trends', {})
        if trends:
            print(f"  📈 Trend: {trends.get('summary', 'N/A')}")


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
