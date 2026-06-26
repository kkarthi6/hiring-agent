"""
Resume Reviewer Web API

FastAPI server that wraps the existing resume review engine
and serves a web frontend for resume evaluation.

Usage:
    python web/api.py
    # → http://localhost:8000
"""

import os
import sys
import json
import uuid
import shutil
import logging
import tempfile
from pathlib import Path
from typing import Optional

# Add parent directory to path so we can import existing modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from pdf import PDFHandler
from models import JSONResume, ResumeReview
from resume_reviewer import ResumeReviewEngine, _is_valid, save_review_markdown
from job_scraper import load_job_description
from transform import convert_json_resume_to_text
from prompt import DEFAULT_MODEL, MODEL_PARAMETERS
from config import DEVELOPMENT_MODE

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)5s - %(levelname)5s - %(message)s",
)

# ── App Setup ────────────────────────────────────────────────────────────

app = FastAPI(
    title="Resume Reviewer",
    description="AI-powered resume review against job descriptions",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store reviews in memory for download
_reviews: dict = {}

# Serve static files (frontend)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Routes ───────────────────────────────────────────────────────────────


@app.get("/")
async def serve_index():
    """Serve the main frontend page."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/health")
async def health_check():
    """Check if the server and LLM provider are reachable."""
    status = {
        "status": "ok",
        "model": DEFAULT_MODEL,
        "provider": os.getenv("LLM_PROVIDER", "ollama"),
        "base_url": os.getenv("OPENAI_COMPATIBLE_BASE_URL", "N/A"),
    }

    # Try to ping the LLM provider
    try:
        from llm_utils import initialize_llm_provider
        provider = initialize_llm_provider(DEFAULT_MODEL)
        # Send a tiny ping
        provider.chat(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            options={"temperature": 0.1},
        )
        status["llm_connected"] = True
    except Exception as e:
        status["llm_connected"] = False
        status["llm_error"] = str(e)

    return status


@app.post("/api/review")
async def run_review(
    resume: UploadFile = File(...),
    job_description: Optional[str] = Form(None),
    job_url: Optional[str] = Form(None),
):
    """
    Run a full resume review against a job description.

    Accepts a PDF file upload and either direct JD text or a URL to scrape.
    Returns a complete ResumeReview JSON.
    """
    # Validate inputs
    if not job_description and not job_url:
        raise HTTPException(
            status_code=400,
            detail="Provide either job_description (text) or job_url.",
        )

    if not resume.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Resume must be a PDF file.",
        )

    # Save uploaded PDF to a temp file
    tmp_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(tmp_dir, resume.filename)

    try:
        with open(pdf_path, "wb") as f:
            content = await resume.read()
            f.write(content)

        logger.info(f"📄 Received resume: {resume.filename} ({len(content)} bytes)")

        # Step 1: Load job description
        logger.info("⏳ Step 1/3: Loading job description...")
        try:
            jd_text = load_job_description(
                job_url=job_url,
                job_text=job_description,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Job description error: {e}")

        logger.info(f"✅ JD loaded ({len(jd_text)} chars)")

        # Verify LLM connection before extraction to provide clear error message
        try:
            from llm_utils import initialize_llm_provider
            test_provider = initialize_llm_provider(DEFAULT_MODEL)
            test_provider.chat(
                model=DEFAULT_MODEL,
                messages=[{"role": "user", "content": "ping"}],
                options={"temperature": 0.1},
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to connect to the LLM provider ({DEFAULT_MODEL}). Ensure your local LLM server is running and configured correctly. Details: {str(e)}",
            )

        # Step 2: Extract resume
        logger.info("⏳ Step 2/3: Extracting resume...")
        pdf_handler = PDFHandler()
        resume_data = pdf_handler.extract_json_from_pdf(pdf_path)

        if resume_data is None or not _is_valid(resume_data):
            raise HTTPException(
                status_code=422,
                detail="Could not extract meaningful data from the PDF. "
                       "Ensure it's a valid resume PDF with text content.",
            )

        resume_text = convert_json_resume_to_text(resume_data)
        logger.info(f"✅ Resume extracted ({len(resume_text)} chars)")

        # Step 3: Run AI review
        logger.info("⏳ Step 3/3: Running AI review...")
        model_params = MODEL_PARAMETERS.get(
            DEFAULT_MODEL, {"temperature": 0.1, "top_p": 0.9}
        )
        engine = ResumeReviewEngine(
            model_name=DEFAULT_MODEL, model_params=model_params
        )

        try:
            review = engine.review(resume_text, jd_text)
        except Exception as e:
            logger.exception("Review failed")
            raise HTTPException(
                status_code=500,
                detail=f"LLM review failed: {str(e)}. "
                       "Make sure LM Studio or AnythingLLM is running with a model loaded.",
            )

        logger.info("✅ Review complete!")

        # Store for later download
        review_id = str(uuid.uuid4())[:8]
        _reviews[review_id] = review

        # Build response
        result = review.model_dump()
        result["review_id"] = review_id

        # Extract candidate name if available
        if (
            resume_data.basics
            and resume_data.basics.name
        ):
            result["candidate_name"] = resume_data.basics.name

        return JSONResponse(content=result)

    finally:
        # Cleanup temp files
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/api/report/{review_id}")
async def download_report(review_id: str):
    """Download a Markdown report for a completed review."""
    if review_id not in _reviews:
        raise HTTPException(status_code=404, detail="Review not found.")

    review = _reviews[review_id]

    # Generate markdown to a temp file
    tmp_dir = tempfile.mkdtemp()
    md_path = os.path.join(tmp_dir, f"review_{review_id}.md")

    try:
        save_review_markdown(review, md_path)
        return FileResponse(
            md_path,
            media_type="text/markdown",
            filename=f"resume_review_{review_id}.md",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")


# ── Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🚀 Resume Reviewer Web App")
    print("=" * 60)
    print(f"   Model:    {DEFAULT_MODEL}")
    print(f"   Provider: {os.getenv('LLM_PROVIDER', 'ollama')}")
    print(f"   URL:      http://localhost:8000")
    print("=" * 60 + "\n")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=300,  # Long timeout for LLM calls
    )
