#!/usr/bin/env python3
"""
Generate Professional Sentinel Reports using LaTeX.
Supports both single-competitor and multi-competitor intelligence reports.

Usage:
    python generate_report.py --input reports/intelligence_20260205_015007.json
    python generate_report.py --input reports/intelligence_20260205_015007.json --competitor ClickUp
"""
import argparse
import os
import subprocess
import shutil
import json
from datetime import datetime

# --- LaTeX Template ---
LATEX_TEMPLATE = r"""
\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage[margin=0.85in]{geometry}
\usepackage{xcolor}
\usepackage{longtable}
\usepackage{booktabs}
\usepackage{array}
\usepackage{textcomp}
\usepackage{enumitem}
\usepackage{fancyhdr}
\usepackage{hyperref}

% Hyperlink setup
\hypersetup{
    colorlinks=true,
    linkcolor=accent,
    urlcolor=accent,
    breaklinks=true
}

% Define Colors
\definecolor{navy}{RGB}{10, 25, 60}
\definecolor{accent}{RGB}{0, 102, 204}
\definecolor{lightgrey}{RGB}{240, 240, 240}
\definecolor{darkgrey}{RGB}{80, 80, 80}
\definecolor{signalgreen}{RGB}{34, 139, 34}
\definecolor{signalred}{RGB}{180, 30, 30}

% Header/Footer
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\fancyfoot[C]{\footnotesize \textcolor{darkgrey}{Sentinel Competitive Intelligence | Confidential}}
\fancyfoot[R]{\footnotesize \textcolor{darkgrey}{\thepage}}

\setlist[itemize]{noitemsep, topsep=0pt, leftmargin=1.5em}

\begin{document}

% === HEADER ===
\noindent
\begin{minipage}[t]{0.7\textwidth}
{\Huge\textbf{\textcolor{navy}{<<COMPETITOR>>}}}\\[0.3em]
{\large\textcolor{darkgrey}{Competitive Intelligence Report}}
\end{minipage}
\hfill
\begin{minipage}[t]{0.28\textwidth}
\raggedleft
{\small\textcolor{darkgrey}{Generated: <<TIMESTAMP>>}}\\
{\small\textcolor{darkgrey}{<<DOMAIN>>}}
\end{minipage}

\vspace{0.5cm}
\noindent\rule{\textwidth}{1pt}

% === STRATEGIC VERDICT ===
\vspace{0.6cm}
\noindent{\Large\textbf{\textcolor{navy}{Strategic Verdict}}}
\vspace{0.4cm}

\noindent\colorbox{navy!8}{\parbox{\dimexpr\textwidth-2\fboxsep}{
\vspace{4mm}
<<VERDICT>>
\vspace{4mm}
}}

% === KEY METRICS ===
<<KEY_METRICS>>

% === COMPANY BACKGROUND ===
<<BACKGROUND_SECTION>>

% === PRICING ANALYSIS ===
\vspace{0.8cm}
\noindent{\Large\textbf{\textcolor{navy}{Pricing Analysis}}}
\vspace{0.4cm}

<<PRICING_SECTION>>

% === HOMEPAGE INTELLIGENCE ===
<<HOMEPAGE_SECTION>>

% === HIRING INTELLIGENCE ===
<<HIRING_SECTION>>

% === SOURCES ===
\vspace{0.8cm}
\noindent\rule{\textwidth}{0.5pt}
\vspace{0.2cm}
{\footnotesize\textcolor{darkgrey}{\textbf{Sources:} <<SOURCES>>}}

\end{document}
"""


def escape_latex(text: str) -> str:
    """Escape special LaTeX characters."""
    if not text:
        return ""

    text = str(text)
    text = text.replace('\\', r'\textbackslash{}')

    replacements = {
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
        '<': r'\textless{}',
        '>': r'\textgreater{}',
    }

    for key, val in replacements.items():
        text = text.replace(key, val)

    text = text.replace('\n\n', r' \par ')
    text = text.replace('\n', r' \newline ')

    return text


def normalize_plan_name(name: str) -> str:
    """Normalize plan name to Title Case."""
    if not name:
        return "N/A"
    # Handle all caps or all lower
    return name.strip().title()


def format_pricing_table(old_plans, new_plans) -> str:
    """Format pricing comparison as a LaTeX table."""
    if not old_plans and not new_plans:
        return r"\textit{No pricing data available.}"

    lines = []
    lines.append(r"\renewcommand{\arraystretch}{1.5}")
    lines.append(r"\begin{longtable}{@{}p{0.46\textwidth}|p{0.46\textwidth}@{}}")
    lines.append(r"\textbf{6 Months Ago} & \textbf{Current} \\")
    lines.append(r"\hline")
    lines.append(r"& \\[-0.6em]")

    # Get all plan names - normalize for matching
    old_dict = {p.get('name', '').lower().strip(): p for p in (old_plans or [])} if old_plans else {}
    new_dict = {p.get('name', '').lower().strip(): p for p in (new_plans or [])} if new_plans else {}
    all_names = list(dict.fromkeys(list(old_dict.keys()) + list(new_dict.keys())))

    for name_key in all_names[:6]:  # Limit to 6 plans
        if not name_key:
            continue

        old_p = old_dict.get(name_key, {})
        new_p = new_dict.get(name_key, {})

        old_text = ""
        new_text = ""

        if old_p:
            display_name = normalize_plan_name(old_p.get('name', 'N/A'))
            old_price = escape_latex(old_p.get('price', 'N/A'))
            old_text = f"\\textbf{{{escape_latex(display_name)}}}: {old_price}"

        if new_p:
            display_name = normalize_plan_name(new_p.get('name', 'N/A'))
            new_price = escape_latex(new_p.get('price', 'N/A'))
            new_text = f"\\textbf{{{escape_latex(display_name)}}}: {new_price}"

            # Highlight price changes
            if old_p and old_p.get('price') != new_p.get('price'):
                new_text = f"\\textcolor{{accent}}{{{new_text}}}"

        lines.append(f"{old_text} & {new_text} \\\\[0.3em]")

    lines.append(r"\end{longtable}")
    return "\n".join(lines)


def format_hiring_section(hiring_analysis, hiring_trends, result: dict = None) -> str:
    """Format hiring analysis as LaTeX with source links."""
    if not hiring_analysis:
        return r"\vspace{0.8cm}" + "\n" + r"\noindent{\Large\textbf{\textcolor{navy}{Hiring Intelligence}}}" + "\n" + r"\vspace{0.4cm}" + "\n\n" + r"\textit{No hiring data available (ATS not detected or unsupported).}"

    lines = []
    lines.append(r"\vspace{0.8cm}")
    lines.append(r"\noindent{\Large\textbf{\textcolor{navy}{Hiring Intelligence}}}")
    lines.append(r"\vspace{0.4cm}")

    total_jobs = hiring_analysis.get('total_jobs', 0)
    top_depts = hiring_analysis.get('top_departments', [])
    signals = hiring_analysis.get('strategic_signals', [])

    # Summary stats - centered
    lines.append(r"\begin{center}")

    lines.append(r"\colorbox{lightgrey}{\parbox{0.28\textwidth}{\centering\vspace{3mm}{\large\textbf{" + str(total_jobs) + r"}}\\\vspace{1mm}{\small Open Positions}\vspace{3mm}}}")
    lines.append(r"\hspace{0.03\textwidth}")

    if top_depts:
        top_dept = top_depts[0]
        dept_name = escape_latex(top_dept['name'])
        # Truncate long department names
        if len(dept_name) > 15:
            dept_name = dept_name[:14] + "..."
        lines.append(r"\colorbox{lightgrey}{\parbox{0.28\textwidth}{\centering\vspace{3mm}{\large\textbf{" + dept_name + r"}}\\\vspace{1mm}{\small Top Department}\vspace{3mm}}}")
        lines.append(r"\hspace{0.03\textwidth}")

    if signals:
        top_signal = signals[0]
        lines.append(r"\colorbox{lightgrey}{\parbox{0.28\textwidth}{\centering\vspace{3mm}{\large\textbf{" + escape_latex(top_signal['category']) + r"}}\\\vspace{1mm}{\small Strategic Focus (" + str(top_signal['percent']) + r"\%)}\vspace{3mm}}}")

    lines.append(r"\end{center}")
    lines.append(r"\vspace{0.5cm}")

    # Two-column layout for departments and signals
    lines.append(r"\noindent")
    lines.append(r"\begin{minipage}[t]{0.48\textwidth}")

    # Department breakdown
    if top_depts:
        lines.append(r"\textbf{\textcolor{accent}{Department Breakdown}}")
        lines.append(r"\vspace{0.2cm}")
        lines.append(r"\begin{itemize}")
        for dept in top_depts[:5]:
            lines.append(f"\\item {escape_latex(dept['name'])}: {dept['count']} roles")
        lines.append(r"\end{itemize}")

    lines.append(r"\end{minipage}")
    lines.append(r"\hfill")
    lines.append(r"\begin{minipage}[t]{0.48\textwidth}")

    # Strategic signals
    if signals:
        lines.append(r"\textbf{\textcolor{accent}{Strategic Signals}}")
        lines.append(r"\vspace{0.2cm}")
        lines.append(r"\begin{itemize}")
        for sig in signals[:4]:
            category = escape_latex(sig['category'])
            count = sig['count']
            pct = sig['percent']
            lines.append(f"\\item {category}: {count} roles ({pct}\\%)")
        lines.append(r"\end{itemize}")

    lines.append(r"\end{minipage}")

    # Trends
    if hiring_trends:
        velocity = hiring_trends.get('velocity_change_percent', 0)
        old_count = hiring_trends.get('old_count', 0)
        new_count = hiring_trends.get('new_count', 0)

        lines.append(r"\vspace{0.5cm}")
        lines.append(r"\noindent\textbf{\textcolor{accent}{Hiring Trends:}} ")
        if velocity > 10:
            lines.append(f"\\textcolor{{signalgreen}}{{Velocity increased {velocity:.0f}\\% ({old_count} to {new_count} roles)}}")
        elif velocity < -10:
            lines.append(f"\\textcolor{{signalred}}{{Velocity decreased {abs(velocity):.0f}\\% ({old_count} to {new_count} roles)}}")
        else:
            lines.append(f"Velocity stable ({new_count} roles)")

        # New roles
        new_roles = hiring_trends.get('new_roles', [])
        if new_roles:
            lines.append(r"\newline\textbf{New Roles:} " + ", ".join([escape_latex(r.get('title', ''))[:40] for r in new_roles[:3]]))

    # Add job source link with proper spacing
    if result:
        job_source_url = result.get('ats_url') or result.get('levelsfyi_url')
        if not job_source_url and 'linkedin' in result.get('job_source', '').lower():
            name = result.get('name', '')
            job_source_url = f"https://www.linkedin.com/company/{name.lower().replace(' ', '-')}/jobs/"
        if job_source_url:
            lines.append(r"\vspace{0.8cm}")  # More space before source
            lines.append(f"\\noindent\\textit{{\\small Source: \\url{{{escape_latex(job_source_url)}}}}}")

    return "\n".join(lines)


def is_valid_description(desc: str) -> bool:
    """Check if description is valid (not a login page or error)."""
    if not desc:
        return False
    invalid_patterns = [
        'login to linkedin',
        'sign in',
        'log in to',
        'create an account',
        'keep in touch with people you know',
    ]
    desc_lower = desc.lower()
    return not any(pattern in desc_lower for pattern in invalid_patterns)


def format_background_section(background: dict, result: dict = None) -> str:
    """Format company background as LaTeX with proper links."""
    if not background:
        return ""

    summary = background.get('summary', {})
    if not summary or len(summary) <= 2:  # Only has 'name' and maybe 'website'
        return ""

    lines = []
    lines.append(r"\vspace{0.8cm}")
    lines.append(r"\noindent{\Large\textbf{\textcolor{navy}{Company Background}}}")
    lines.append(r"\par\vspace{0.5cm}")  # Force new line after header

    # Helper to check if value is valid (not Wikipedia parsing junk)
    def is_valid_field(val):
        if not val:
            return False
        val_str = str(val).strip()
        # Filter out Wikipedia infobox parsing artifacts
        if '|' in val_str or '=' in val_str or val_str.startswith('{') or val_str.startswith('['):
            return False
        return len(val_str) > 0

    # Key facts - each on its own line for clarity
    facts_lines = []
    if is_valid_field(summary.get('founded')):
        facts_lines.append(f"\\textbf{{Founded:}} {escape_latex(str(summary['founded']))}")
    if is_valid_field(summary.get('headquarters')):
        facts_lines.append(f"\\textbf{{Headquarters:}} {escape_latex(str(summary['headquarters']))}")
    if is_valid_field(summary.get('employees')):
        facts_lines.append(f"\\textbf{{Employees:}} {escape_latex(str(summary['employees']))}")
    if is_valid_field(summary.get('funding')):
        facts_lines.append(f"\\textbf{{Funding:}} \\${escape_latex(str(summary['funding']))}")
    if is_valid_field(summary.get('industry')):
        facts_lines.append(f"\\textbf{{Industry:}} {escape_latex(str(summary['industry']))}")

    if facts_lines:
        lines.append(r"\noindent")
        lines.append(" \\quad | \\quad ".join(facts_lines))
        lines.append(r"\vspace{0.4cm}")  # Space after facts

    # Founders
    if summary.get('founders'):
        lines.append(r"")
        lines.append(r"\noindent\textbf{\textcolor{accent}{Founders:}}")
        lines.append(r"\par\noindent")  # Force paragraph break
        lines.append(escape_latex(str(summary['founders'])))
        lines.append(r"")
        lines.append(r"\vspace{0.4cm}")  # Space after founders

    # Description - filter out LinkedIn login page errors
    desc = summary.get('description', '')
    if is_valid_description(desc):
        # Don't truncate - show full description
        desc_text = str(desc)
        lines.append(r"")
        lines.append(r"\noindent\textbf{\textcolor{accent}{Overview:}}")
        lines.append(r"\par\noindent")  # Force paragraph break
        lines.append(f"{escape_latex(desc_text)}")
        lines.append(r"")
        lines.append(r"\vspace{0.4cm}")  # Space after overview

    # Mission statement (if different from description)
    mission = summary.get('mission', '')
    if mission and mission != desc and is_valid_description(mission):
        mission_text = str(mission)
        lines.append(r"")
        lines.append(r"\noindent\textbf{\textcolor{accent}{Mission:}}")
        lines.append(r"\par\noindent")  # Force paragraph break
        lines.append(f"{escape_latex(mission_text)}")
        lines.append(r"")
        lines.append(r"\vspace{0.4cm}")  # Space after mission

    # Recent news - show full titles with links
    news = background.get('recent_news', [])
    if news:
        lines.append(r"")
        lines.append(r"\noindent\textbf{\textcolor{accent}{Recent News:}}")
        lines.append(r"\begin{itemize}")
        for item in news[:3]:
            title = str(item.get('title', ''))
            url = item.get('url', '')
            if title:
                if url:
                    # Make title a clickable link - URL needs minimal escaping, only % and #
                    safe_url = url.replace('%', r'\%').replace('#', r'\#')
                    lines.append(f"\\item \\href{{{safe_url}}}{{{escape_latex(title)}}}")
                else:
                    lines.append(f"\\item {escape_latex(title)}")
        lines.append(r"\end{itemize}")
        lines.append(r"\vspace{0.3cm}")  # Space after news

    # GitHub stats
    github = background.get('github', {})
    if github and (github.get('public_repos') or github.get('total_stars')):
        repos = github.get('public_repos', 0)
        stars = github.get('total_stars', 0)
        org_url = github.get('url', '')
        if org_url:
            lines.append(f"\\noindent\\textbf{{\\textcolor{{accent}}{{Open Source:}}}} {repos} public repos, {stars:,} total stars (\\url{{{escape_latex(org_url)}}})")
        else:
            lines.append(f"\\noindent\\textbf{{\\textcolor{{accent}}{{Open Source:}}}} {repos} public repos, {stars:,} total stars")
        lines.append(r"\vspace{0.3cm}")

    # Wikipedia source link
    wiki = background.get('wikipedia', {})
    if wiki and wiki.get('url'):
        lines.append(r"\vspace{0.2cm}")
        lines.append(f"\\noindent\\textit{{\\small Source: \\url{{{escape_latex(wiki.get('url'))}}}}}")

    if len(lines) <= 3:  # Only header, no content
        return ""

    return "\n".join(lines)


def format_homepage_section(homepage_analysis: dict, result: dict = None) -> str:
    """Format homepage intelligence as LaTeX."""
    if not homepage_analysis:
        return ""

    # Check for error
    if 'error' in homepage_analysis:
        return ""

    new_state = homepage_analysis.get('new_state') or {}
    old_state = homepage_analysis.get('old_state') or {}
    analysis = homepage_analysis.get('analysis') or {}

    # Skip if no meaningful data
    if not new_state or 'error' in new_state:
        return ""

    lines = []
    lines.append(r"\vspace{0.8cm}")
    lines.append(r"\noindent{\Large\textbf{\textcolor{navy}{Homepage Intelligence}}}")
    lines.append(r"\par\vspace{0.5cm}")  # Force new line after header

    # Strategic shift summary (if changes detected)
    change_detected = analysis.get('change_detected', False)
    if change_detected:
        shift = analysis.get('strategic_shift', '')
        magnitude = analysis.get('change_magnitude', 'moderate')
        if shift:
            if magnitude == 'major':
                color = 'signalred'
            elif magnitude == 'minor':
                color = 'darkgrey'
            else:
                color = 'accent'
            lines.append(r"\noindent\colorbox{navy!8}{\parbox{\dimexpr\textwidth-2\fboxsep}{")
            lines.append(r"\vspace{2mm}")
            lines.append(f"\\textbf{{\\textcolor{{{color}}}{{Strategic Shift:}}}} {escape_latex(shift)}")
            lines.append(r"\vspace{2mm}")
            lines.append(r"}}")
            lines.append(r"\vspace{0.4cm}")

    # Current positioning
    hero = new_state.get('hero_headline', '')
    sub_hero = new_state.get('hero_subheadline', '')
    if hero:
        lines.append(r"\noindent\textbf{\textcolor{accent}{Current Positioning:}}")
        lines.append(r"\par\noindent")
        lines.append(f"``{escape_latex(hero)}''")
        if sub_hero:
            lines.append(f" --- {escape_latex(sub_hero)}")
        lines.append(r"\vspace{0.4cm}")

    # Target audience
    audience = new_state.get('target_audience', '')
    if audience:
        lines.append(r"")
        lines.append(f"\\noindent\\textbf{{\\textcolor{{accent}}{{Target Audience:}}}} {escape_latex(audience)}")
        lines.append(r"\vspace{0.3cm}")

    # Key value propositions
    value_props = new_state.get('value_propositions', [])
    if value_props:
        lines.append(r"")
        lines.append(r"\noindent\textbf{\textcolor{accent}{Value Propositions:}}")
        lines.append(r"\begin{itemize}")
        for prop in value_props[:4]:  # Limit to 4
            lines.append(f"\\item {escape_latex(str(prop))}")
        lines.append(r"\end{itemize}")
        lines.append(r"\vspace{0.3cm}")

    # Key features highlighted
    features = new_state.get('key_features', [])
    if features:
        lines.append(r"")
        lines.append(f"\\noindent\\textbf{{\\textcolor{{accent}}{{Key Features:}}}} {escape_latex(', '.join(features[:5]))}")
        lines.append(r"\vspace{0.3cm}")

    # Social proof
    social = new_state.get('social_proof', {})
    if social:
        logos = social.get('customer_logos', [])
        metrics = social.get('metrics', '')
        if logos:
            lines.append(r"")
            lines.append(f"\\noindent\\textbf{{\\textcolor{{accent}}{{Notable Customers:}}}} {escape_latex(', '.join(logos[:5]))}")
        if metrics:
            lines.append(r"")
            lines.append(f"\\noindent\\textbf{{\\textcolor{{accent}}{{Metrics:}}}} {escape_latex(metrics)}")
        lines.append(r"\vspace{0.3cm}")

    # CTA and tone
    cta = new_state.get('primary_cta', '')
    tone = new_state.get('messaging_tone', '')
    if cta or tone:
        lines.append(r"")
        info_parts = []
        if cta:
            info_parts.append(f"\\textbf{{CTA:}} {escape_latex(cta)}")
        if tone:
            info_parts.append(f"\\textbf{{Tone:}} {escape_latex(tone)}")
        lines.append(r"\noindent" + " \\quad | \\quad ".join(info_parts))
        lines.append(r"\vspace{0.3cm}")

    # Evidence of changes (if comparison available)
    evidence = analysis.get('evidence', {})
    if evidence and change_detected:
        lines.append(r"")
        lines.append(r"\noindent\textbf{\textcolor{accent}{Change Evidence:}}")
        lines.append(r"\begin{itemize}")
        for key, value in evidence.items():
            if value and value != 'No change' and str(value).strip():
                key_formatted = key.replace('_', ' ').title()
                lines.append(f"\\item \\textbf{{{escape_latex(key_formatted)}}}: {escape_latex(str(value))}")
        lines.append(r"\end{itemize}")

    # Source link
    homepage_url = homepage_analysis.get('url', '')
    if homepage_url:
        lines.append(r"\par\vspace{0.5cm}")
        lines.append(f"\\noindent\\textit{{\\small Source: \\url{{{escape_latex(homepage_url)}}}}}")

    if len(lines) <= 3:  # Only header, no content
        return ""

    return "\n".join(lines)


def format_key_metrics(result) -> str:
    """Format key metrics boxes - centered."""
    lines = []

    pricing = result.get('pricing_analysis', {})
    hiring = result.get('hiring_analysis', {})

    lines.append(r"\vspace{0.5cm}")
    lines.append(r"\begin{center}")

    # Pricing change indicator - Unknown if no pricing data
    if not pricing:
        pricing_status = "Unknown"
        pricing_color = "darkgrey"
    else:
        analysis = pricing.get('analysis', {}) if pricing else {}
        change_detected = analysis.get('change_detected', False)
        if change_detected:
            pricing_status = "Changed"
            pricing_color = "accent"
        else:
            pricing_status = "Stable"
            pricing_color = "navy"

    lines.append(r"\colorbox{lightgrey}{\parbox{0.28\textwidth}{\centering\vspace{3mm}{\large\textbf{\textcolor{" + pricing_color + r"}{" + pricing_status + r"}}}\\\vspace{1mm}{\small Pricing Status}\vspace{3mm}}}")
    lines.append(r"\hspace{0.03\textwidth}")

    # Job count
    total_jobs = hiring.get('total_jobs', 0) if hiring else 0
    lines.append(r"\colorbox{lightgrey}{\parbox{0.28\textwidth}{\centering\vspace{3mm}{\large\textbf{" + str(total_jobs) + r"}}\\\vspace{1mm}{\small Open Roles}\vspace{3mm}}}")
    lines.append(r"\hspace{0.03\textwidth}")

    # ATS type
    ats_url = result.get('ats_url', '')
    if ats_url:
        if 'ashby' in ats_url.lower():
            ats_type = 'Ashby'
        elif 'greenhouse' in ats_url.lower():
            ats_type = 'Greenhouse'
        elif 'lever' in ats_url.lower():
            ats_type = 'Lever'
        else:
            ats_type = 'Detected'
    else:
        ats_type = 'Unknown'

    lines.append(r"\colorbox{lightgrey}{\parbox{0.28\textwidth}{\centering\vspace{3mm}{\large\textbf{" + ats_type + r"}}\\\vspace{1mm}{\small ATS Platform}\vspace{3mm}}}")

    lines.append(r"\end{center}")
    lines.append(r"\vspace{0.3cm}")

    return "\n".join(lines)


def generate_report_for_competitor(result: dict, output_dir: str = ".") -> str:
    """Generate a PDF report for a single competitor."""

    name = result.get('name', 'Unknown')
    domain = result.get('domain', '')
    pricing = result.get('pricing_analysis', {})
    hiring = result.get('hiring_analysis')
    trends = result.get('hiring_trends')
    background = result.get('background', {})
    homepage = result.get('homepage_analysis', {})

    # Extract data (use 'or {}' to handle explicit None values)
    old_state = (pricing.get('old_state') or {}) if pricing else {}
    new_state = (pricing.get('new_state') or {}) if pricing else {}
    analysis = (pricing.get('analysis') or {}) if pricing else {}

    # Verdict - use executive summary from evaluator agent, or fallback
    verdict_text = result.get('executive_summary')

    if not verdict_text or verdict_text == "Executive summary unavailable.":
        # Fallback to old method if no executive summary
        verdict_text = (
            analysis.get('strategic_shift') or
            analysis.get('strategic_analysis') or
            analysis.get('summary') or
            "No significant changes detected."
        )

    if 'error' in analysis:
        verdict_text = "Analysis incomplete due to API error. Please re-run."

    # Clean verdict (escape only once)
    verdict = escape_latex(verdict_text)

    # Format sections
    old_plans = old_state.get('pricing_plans', [])
    new_plans = new_state.get('pricing_plans', [])

    pricing_section = ""
    if old_state or new_state:
        # Analysis paragraph
        evidence = analysis.get('evidence', {})
        if evidence:
            pricing_section += r"\noindent\textbf{\textcolor{accent}{Key Changes:}}" + "\n"
            pricing_section += r"\begin{itemize}" + "\n"
            for key, value in evidence.items():
                if value and value != 'N/A' and str(value).strip():
                    key_formatted = key.replace('_', ' ').title()
                    pricing_section += f"\\item \\textbf{{{escape_latex(key_formatted)}}}: {escape_latex(str(value))}\n"
            pricing_section += r"\end{itemize}" + "\n"
            pricing_section += r"\vspace{0.3cm}" + "\n"

        # Tagline comparison
        old_tagline = old_state.get('tagline', '')
        new_tagline = new_state.get('tagline', '')

        if old_tagline or new_tagline:
            pricing_section += r"\noindent\textbf{\textcolor{accent}{Positioning:}} "
            if old_tagline != new_tagline and old_tagline and new_tagline:
                pricing_section += f"Changed from ``{escape_latex(old_tagline[:80])}'' to ``\\textcolor{{accent}}{{{escape_latex(new_tagline[:80])}}}''"
            elif new_tagline:
                pricing_section += f"``{escape_latex(new_tagline[:100])}''"
            pricing_section += r"\vspace{0.4cm}" + "\n"

        # Pricing table
        pricing_section += "\n" + r"\noindent\textbf{\textcolor{accent}{Pricing Comparison:}}" + "\n"
        pricing_section += r"\vspace{0.2cm}" + "\n"
        pricing_section += format_pricing_table(old_plans, new_plans)

        # Add pricing source link
        pricing_url = result.get('pricing_url')
        if pricing_url:
            pricing_section += "\n" + r"\par\vspace{0.5cm}" + "\n"
            pricing_section += f"\\noindent\\textit{{\\small Source: \\url{{{escape_latex(pricing_url)}}}}}"
    else:
        pricing_section = r"\textit{No pricing data available.}"
        pricing_url = result.get('pricing_url')
        if pricing_url:
            pricing_section += f" (Attempted: \\url{{{escape_latex(pricing_url)}}})"

    hiring_section = format_hiring_section(hiring, trends, result)
    key_metrics = format_key_metrics(result)
    background_section = format_background_section(background, result)
    homepage_section = format_homepage_section(homepage, result)

    # Sources - collect as (label, url) tuples
    source_items = []

    # Pricing source
    if result.get('pricing_url'):
        source_items.append(("Pricing", result.get('pricing_url')))

    # Historical snapshot
    if result.get('historical_snapshot'):
        source_items.append(("Historical", result.get('historical_snapshot')))

    # Job source
    job_source = result.get('job_source', '')
    if job_source:
        # Parse job source to get URL
        if result.get('ats_url'):
            source_items.append(("Jobs/ATS", result.get('ats_url')))
        elif result.get('levelsfyi_url'):
            source_items.append(("Jobs/Levels.fyi", result.get('levelsfyi_url')))
        elif 'linkedin' in job_source.lower():
            source_items.append(("Jobs/LinkedIn", f"https://www.linkedin.com/company/{name.lower().replace(' ', '-')}/jobs/"))

    # Homepage source
    if homepage and homepage.get('url'):
        source_items.append(("Homepage", homepage.get('url')))

    # Background sources
    if background:
        wiki = background.get('wikipedia', {})
        if wiki and wiki.get('url'):
            source_items.append(("Wikipedia", wiki.get('url')))
        github = background.get('github', {})
        if github and github.get('url'):
            source_items.append(("GitHub", github.get('url')))

    # Format sources as clickable links
    sources = []
    for label, url in source_items:
        if url:
            sources.append(f"{label}: \\url{{{escape_latex(url)}}}")

    # Build document - don't double escape
    tex = LATEX_TEMPLATE
    tex = tex.replace("<<COMPETITOR>>", escape_latex(name))
    tex = tex.replace("<<DOMAIN>>", escape_latex(domain.replace('https://', '')))
    tex = tex.replace("<<TIMESTAMP>>", datetime.now().strftime('%Y-%m-%d %H:%M'))
    tex = tex.replace("<<VERDICT>>", verdict)  # Already escaped
    tex = tex.replace("<<KEY_METRICS>>", key_metrics)
    tex = tex.replace("<<BACKGROUND_SECTION>>", background_section)
    tex = tex.replace("<<PRICING_SECTION>>", pricing_section)
    tex = tex.replace("<<HOMEPAGE_SECTION>>", homepage_section)
    tex = tex.replace("<<HIRING_SECTION>>", hiring_section)
    # Format sources - use line breaks for readability when many sources
    if sources:
        if len(sources) > 2:
            # Multiple sources - use line breaks
            sources_text = r" \newline ".join(sources)
        else:
            sources_text = " | ".join(sources)
    else:
        sources_text = "No external sources available"
    tex = tex.replace("<<SOURCES>>", sources_text)

    # Write and compile
    safe_name = name.lower().replace(" ", "_").replace(".", "")
    tex_file = os.path.join(output_dir, f"report_{safe_name}.tex")
    pdf_file = os.path.join(output_dir, f"report_{safe_name}.pdf")

    with open(tex_file, 'w', encoding='utf-8') as f:
        f.write(tex)

    # Compile PDF
    if compile_pdf(tex_file):
        # Cleanup aux files
        for ext in ['.aux', '.log', '.out']:
            try:
                os.remove(tex_file.replace('.tex', ext))
            except:
                pass
        print(f"  ✓ Generated: {pdf_file}")
        return pdf_file
    else:
        print(f"  ✗ Failed to compile PDF for {name}")
        return None


# --- Markdown Report Generation ---

MARKDOWN_CSS = """
@page {
    size: A4;
    margin: 2cm;
    @bottom-center {
        content: "Sentinel Competitive Intelligence | Confidential";
        font-size: 9px;
        color: #505050;
    }
    @bottom-right {
        content: counter(page);
        font-size: 9px;
        color: #505050;
    }
}
body {
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #333;
}
h1 {
    color: #0a193c;
    font-size: 24pt;
    margin-bottom: 0.2em;
    border-bottom: 2px solid #0a193c;
    padding-bottom: 0.3em;
}
h2 {
    color: #0a193c;
    font-size: 16pt;
    margin-top: 1.5em;
    margin-bottom: 0.5em;
}
h3 {
    color: #0066cc;
    font-size: 12pt;
    margin-top: 1em;
    margin-bottom: 0.3em;
}
blockquote {
    background: #f0f4fa;
    border-left: 4px solid #0a193c;
    padding: 12px 16px;
    margin: 1em 0;
    font-style: normal;
}
blockquote p { margin: 0; }
table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.8em 0;
    font-size: 10pt;
}
th {
    background: #0a193c;
    color: white;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
}
td {
    padding: 6px 12px;
    border-bottom: 1px solid #ddd;
}
tr:nth-child(even) td { background: #f8f8f8; }
a { color: #0066cc; text-decoration: none; }
a:hover { text-decoration: underline; }
.subtitle {
    color: #505050;
    font-size: 13pt;
    margin-top: 0;
}
.meta {
    color: #505050;
    font-size: 10pt;
    text-align: right;
    float: right;
    margin-top: -3em;
}
.metrics-row {
    display: flex;
    gap: 12px;
    margin: 1em 0;
}
.metric-box {
    flex: 1;
    background: #f0f0f0;
    border-radius: 6px;
    padding: 12px;
    text-align: center;
}
.metric-box .value {
    font-size: 16pt;
    font-weight: bold;
    color: #0a193c;
}
.metric-box .label {
    font-size: 9pt;
    color: #505050;
    margin-top: 4px;
}
.accent { color: #0066cc; }
.signal-green { color: #228b22; }
.signal-red { color: #b41e1e; }
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 1.5em 0;
}
"""


def _md_metrics_html(result: dict) -> str:
    """Generate HTML metrics boxes for the markdown report (rendered in PDF)."""
    pricing = result.get('pricing_analysis', {})
    hiring = result.get('hiring_analysis', {})

    if not pricing:
        pricing_status = "Unknown"
    else:
        analysis = pricing.get('analysis', {}) if pricing else {}
        if analysis.get('change_detected', False):
            pricing_status = "Changed"
        else:
            pricing_status = "Stable"

    total_jobs = hiring.get('total_jobs', 0) if hiring else 0

    ats_url = result.get('ats_url', '')
    if ats_url:
        if 'ashby' in ats_url.lower():
            ats_type = 'Ashby'
        elif 'greenhouse' in ats_url.lower():
            ats_type = 'Greenhouse'
        elif 'lever' in ats_url.lower():
            ats_type = 'Lever'
        else:
            ats_type = 'Detected'
    else:
        ats_type = 'Unknown'

    return f"""<div class="metrics-row">
<div class="metric-box"><div class="value">{pricing_status}</div><div class="label">Pricing Status</div></div>
<div class="metric-box"><div class="value">{total_jobs}</div><div class="label">Open Roles</div></div>
<div class="metric-box"><div class="value">{ats_type}</div><div class="label">ATS Platform</div></div>
</div>"""


def _md_pricing_table(old_plans, new_plans) -> str:
    """Format pricing comparison as a markdown table."""
    if not old_plans and not new_plans:
        return "*No pricing data available.*\n"

    old_dict = {p.get('name', '').lower().strip(): p for p in (old_plans or [])} if old_plans else {}
    new_dict = {p.get('name', '').lower().strip(): p for p in (new_plans or [])} if new_plans else {}
    all_names = list(dict.fromkeys(list(old_dict.keys()) + list(new_dict.keys())))

    lines = ["| Plan | 6 Months Ago | Current |", "|------|-------------|---------|"]
    for name_key in all_names[:6]:
        if not name_key:
            continue
        old_p = old_dict.get(name_key, {})
        new_p = new_dict.get(name_key, {})

        display_name = normalize_plan_name(old_p.get('name') or new_p.get('name', 'N/A'))
        old_price = old_p.get('price', '—')
        new_price = new_p.get('price', '—')

        changed = old_p and new_p and old_p.get('price') != new_p.get('price')
        new_cell = f"**{new_price}**" if changed else new_price
        lines.append(f"| {display_name} | {old_price} | {new_cell} |")

    return "\n".join(lines) + "\n"


def generate_markdown_report_for_competitor(result: dict, output_dir: str = ".") -> str:
    """Generate a Markdown report (and PDF via weasyprint) for a single competitor."""

    name = result.get('name', 'Unknown')
    domain = result.get('domain', '')
    pricing = result.get('pricing_analysis', {})
    hiring = result.get('hiring_analysis')
    trends = result.get('hiring_trends')
    background = result.get('background', {})
    homepage = result.get('homepage_analysis', {})

    old_state = (pricing.get('old_state') or {}) if pricing else {}
    new_state = (pricing.get('new_state') or {}) if pricing else {}
    analysis = (pricing.get('analysis') or {}) if pricing else {}

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    domain_display = domain.replace('https://', '').replace('http://', '')

    md_lines = []

    # --- Header ---
    md_lines.append(f"# {name}\n")
    md_lines.append(f"*Competitive Intelligence Report*\n")
    md_lines.append(f'<div class="meta">Generated: {timestamp}<br>{domain_display}</div>\n')
    md_lines.append("---\n")

    # --- Strategic Verdict ---
    verdict_text = result.get('executive_summary')
    if not verdict_text or verdict_text == "Executive summary unavailable.":
        verdict_text = (
            analysis.get('strategic_shift') or
            analysis.get('strategic_analysis') or
            analysis.get('summary') or
            "No significant changes detected."
        )
    if 'error' in analysis:
        verdict_text = "Analysis incomplete due to API error. Please re-run."

    md_lines.append("## Strategic Verdict\n")
    md_lines.append(f"> {verdict_text}\n")

    # --- Key Metrics (HTML for PDF styling) ---
    md_lines.append(_md_metrics_html(result))
    md_lines.append("")

    # --- Company Background ---
    if background:
        summary = background.get('summary', {})
        if summary and len(summary) > 2:
            md_lines.append("## Company Background\n")

            def is_valid_field(val):
                if not val:
                    return False
                val_str = str(val).strip()
                if '|' in val_str or '=' in val_str or val_str.startswith('{') or val_str.startswith('['):
                    return False
                return len(val_str) > 0

            facts = []
            if is_valid_field(summary.get('founded')):
                facts.append(f"**Founded:** {summary['founded']}")
            if is_valid_field(summary.get('headquarters')):
                facts.append(f"**Headquarters:** {summary['headquarters']}")
            if is_valid_field(summary.get('employees')):
                facts.append(f"**Employees:** {summary['employees']}")
            if is_valid_field(summary.get('funding')):
                facts.append(f"**Funding:** ${summary['funding']}")
            if is_valid_field(summary.get('industry')):
                facts.append(f"**Industry:** {summary['industry']}")
            if facts:
                md_lines.append(" | ".join(facts) + "\n")

            if summary.get('founders'):
                md_lines.append(f"### Founders\n\n{summary['founders']}\n")

            desc = summary.get('description', '')
            if is_valid_description(desc):
                md_lines.append(f"### Overview\n\n{desc}\n")

            mission = summary.get('mission', '')
            if mission and mission != desc and is_valid_description(mission):
                md_lines.append(f"### Mission\n\n{mission}\n")

            news = background.get('recent_news', [])
            if news:
                md_lines.append("### Recent News\n")
                for item in news[:3]:
                    title = item.get('title', '')
                    url = item.get('url', '')
                    if title:
                        if url:
                            md_lines.append(f"- [{title}]({url})")
                        else:
                            md_lines.append(f"- {title}")
                md_lines.append("")

            github = background.get('github', {})
            if github and (github.get('public_repos') or github.get('total_stars')):
                repos = github.get('public_repos', 0)
                stars = github.get('total_stars', 0)
                org_url = github.get('url', '')
                gh_text = f"**Open Source:** {repos} public repos, {stars:,} total stars"
                if org_url:
                    gh_text += f" ([GitHub]({org_url}))"
                md_lines.append(gh_text + "\n")

    # --- Pricing Analysis ---
    md_lines.append("## Pricing Analysis\n")

    if old_state or new_state:
        evidence = analysis.get('evidence', {})
        if evidence:
            md_lines.append("### Key Changes\n")
            for key, value in evidence.items():
                if value and value != 'N/A' and str(value).strip():
                    key_formatted = key.replace('_', ' ').title()
                    md_lines.append(f"- **{key_formatted}:** {value}")
            md_lines.append("")

        old_tagline = old_state.get('tagline', '')
        new_tagline = new_state.get('tagline', '')
        if old_tagline or new_tagline:
            if old_tagline != new_tagline and old_tagline and new_tagline:
                md_lines.append(f'**Positioning:** Changed from "{old_tagline[:80]}" to "**{new_tagline[:80]}**"\n')
            elif new_tagline:
                md_lines.append(f'**Positioning:** "{new_tagline[:100]}"\n')

        md_lines.append("### Pricing Comparison\n")
        old_plans = old_state.get('pricing_plans', [])
        new_plans = new_state.get('pricing_plans', [])
        md_lines.append(_md_pricing_table(old_plans, new_plans))

        pricing_url = result.get('pricing_url')
        if pricing_url:
            md_lines.append(f"*Source: [{pricing_url}]({pricing_url})*\n")
    else:
        md_lines.append("*No pricing data available.*\n")
        pricing_url = result.get('pricing_url')
        if pricing_url:
            md_lines.append(f"*(Attempted: [{pricing_url}]({pricing_url}))*\n")

    # --- Homepage Intelligence ---
    if homepage and 'error' not in homepage:
        hp_new_state = homepage.get('new_state') or {}
        hp_analysis = homepage.get('analysis') or {}

        if hp_new_state and 'error' not in hp_new_state:
            md_lines.append("## Homepage Intelligence\n")

            change_detected = hp_analysis.get('change_detected', False)
            if change_detected:
                shift = hp_analysis.get('strategic_shift', '')
                magnitude = hp_analysis.get('change_magnitude', 'moderate')
                if shift:
                    md_lines.append(f"> **Strategic Shift ({magnitude}):** {shift}\n")

            hero = hp_new_state.get('hero_headline', '')
            sub_hero = hp_new_state.get('hero_subheadline', '')
            if hero:
                positioning = f'### Current Positioning\n\n"{hero}"'
                if sub_hero:
                    positioning += f" — {sub_hero}"
                md_lines.append(positioning + "\n")

            audience = hp_new_state.get('target_audience', '')
            if audience:
                md_lines.append(f"**Target Audience:** {audience}\n")

            value_props = hp_new_state.get('value_propositions', [])
            if value_props:
                md_lines.append("### Value Propositions\n")
                for prop in value_props[:4]:
                    md_lines.append(f"- {prop}")
                md_lines.append("")

            features = hp_new_state.get('key_features', [])
            if features:
                md_lines.append(f"**Key Features:** {', '.join(features[:5])}\n")

            social = hp_new_state.get('social_proof', {})
            if social:
                logos = social.get('customer_logos', [])
                metrics = social.get('metrics', '')
                if logos:
                    md_lines.append(f"**Notable Customers:** {', '.join(logos[:5])}\n")
                if metrics:
                    md_lines.append(f"**Metrics:** {metrics}\n")

            cta = hp_new_state.get('primary_cta', '')
            tone = hp_new_state.get('messaging_tone', '')
            if cta or tone:
                parts = []
                if cta:
                    parts.append(f"**CTA:** {cta}")
                if tone:
                    parts.append(f"**Tone:** {tone}")
                md_lines.append(" | ".join(parts) + "\n")

            evidence = hp_analysis.get('evidence', {})
            if evidence and change_detected:
                md_lines.append("### Change Evidence\n")
                for key, value in evidence.items():
                    if value and value != 'No change' and str(value).strip():
                        key_formatted = key.replace('_', ' ').title()
                        md_lines.append(f"- **{key_formatted}:** {value}")
                md_lines.append("")

            homepage_url = homepage.get('url', '')
            if homepage_url:
                md_lines.append(f"*Source: [{homepage_url}]({homepage_url})*\n")

    # --- Hiring Intelligence ---
    md_lines.append("## Hiring Intelligence\n")

    if hiring:
        total_jobs = hiring.get('total_jobs', 0)
        top_depts = hiring.get('top_departments', [])
        signals = hiring.get('strategic_signals', [])

        md_lines.append(f"**Total Open Positions:** {total_jobs}\n")

        if top_depts:
            md_lines.append("### Department Breakdown\n")
            md_lines.append("| Department | Roles |")
            md_lines.append("|-----------|-------|")
            for dept in top_depts[:5]:
                md_lines.append(f"| {dept['name']} | {dept['count']} |")
            md_lines.append("")

        if signals:
            md_lines.append("### Strategic Signals\n")
            md_lines.append("| Category | Roles | % |")
            md_lines.append("|----------|-------|---|")
            for sig in signals[:4]:
                md_lines.append(f"| {sig['category']} | {sig['count']} | {sig['percent']}% |")
            md_lines.append("")

        if trends:
            velocity = trends.get('velocity_change_percent', 0)
            old_count = trends.get('old_count', 0)
            new_count = trends.get('new_count', 0)

            if velocity > 10:
                md_lines.append(f"**Hiring Trends:** Velocity increased {velocity:.0f}% ({old_count} to {new_count} roles)\n")
            elif velocity < -10:
                md_lines.append(f"**Hiring Trends:** Velocity decreased {abs(velocity):.0f}% ({old_count} to {new_count} roles)\n")
            else:
                md_lines.append(f"**Hiring Trends:** Velocity stable ({new_count} roles)\n")

            new_roles = trends.get('new_roles', [])
            if new_roles:
                role_names = ", ".join([r.get('title', '')[:40] for r in new_roles[:3]])
                md_lines.append(f"**New Roles:** {role_names}\n")

        job_source_url = result.get('ats_url') or result.get('levelsfyi_url')
        if not job_source_url and 'linkedin' in result.get('job_source', '').lower():
            job_source_url = f"https://www.linkedin.com/company/{name.lower().replace(' ', '-')}/jobs/"
        if job_source_url:
            md_lines.append(f"*Source: [{job_source_url}]({job_source_url})*\n")
    else:
        md_lines.append("*No hiring data available (ATS not detected or unsupported).*\n")

    # --- Sources ---
    source_items = []
    if result.get('pricing_url'):
        source_items.append(("Pricing", result['pricing_url']))
    if result.get('historical_snapshot'):
        source_items.append(("Historical", result['historical_snapshot']))
    job_source = result.get('job_source', '')
    if job_source:
        if result.get('ats_url'):
            source_items.append(("Jobs/ATS", result['ats_url']))
        elif result.get('levelsfyi_url'):
            source_items.append(("Jobs/Levels.fyi", result['levelsfyi_url']))
        elif 'linkedin' in job_source.lower():
            source_items.append(("Jobs/LinkedIn", f"https://www.linkedin.com/company/{name.lower().replace(' ', '-')}/jobs/"))
    if homepage and homepage.get('url'):
        source_items.append(("Homepage", homepage['url']))
    if background:
        wiki = background.get('wikipedia', {})
        if wiki and wiki.get('url'):
            source_items.append(("Wikipedia", wiki['url']))
        gh = background.get('github', {})
        if gh and gh.get('url'):
            source_items.append(("GitHub", gh['url']))

    md_lines.append("---\n")
    if source_items:
        md_lines.append("**Sources:** " + " | ".join([f"[{label}]({url})" for label, url in source_items if url]))
    else:
        md_lines.append("**Sources:** No external sources available")
    md_lines.append("")

    # Write markdown file
    md_content = "\n".join(md_lines)
    safe_name = name.lower().replace(" ", "_").replace(".", "")
    md_file = os.path.join(output_dir, f"report_{safe_name}.md")
    pdf_file = os.path.join(output_dir, f"report_{safe_name}_md.pdf")

    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"  ✓ Generated: {md_file}")

    # Compile PDF from markdown
    if compile_markdown_pdf(md_file, pdf_file):
        print(f"  ✓ Generated: {pdf_file}")
        return pdf_file
    else:
        print(f"  ⚠ Markdown PDF compilation failed (markdown .md file still available)")
        return md_file


def compile_markdown_pdf(md_file: str, pdf_file: str) -> bool:
    """Compile Markdown to PDF via HTML + weasyprint."""
    try:
        import markdown as md_lib
        from weasyprint import HTML
    except ImportError:
        print("\n⚠ Missing dependencies for Markdown PDF generation.")
        print("  Install with: pip install markdown weasyprint")
        return False

    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            md_text = f.read()

        html_body = md_lib.markdown(md_text, extensions=['tables', 'fenced_code'])

        html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{MARKDOWN_CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

        HTML(string=html_doc).write_pdf(pdf_file)
        return os.path.exists(pdf_file) and os.path.getsize(pdf_file) > 1000
    except Exception as e:
        print(f"  Markdown PDF compilation error: {e}")
        return False


def compile_pdf(tex_file: str) -> bool:
    """Compile LaTeX to PDF."""
    if not shutil.which("pdflatex"):
        print("\n❌ Error: 'pdflatex' not found.")
        print("To fix: brew install --cask basictex (on Mac)")
        return False

    try:
        cwd = os.path.dirname(tex_file) or "."
        basename = os.path.basename(tex_file)

        cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", basename]

        # Run twice for references
        subprocess.run(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        pdf_file = tex_file.replace(".tex", ".pdf")
        return os.path.exists(pdf_file) and os.path.getsize(pdf_file) > 1000
    except Exception as e:
        print(f"Compilation error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Generate Sentinel PDF Reports")
    parser.add_argument("--input", "-i", required=True, help="Intelligence JSON file from orchestrator")
    parser.add_argument("--competitor", "-c", help="Generate report for specific competitor only")
    parser.add_argument("--output", "-o", default="reports", help="Output directory for PDFs")
    parser.add_argument("--format", "-f", choices=["latex", "markdown", "both"], default="latex",
                        help="Report format: latex (default), markdown, or both")

    args = parser.parse_args()

    # Load intelligence data
    try:
        with open(args.input, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Failed to load input file: {e}")
        return

    results = data.get('results', [])
    if not results:
        print("❌ No competitor results found in input file.")
        return

    os.makedirs(args.output, exist_ok=True)

    fmt = args.format
    fmt_label = "LaTeX" if fmt == "latex" else "Markdown" if fmt == "markdown" else "LaTeX + Markdown"
    print(f"\n📄 Generating {fmt_label} Reports...")
    print(f"   Input: {args.input}")
    print(f"   Output: {args.output}/\n")

    generated = []

    for result in results:
        name = result.get('name', 'Unknown')

        # Filter by competitor if specified
        if args.competitor and args.competitor.lower() != name.lower():
            continue

        print(f"📝 Processing {name}...")

        if fmt in ("latex", "both"):
            pdf = generate_report_for_competitor(result, args.output)
            if pdf:
                generated.append(pdf)

        if fmt in ("markdown", "both"):
            md_pdf = generate_markdown_report_for_competitor(result, args.output)
            if md_pdf:
                generated.append(md_pdf)

    print(f"\n✅ Generated {len(generated)} report(s)")
    for path in generated:
        print(f"   - {path}")


if __name__ == "__main__":
    main()
