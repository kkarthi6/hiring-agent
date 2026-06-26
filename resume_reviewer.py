#!/usr/bin/env python3
"""
Resume Reviewer — Job-Description-Aware Resume Evaluation

Evaluates a resume PDF against a specific job description and produces
a comprehensive review with scores, gap analysis, keyword coverage,
and a ready-to-use LLM rewrite prompt.

Usage:
    # With LM Studio (default OpenAI-compatible provider)
    python resume_reviewer.py --resume /path/to/resume.pdf \\
        --job-url "https://example.com/job-posting"

    # With a pasted job description
    python resume_reviewer.py --resume /path/to/resume.pdf \\
        --job-text "We are looking for a Senior Backend Engineer..."

    # With a job description from a text file
    python resume_reviewer.py --resume /path/to/resume.pdf \\
        --job-file /path/to/jd.txt

    # Override provider / base URL from the command line
    python resume_reviewer.py --resume /path/to/resume.pdf \\
        --job-url "..." --base-url http://localhost:3001/api/v1
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from typing import Optional

from pdf import PDFHandler
from models import JSONResume, ResumeReview, CategoryScore
from llm_utils import initialize_llm_provider, extract_json_from_response
from prompts.template_manager import TemplateManager
from job_scraper import load_job_description
from transform import convert_json_resume_to_text
from prompt import DEFAULT_MODEL, MODEL_PARAMETERS
from config import DEVELOPMENT_MODE

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)5s - %(lineno)5d - %(funcName)33s - %(levelname)5s - %(message)s",
)


# ── Review Engine ────────────────────────────────────────────────────────


class ResumeReviewEngine:
    """Evaluates a resume against a job description using LLM."""

    def __init__(self, model_name: str = DEFAULT_MODEL, model_params: dict = None):
        if not model_name:
            raise ValueError("Model name cannot be empty")

        self.model_name = model_name
        self.model_params = model_params or MODEL_PARAMETERS.get(
            model_name, {"temperature": 0.1, "top_p": 0.9}
        )
        self.template_manager = TemplateManager()
        self.provider = initialize_llm_provider(self.model_name)

    def review(self, resume_text: str, job_description: str) -> ResumeReview:
        """
        Run the full review: send resume + JD to the LLM and parse the response.

        Args:
            resume_text: Plain-text representation of the resume.
            job_description: Plain-text job description.

        Returns:
            A validated ResumeReview Pydantic model.
        """
        # Render the evaluation prompt
        criteria_prompt = self.template_manager.render_template(
            "job_review_criteria",
            resume_text=resume_text,
            job_description=job_description,
        )
        if criteria_prompt is None:
            raise ValueError("Failed to load job_review_criteria template")

        system_message = self.template_manager.render_template(
            "job_review_system_message"
        )
        if system_message is None:
            raise ValueError("Failed to load job_review_system_message template")

        # Call the LLM
        chat_params = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": criteria_prompt},
            ],
            "options": {
                "stream": False,
                "temperature": self.model_params.get("temperature", 0.1),
                "top_p": self.model_params.get("top_p", 0.9),
            },
        }

        # Request JSON-structured output
        kwargs = {"format": ResumeReview.model_json_schema()}

        logger.info(f"🤖 Sending review request to {self.model_name}...")
        response = self.provider.chat(**chat_params, **kwargs)

        response_text = response["message"]["content"]
        response_text = extract_json_from_response(response_text)
        logger.debug(f"Raw response: {response_text[:500]}...")

        # Parse and validate
        review_dict = json.loads(response_text)
        review = ResumeReview(**review_dict)

        return review


# ── Output Formatting ────────────────────────────────────────────────────


def print_review_results(review: ResumeReview):
    """Pretty-print the review results to the console."""

    print("\n" + "=" * 80)
    print("📊 RESUME REVIEW RESULTS")
    print("=" * 80)

    # Overall Score
    score = review.overall_score
    if score >= 80:
        emoji = "🟢"
    elif score >= 60:
        emoji = "🟡"
    elif score >= 40:
        emoji = "🟠"
    else:
        emoji = "🔴"

    print(f"\n{emoji} OVERALL SCORE: {score:.0f}/100")

    # Category Breakdown
    print("\n📈 CATEGORY BREAKDOWN:")
    print("-" * 60)

    categories = [
        ("🔑 Keyword Match", review.keyword_match),
        ("💼 Experience Relevance", review.experience_relevance),
        ("🛠️  Skills Alignment", review.skills_alignment),
        ("📊 Impact Quantification", review.impact_quantification),
        ("📄 Presentation Quality", review.presentation_quality),
    ]

    for name, cat in categories:
        bar_len = int((cat.score / cat.max) * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {name:30s} {cat.score:5.1f}/{cat.max}  [{bar}]")
        print(f"    {cat.evidence}")
        print()

    # Keyword Analysis
    ka = review.keyword_analysis
    if ka.missing_keywords:
        print("❌ MISSING KEYWORDS:")
        print("-" * 40)
        for kw in ka.missing_keywords:
            print(f"  • {kw}")
        print()

    if ka.strong_matches:
        print("✅ STRONG MATCHES:")
        print("-" * 40)
        for m in ka.strong_matches:
            print(f"  • {m}")
        print()

    if ka.partial_matches:
        print("⚠️  PARTIAL MATCHES:")
        print("-" * 40)
        for m in ka.partial_matches:
            print(f"  • {m}")
        print()

    # Section Reviews
    print("📝 SECTION-BY-SECTION REVIEW:")
    print("-" * 60)
    for sr in review.section_reviews:
        icon = {"Strong": "✅", "Moderate": "⚠️ ", "Weak": "❌"}.get(sr.score, "•")
        print(f"\n  {icon} {sr.section} [{sr.score}]")
        print(f"    ✓ {sr.feedback}")
        print(f"    → {sr.suggestion}")

    # Top Improvements
    print(f"\n🚀 TOP IMPROVEMENTS (by impact):")
    print("-" * 40)
    for i, imp in enumerate(review.top_improvements, 1):
        print(f"  {i}. {imp}")

    # ATS Notes
    print(f"\n🤖 ATS COMPATIBILITY NOTES:")
    print("-" * 40)
    for note in review.ats_notes:
        print(f"  • {note}")

    print("\n" + "=" * 80)


def save_review_json(review: ResumeReview, output_path: str):
    """Save the full review as JSON."""
    data = review.model_dump()
    Path(output_path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n💾 JSON review saved to: {output_path}")


def save_review_markdown(review: ResumeReview, output_path: str):
    """Save a human-readable Markdown report."""
    lines = []
    lines.append("# Resume Review Report\n")

    score = review.overall_score
    lines.append(f"## Overall Score: {score:.0f}/100\n")

    # Category table
    lines.append("## Category Breakdown\n")
    lines.append("| Category | Score | Max | Rating |")
    lines.append("|----------|-------|-----|--------|")

    categories = [
        ("Keyword Match", review.keyword_match),
        ("Experience Relevance", review.experience_relevance),
        ("Skills Alignment", review.skills_alignment),
        ("Impact Quantification", review.impact_quantification),
        ("Presentation Quality", review.presentation_quality),
    ]

    for name, cat in categories:
        pct = (cat.score / cat.max) * 100
        if pct >= 80:
            rating = "🟢 Strong"
        elif pct >= 60:
            rating = "🟡 Good"
        elif pct >= 40:
            rating = "🟠 Fair"
        else:
            rating = "🔴 Weak"
        lines.append(f"| {name} | {cat.score:.1f} | {cat.max} | {rating} |")

    lines.append("")

    # Evidence
    lines.append("## Detailed Evidence\n")
    for name, cat in categories:
        lines.append(f"### {name}\n")
        lines.append(f"{cat.evidence}\n")

    # Keywords
    ka = review.keyword_analysis
    lines.append("## Keyword Analysis\n")

    if ka.missing_keywords:
        lines.append("### ❌ Missing Keywords\n")
        for kw in ka.missing_keywords:
            lines.append(f"- {kw}")
        lines.append("")

    if ka.strong_matches:
        lines.append("### ✅ Strong Matches\n")
        for m in ka.strong_matches:
            lines.append(f"- {m}")
        lines.append("")

    if ka.partial_matches:
        lines.append("### ⚠️ Partial Matches\n")
        for m in ka.partial_matches:
            lines.append(f"- {m}")
        lines.append("")

    # Section reviews
    lines.append("## Section Reviews\n")
    for sr in review.section_reviews:
        lines.append(f"### {sr.section} — {sr.score}\n")
        lines.append(f"**Feedback:** {sr.feedback}\n")
        lines.append(f"**Suggestion:** {sr.suggestion}\n")

    # Top improvements
    lines.append("## Top Improvements (by impact)\n")
    for i, imp in enumerate(review.top_improvements, 1):
        lines.append(f"{i}. {imp}")
    lines.append("")

    # ATS notes
    lines.append("## ATS Compatibility Notes\n")
    for note in review.ats_notes:
        lines.append(f"- {note}")
    lines.append("")

    # LLM rewrite prompt
    lines.append("---\n")
    lines.append("## 🤖 Ready-to-Use LLM Rewrite Prompt\n")
    lines.append("Copy the prompt below and paste it into any LLM along with your resume:\n")
    lines.append("```")
    lines.append(review.llm_rewrite_prompt)
    lines.append("```\n")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"📝 Markdown report saved to: {output_path}")


# ── PDF Processing ───────────────────────────────────────────────────────


def extract_resume(pdf_path: str) -> JSONResume:
    """Extract structured data from a resume PDF, using cache if available."""
    cache_filename = (
        f"cache/resumecache_{os.path.basename(pdf_path).replace('.pdf', '')}.json"
    )

    # Try cache first
    if DEVELOPMENT_MODE and os.path.exists(cache_filename):
        print(f"📂 Loading cached resume data from {cache_filename}")
        try:
            cached_data = json.loads(
                Path(cache_filename).read_text(encoding="utf-8")
            )
            resume = JSONResume(**cached_data)
            if _is_valid(resume):
                return resume
        except Exception as e:
            print(f"⚠️  Cache invalid ({e}), re-extracting from PDF...")

    # Extract fresh
    print(f"📄 Extracting resume from {pdf_path}...")
    pdf_handler = PDFHandler()
    resume_data = pdf_handler.extract_json_from_pdf(pdf_path)

    if resume_data is None:
        raise ValueError(f"Failed to extract data from {pdf_path}")

    # Cache for next run
    if DEVELOPMENT_MODE and _is_valid(resume_data):
        os.makedirs(os.path.dirname(cache_filename), exist_ok=True)
        Path(cache_filename).write_text(
            json.dumps(resume_data.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return resume_data


def _is_valid(resume: JSONResume) -> bool:
    """Check that at least one core section was extracted."""
    if not resume:
        return False
    return any([
        resume.basics,
        resume.work,
        resume.education,
        resume.skills,
        resume.projects,
    ])


# ── CLI ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Review a resume against a job description using a local LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage with a job posting URL
  python resume_reviewer.py --resume my_resume.pdf --job-url "https://boards.greenhouse.io/..."

  # With a text file containing the job description
  python resume_reviewer.py --resume my_resume.pdf --job-file job_description.txt

  # With direct text input
  python resume_reviewer.py --resume my_resume.pdf --job-text "We are hiring a ..."

  # Override the LM Studio / AnythingLLM base URL
  python resume_reviewer.py --resume my_resume.pdf --job-url "..." --base-url http://localhost:3001/api/v1

  # Specify a model name
  python resume_reviewer.py --resume my_resume.pdf --job-url "..." --model "mistral-7b-instruct"
        """,
    )

    # Required
    parser.add_argument(
        "--resume", "-r",
        required=True,
        help="Path to the resume PDF file.",
    )

    # Job description sources (at least one required)
    jd_group = parser.add_mutually_exclusive_group(required=True)
    jd_group.add_argument(
        "--job-url", "-u",
        help="URL of the job posting to scrape.",
    )
    jd_group.add_argument(
        "--job-text", "-t",
        help="Job description text (paste directly or use quotes).",
    )
    jd_group.add_argument(
        "--job-file", "-f",
        help="Path to a text file containing the job description.",
    )

    # Optional overrides
    parser.add_argument(
        "--model", "-m",
        default=None,
        help=f"Model name to use (default: {DEFAULT_MODEL} from .env).",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the OpenAI-compatible API base URL.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=".",
        help="Directory for output files (default: current directory).",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Validate resume path
    if not os.path.exists(args.resume):
        print(f"❌ Error: Resume file not found: {args.resume}")
        sys.exit(1)

    # Apply CLI overrides to environment (before importing prompt module config)
    if args.base_url:
        os.environ["OPENAI_COMPATIBLE_BASE_URL"] = args.base_url
        # Re-import to pick up the change
        import importlib
        import prompt as prompt_module
        importlib.reload(prompt_module)

    model_name = args.model or DEFAULT_MODEL

    print("\n" + "=" * 80)
    print("🔍 RESUME REVIEWER")
    print("=" * 80)
    print(f"📄 Resume:  {args.resume}")
    print(f"🤖 Model:   {model_name}")
    if args.job_url:
        print(f"🔗 Job URL: {args.job_url}")
    elif args.job_file:
        print(f"📁 Job File: {args.job_file}")
    else:
        print(f"📝 Job Text: {args.job_text[:80]}...")
    print("=" * 80 + "\n")

    # Step 1: Load job description
    print("⏳ Step 1/3: Loading job description...")
    try:
        job_description = load_job_description(
            job_url=args.job_url,
            job_text=args.job_text,
            job_file=args.job_file,
        )
    except ValueError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

    print(f"   ✅ Job description loaded ({len(job_description)} chars)\n")

    # Step 2: Extract resume
    print("⏳ Step 2/3: Extracting resume data...")
    try:
        resume_data = extract_resume(args.resume)
    except ValueError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

    resume_text = convert_json_resume_to_text(resume_data)
    print(f"   ✅ Resume extracted ({len(resume_text)} chars)\n")

    # Step 3: Run the review
    print("⏳ Step 3/3: Running AI-powered review...")
    model_params = MODEL_PARAMETERS.get(
        model_name, {"temperature": 0.1, "top_p": 0.9}
    )
    engine = ResumeReviewEngine(model_name=model_name, model_params=model_params)

    try:
        review = engine.review(resume_text, job_description)
    except Exception as e:
        print(f"❌ Error during review: {e}")
        logger.exception("Review failed")
        sys.exit(1)

    print("   ✅ Review complete!\n")

    # Output results
    print_review_results(review)

    # Save files
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    resume_basename = os.path.basename(args.resume).replace(".pdf", "")
    json_path = os.path.join(output_dir, f"review_{resume_basename}.json")
    md_path = os.path.join(output_dir, f"review_{resume_basename}.md")

    save_review_json(review, json_path)
    save_review_markdown(review, md_path)

    # Print the LLM rewrite prompt for quick access
    print("\n" + "=" * 80)
    print("🤖 READY-TO-USE LLM REWRITE PROMPT")
    print("=" * 80)
    print("Copy the prompt below and paste it into any LLM along with your resume:\n")
    print(review.llm_rewrite_prompt)
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
