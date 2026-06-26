"""
Job Description Scraper

Fetches and extracts clean job description text from URLs.
Handles common job boards (LinkedIn, Greenhouse, Lever, Workday, etc.)
with specialized parsers, and falls back to generic text extraction.
"""

import re
import logging
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Common user agent to avoid blocks
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Selectors for common job boards, ordered by specificity
BOARD_SELECTORS = {
    "greenhouse.io": [
        "#content .body",
        "#content",
        ".job-post",
        "#app_body",
    ],
    "lever.co": [
        ".posting-page .content",
        ".section-wrapper",
        ".posting-headline",
    ],
    "workday.com": [
        '[data-automation-id="jobPostingDescription"]',
        ".css-cygeeu",
        '[data-automation-id="job-posting-details"]',
    ],
    "linkedin.com": [
        ".show-more-less-html__markup",
        ".description__text",
        ".job-description",
    ],
    "indeed.com": [
        "#jobDescriptionText",
        ".jobsearch-JobComponent-description",
    ],
    "glassdoor.com": [
        ".desc",
        ".jobDescriptionContent",
    ],
    "angel.co": [
        ".job-description",
        '[class*="description"]',
    ],
    "wellfound.com": [
        ".job-description",
        '[class*="description"]',
    ],
    "ashbyhq.com": [
        '[class*="job-posting"]',
        ".ashby-job-posting-description",
    ],
    "myworkdayjobs.com": [
        '[data-automation-id="jobPostingDescription"]',
        ".css-cygeeu",
    ],
}

# Generic selectors tried when no board-specific match is found
GENERIC_SELECTORS = [
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[class*="job_description"]',
    '[id*="job-description"]',
    '[id*="jobDescription"]',
    '[id*="job_description"]',
    '[class*="posting-description"]',
    '[class*="description"]',
    "article",
    "main",
    '[role="main"]',
]

# Tags to strip from extracted content
STRIP_TAGS = [
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "iframe",
    "noscript",
    "form",
    "button",
    "input",
    "select",
    "textarea",
]


def _clean_text(text: str) -> str:
    """Clean extracted text — collapse whitespace, remove artifacts."""
    # Remove excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    # Remove common artifacts
    text = re.sub(r"(Apply Now|Share this job|Save Job|Report this job)\s*", "", text, flags=re.IGNORECASE)
    # Strip leading/trailing whitespace per line
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return text.strip()


def _extract_from_soup(soup: BeautifulSoup, selectors: list) -> Optional[str]:
    """Try a list of CSS selectors and return the first match's text."""
    for selector in selectors:
        try:
            elements = soup.select(selector)
            if elements:
                # Take the element with the most text content
                best = max(elements, key=lambda el: len(el.get_text(strip=True)))
                text = best.get_text(separator="\n", strip=True)
                if len(text) > 100:  # Minimum viable job description
                    return text
        except Exception:
            continue
    return None


def _identify_board(url: str) -> Optional[str]:
    """Identify which job board a URL belongs to."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    for board_domain in BOARD_SELECTORS:
        if board_domain in domain:
            return board_domain
    return None


def scrape_job_description(url: str, timeout: int = 15) -> str:
    """
    Scrape a job description from a URL.

    Args:
        url: The job posting URL.
        timeout: Request timeout in seconds.

    Returns:
        Cleaned job description text.

    Raises:
        ValueError: If the URL cannot be fetched or no content is extracted.
    """
    logger.info(f"🔗 Scraping job description from: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as e:
        raise ValueError(f"Failed to fetch URL '{url}': {e}")

    soup = BeautifulSoup(response.text, "lxml")

    # Strip unwanted tags
    for tag_name in STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Try board-specific selectors first
    board = _identify_board(url)
    if board:
        logger.info(f"📋 Detected job board: {board}")
        text = _extract_from_soup(soup, BOARD_SELECTORS[board])
        if text:
            return _clean_text(text)
        logger.info(f"⚠️  Board-specific selectors failed, trying generic selectors")

    # Try generic selectors
    text = _extract_from_soup(soup, GENERIC_SELECTORS)
    if text:
        return _clean_text(text)

    # Last resort: extract body text
    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
        if len(text) > 100:
            logger.warning("⚠️  Using full body text — may include non-JD content")
            return _clean_text(text)

    raise ValueError(
        f"Could not extract job description from '{url}'. "
        "Try using --job-text or --job-file instead."
    )


def load_job_description(
    job_url: Optional[str] = None,
    job_text: Optional[str] = None,
    job_file: Optional[str] = None,
) -> str:
    """
    Load a job description from one of three sources.

    Args:
        job_url: URL to scrape.
        job_text: Direct text input.
        job_file: Path to a text file.

    Returns:
        Job description text.

    Raises:
        ValueError: If no source is provided or content is too short.
    """
    if job_text:
        text = job_text.strip()
    elif job_file:
        try:
            with open(job_file, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except FileNotFoundError:
            raise ValueError(f"Job description file not found: {job_file}")
        except Exception as e:
            raise ValueError(f"Error reading job description file: {e}")
    elif job_url:
        text = scrape_job_description(job_url)
    else:
        raise ValueError(
            "No job description provided. Use --job-url, --job-text, or --job-file."
        )

    if len(text) < 50:
        raise ValueError(
            f"Job description is too short ({len(text)} chars). "
            "Please provide a more complete job description."
        )

    logger.info(f"✅ Job description loaded: {len(text)} characters")
    return text
