/**
 * Resume Reviewer — Frontend Application
 *
 * Single-page app that handles:
 *   - File upload (drag & drop + click)
 *   - Job description input (text or URL)
 *   - Submission to FastAPI backend
 *   - Results rendering (scores, keywords, section reviews)
 *   - Report download & prompt copying
 */

// ── State ───────────────────────────────────────────────────────────────

let selectedFile = null;
let currentReviewId = null;

// ── DOM References ──────────────────────────────────────────────────────

const views = {
    input: document.getElementById('inputView'),
    loading: document.getElementById('loadingView'),
    results: document.getElementById('resultsView'),
    error: document.getElementById('errorView'),
};

const elements = {
    statusBadge: document.getElementById('statusBadge'),
    dropZone: document.getElementById('dropZone'),
    resumeFile: document.getElementById('resumeFile'),
    uploadContent: document.getElementById('uploadContent'),
    uploadSuccess: document.getElementById('uploadSuccess'),
    fileName: document.getElementById('fileName'),
    clearFile: document.getElementById('clearFile'),
    jobText: document.getElementById('jobText'),
    jobUrl: document.getElementById('jobUrl'),
    submitBtn: document.getElementById('submitBtn'),
    reviewForm: document.getElementById('reviewForm'),
    errorMessage: document.getElementById('errorMessage'),
    retryBtn: document.getElementById('retryBtn'),
    newReviewBtn: document.getElementById('newReviewBtn'),
    downloadReportBtn: document.getElementById('downloadReportBtn'),
    copyPromptBtn: document.getElementById('copyPromptBtn'),
};

// ── View Management ─────────────────────────────────────────────────────

function showView(name) {
    Object.values(views).forEach(v => v.classList.remove('active'));
    views[name].classList.add('active');
}

// ── Health Check ────────────────────────────────────────────────────────

async function checkHealth() {
    const badge = elements.statusBadge;
    const dot = badge.querySelector('.status-dot');
    const text = badge.querySelector('.status-text');

    try {
        const res = await fetch('/api/health');
        const data = await res.json();

        if (data.status === 'ok') {
            dot.className = 'status-dot connected';
            text.textContent = data.model || 'Connected';
        } else {
            dot.className = 'status-dot error';
            text.textContent = 'Error';
        }
    } catch {
        dot.className = 'status-dot error';
        text.textContent = 'Offline';
    }
}

// ── File Upload ─────────────────────────────────────────────────────────

function setupFileUpload() {
    const { dropZone, resumeFile, uploadContent, uploadSuccess, fileName, clearFile } = elements;

    // Click to browse
    dropZone.addEventListener('click', (e) => {
        if (e.target === clearFile || e.target.closest('.btn-clear')) return;
        resumeFile.click();
    });

    // File selected via input
    resumeFile.addEventListener('change', () => {
        if (resumeFile.files.length > 0) {
            handleFileSelect(resumeFile.files[0]);
        }
    });

    // Drag & Drop
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            const file = e.dataTransfer.files[0];
            if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) {
                handleFileSelect(file);
            }
        }
    });

    // Clear file
    clearFile.addEventListener('click', (e) => {
        e.stopPropagation();
        selectedFile = null;
        resumeFile.value = '';
        uploadContent.hidden = false;
        uploadSuccess.hidden = true;
        updateSubmitState();
    });
}

function handleFileSelect(file) {
    selectedFile = file;
    elements.uploadContent.hidden = true;
    elements.uploadSuccess.hidden = false;
    elements.fileName.textContent = file.name;
    updateSubmitState();
}

// ── JD Tabs ─────────────────────────────────────────────────────────────

function setupTabs() {
    const tabs = document.querySelectorAll('.jd-tabs .tab');
    const tabText = document.getElementById('tabText');
    const tabUrl = document.getElementById('tabUrl');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            const target = tab.dataset.tab;
            tabText.classList.toggle('active', target === 'text');
            tabUrl.classList.toggle('active', target === 'url');
            updateSubmitState();
        });
    });
}

// ── Submit State ────────────────────────────────────────────────────────

function updateSubmitState() {
    const hasFile = selectedFile !== null;
    const activeTab = document.querySelector('.jd-tabs .tab.active')?.dataset.tab;
    const hasJD = activeTab === 'text'
        ? elements.jobText.value.trim().length > 30
        : elements.jobUrl.value.trim().length > 10;

    elements.submitBtn.disabled = !(hasFile && hasJD);
}

// ── Form Submission ─────────────────────────────────────────────────────

async function handleSubmit(e) {
    e.preventDefault();

    if (!selectedFile) return;

    // Build FormData
    const formData = new FormData();
    formData.append('resume', selectedFile);

    const activeTab = document.querySelector('.jd-tabs .tab.active')?.dataset.tab;
    if (activeTab === 'text') {
        formData.append('job_description', elements.jobText.value.trim());
    } else {
        formData.append('job_url', elements.jobUrl.value.trim());
    }

    // Show loading
    showView('loading');
    animateLoadingSteps();

    try {
        const res = await fetch('/api/review', {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(err.detail || `Server error (${res.status})`);
        }

        const data = await res.json();
        currentReviewId = data.review_id;
        renderResults(data);
        showView('results');

    } catch (err) {
        elements.errorMessage.textContent = err.message;
        showView('error');
    }
}

// ── Loading Animation ───────────────────────────────────────────────────

function animateLoadingSteps() {
    const steps = [
        document.getElementById('step1'),
        document.getElementById('step2'),
        document.getElementById('step3'),
    ];

    // Reset
    steps.forEach(s => {
        s.className = 'step';
        s.querySelector('.step-icon').textContent = '⏳';
    });

    // Step 1 starts immediately
    steps[0].classList.add('active');
    steps[0].querySelector('.step-icon').textContent = '⏳';

    // Simulate progress (actual timing depends on LLM)
    setTimeout(() => {
        steps[0].className = 'step done';
        steps[0].querySelector('.step-icon').textContent = '✅';
        steps[1].classList.add('active');
    }, 3000);

    setTimeout(() => {
        steps[1].className = 'step done';
        steps[1].querySelector('.step-icon').textContent = '✅';
        steps[2].classList.add('active');
    }, 15000);
}

// ── Results Rendering ───────────────────────────────────────────────────

function renderResults(data) {
    renderScoreHero(data);
    renderCategories(data);
    renderKeywords(data.keyword_analysis);
    renderSectionReviews(data.section_reviews);
    renderImprovements(data.top_improvements);
    renderAtsNotes(data.ats_notes);
    renderRewritePrompt(data.llm_rewrite_prompt);
}

function renderScoreHero(data) {
    const score = Math.round(data.overall_score);
    const scoreValue = document.getElementById('scoreValue');
    const scoreLabel = document.getElementById('scoreLabel');
    const scoreDesc = document.getElementById('scoreDescription');
    const ringFill = document.getElementById('scoreRingFill');
    const hero = document.getElementById('scoreHero');

    // Set score text
    scoreValue.textContent = score;

    // Set label and color
    let label, description, color;
    if (score >= 80) {
        label = 'Excellent Match';
        description = 'Your resume is well-aligned with this role. Focus on the specific improvements below to make it even stronger.';
        color = 'var(--green)';
    } else if (score >= 60) {
        label = 'Good Match';
        description = 'Solid foundation with room for improvement. Address the keyword gaps and section suggestions below.';
        color = 'var(--accent-light)';
    } else if (score >= 40) {
        label = 'Fair Match';
        description = 'Significant gaps exist between your resume and this role. The improvements below will help close the gap.';
        color = 'var(--orange)';
    } else {
        label = 'Needs Work';
        description = 'Major alignment issues found. Consider a substantial rewrite using the LLM prompt at the bottom of this page.';
        color = 'var(--red)';
    }

    scoreLabel.textContent = label;
    scoreDesc.textContent = description;

    // Animate ring
    const circumference = 2 * Math.PI * 52; // r=52
    const offset = circumference - (score / 100) * circumference;
    ringFill.style.stroke = color;

    // Trigger animation after a frame
    requestAnimationFrame(() => {
        ringFill.style.strokeDashoffset = offset;
    });
}

function getBarColor(score, max) {
    const pct = (score / max) * 100;
    if (pct >= 80) return 'var(--green)';
    if (pct >= 60) return 'var(--accent-light)';
    if (pct >= 40) return 'var(--orange)';
    return 'var(--red)';
}

function renderCategories(data) {
    const grid = document.getElementById('categoriesGrid');
    const categories = [
        { key: 'keyword_match', icon: '🔑', name: 'Keyword Match' },
        { key: 'experience_relevance', icon: '💼', name: 'Experience Relevance' },
        { key: 'skills_alignment', icon: '🛠️', name: 'Skills Alignment' },
        { key: 'impact_quantification', icon: '📊', name: 'Impact Quantification' },
        { key: 'presentation_quality', icon: '📄', name: 'Presentation Quality' },
    ];

    grid.innerHTML = categories.map(cat => {
        const d = data[cat.key];
        if (!d) return '';
        const pct = (d.score / d.max) * 100;
        const color = getBarColor(d.score, d.max);

        return `
            <div class="category-card">
                <div class="category-header">
                    <span class="category-name">${cat.icon} ${cat.name}</span>
                    <span class="category-score" style="color: ${color}">${d.score}/${d.max}</span>
                </div>
                <div class="category-bar">
                    <div class="category-bar-fill" style="width: ${pct}%; background: ${color}"></div>
                </div>
                <p class="category-evidence">${escapeHtml(d.evidence)}</p>
            </div>
        `;
    }).join('');

    // Animate bars
    requestAnimationFrame(() => {
        grid.querySelectorAll('.category-bar-fill').forEach(bar => {
            bar.style.width = bar.style.width; // trigger reflow
        });
    });
}

function renderKeywords(ka) {
    if (!ka) return;
    const grid = document.getElementById('keywordGrid');

    const groups = [];

    if (ka.missing_keywords?.length) {
        groups.push({
            title: '❌ Missing Keywords',
            tags: ka.missing_keywords,
            className: 'missing',
        });
    }
    if (ka.strong_matches?.length) {
        groups.push({
            title: '✅ Strong Matches',
            tags: ka.strong_matches,
            className: 'strong',
        });
    }
    if (ka.partial_matches?.length) {
        groups.push({
            title: '⚠️ Partial Matches',
            tags: ka.partial_matches,
            className: 'partial',
        });
    }

    grid.innerHTML = groups.map(g => `
        <div class="keyword-group">
            <div class="keyword-group-title">${g.title}</div>
            <div class="keyword-tags">
                ${g.tags.map(t => `<span class="keyword-tag ${g.className}">${escapeHtml(t)}</span>`).join('')}
            </div>
        </div>
    `).join('');
}

function renderSectionReviews(sections) {
    if (!sections?.length) return;
    const container = document.getElementById('sectionReviews');

    container.innerHTML = sections.map(sr => `
        <div class="review-card">
            <div class="review-card-header">
                <span class="review-section-name">${escapeHtml(sr.section)}</span>
                <span class="review-badge ${sr.score.toLowerCase()}">${sr.score}</span>
            </div>
            <p class="review-feedback">✓ ${escapeHtml(sr.feedback)}</p>
            <p class="review-suggestion">→ ${escapeHtml(sr.suggestion)}</p>
        </div>
    `).join('');
}

function renderImprovements(items) {
    if (!items?.length) return;
    const list = document.getElementById('improvementsList');
    list.innerHTML = items.map(item => `<li>${escapeHtml(item)}</li>`).join('');
}

function renderAtsNotes(items) {
    if (!items?.length) return;
    const list = document.getElementById('atsList');
    list.innerHTML = items.map(item => `<li>${escapeHtml(item)}</li>`).join('');
}

function renderRewritePrompt(prompt) {
    if (!prompt) return;
    document.getElementById('rewritePrompt').textContent = prompt;
}

// ── Utilities ───────────────────────────────────────────────────────────

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ── Copy Prompt ─────────────────────────────────────────────────────────

function setupCopyPrompt() {
    elements.copyPromptBtn.addEventListener('click', async () => {
        const text = document.getElementById('rewritePrompt').textContent;
        try {
            await navigator.clipboard.writeText(text);
            const btn = elements.copyPromptBtn;
            btn.classList.add('copied');
            btn.querySelector('.copy-text').textContent = 'Copied!';
            setTimeout(() => {
                btn.classList.remove('copied');
                btn.querySelector('.copy-text').textContent = 'Copy';
            }, 2000);
        } catch {
            // Fallback
            const ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        }
    });
}

// ── Download Report ─────────────────────────────────────────────────────

function setupDownload() {
    elements.downloadReportBtn.addEventListener('click', () => {
        if (!currentReviewId) return;
        window.open(`/api/report/${currentReviewId}`, '_blank');
    });
}

// ── Navigation ──────────────────────────────────────────────────────────

function setupNavigation() {
    elements.retryBtn.addEventListener('click', () => showView('input'));
    elements.newReviewBtn.addEventListener('click', () => {
        showView('input');
        // Reset form
        selectedFile = null;
        elements.resumeFile.value = '';
        elements.uploadContent.hidden = false;
        elements.uploadSuccess.hidden = true;
        elements.jobText.value = '';
        elements.jobUrl.value = '';
        updateSubmitState();
    });
}

// ── Init ────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    checkHealth();
    setupFileUpload();
    setupTabs();
    setupCopyPrompt();
    setupDownload();
    setupNavigation();

    // Form validation on input
    elements.jobText.addEventListener('input', updateSubmitState);
    elements.jobUrl.addEventListener('input', updateSubmitState);

    // Form submit
    elements.reviewForm.addEventListener('submit', handleSubmit);

    // Periodic health check
    setInterval(checkHealth, 30000);
});
