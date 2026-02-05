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

% === PRICING ANALYSIS ===
\vspace{0.8cm}
\noindent{\Large\textbf{\textcolor{navy}{Pricing Analysis}}}
\vspace{0.4cm}

<<PRICING_SECTION>>

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


def format_hiring_section(hiring_analysis, hiring_trends) -> str:
    """Format hiring analysis as LaTeX."""
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

    return "\n".join(lines)


def format_key_metrics(result) -> str:
    """Format key metrics boxes - centered."""
    lines = []

    pricing = result.get('pricing_analysis', {})
    hiring = result.get('hiring_analysis', {})

    analysis = pricing.get('analysis', {}) if pricing else {}
    change_detected = analysis.get('change_detected', False)

    lines.append(r"\vspace{0.5cm}")
    lines.append(r"\begin{center}")

    # Pricing change indicator
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

    # Extract data
    old_state = pricing.get('old_state', {}) if pricing else {}
    new_state = pricing.get('new_state', {}) if pricing else {}
    analysis = pricing.get('analysis', {}) if pricing else {}

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
    else:
        pricing_section = r"\textit{No pricing data available.}"

    hiring_section = format_hiring_section(hiring, trends)
    key_metrics = format_key_metrics(result)

    # Sources
    sources = []
    if pricing and pricing.get('url'):
        sources.append(escape_latex(pricing.get('url')))
    if result.get('historical_snapshot'):
        sources.append("Wayback Machine")
    if result.get('ats_url'):
        sources.append(escape_latex(result.get('ats_url')))

    # Build document - don't double escape
    tex = LATEX_TEMPLATE
    tex = tex.replace("<<COMPETITOR>>", escape_latex(name))
    tex = tex.replace("<<DOMAIN>>", escape_latex(domain.replace('https://', '')))
    tex = tex.replace("<<TIMESTAMP>>", datetime.now().strftime('%Y-%m-%d %H:%M'))
    tex = tex.replace("<<VERDICT>>", verdict)  # Already escaped
    tex = tex.replace("<<KEY_METRICS>>", key_metrics)
    tex = tex.replace("<<PRICING_SECTION>>", pricing_section)
    tex = tex.replace("<<HIRING_SECTION>>", hiring_section)
    tex = tex.replace("<<SOURCES>>", ", ".join(sources) if sources else "N/A")

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

    print(f"\n📄 Generating PDF Reports...")
    print(f"   Input: {args.input}")
    print(f"   Output: {args.output}/\n")

    generated = []

    for result in results:
        name = result.get('name', 'Unknown')

        # Filter by competitor if specified
        if args.competitor and args.competitor.lower() != name.lower():
            continue

        print(f"📝 Processing {name}...")
        pdf = generate_report_for_competitor(result, args.output)
        if pdf:
            generated.append(pdf)

    print(f"\n✅ Generated {len(generated)} report(s)")
    for pdf in generated:
        print(f"   - {pdf}")


if __name__ == "__main__":
    main()
