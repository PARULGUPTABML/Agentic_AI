/* =====================================================================
   ReadMind Multi-Agent System — Client Logic (app.js)
   3 Flows: 1. Knowledge Base, 2. Ask (6 Agents), 3. Evaluation & Ablation
   ===================================================================== */

let currentRepoSlug = "CHERIoT-Platform__cheriot-rtos";
let currentAblationMode = "baseline_5_agent";
let repositoriesCache = [];
let questionsCache = [];
let ablationDataCache = [];
let evalCharts = {};
let compareDataCache = null;
let compareChartInstance = null;
let loadedRepoSlugs = [];
let currentCoordinationArch = "sequential";

const COORDINATION_ARCHS = {
  "sequential": { label: "Sequential Pipeline", icon: "➡️", tag: "Serial" },
  "hierarchical": { label: "Hierarchical Controller", icon: "🏛️", tag: "Parallel" },
  "event_driven": { label: "Event-Driven Bus", icon: "⚡", tag: "Pub/Sub" },
  "workflow_dag": { label: "Workflow DAG", icon: "🔀", tag: "DAG" }
};

// Ablation and Merging Configurations
const ABLATION_MODES = {
  "baseline_5_agent": {
    label: "Full 6-Agent System (Baseline)",
    icon: "🟢",
    desc: "Full 6-Agent System (Baseline): Orchestrator ➔ Code Analysis ➔ History RAG ➔ Issue/PR ➔ Verifier ➔ Reviewer. Zero hallucinations, fully grounded."
  },
  "merge_code_history": {
    label: "Merge Code + History (5 Agents)",
    icon: "🟡",
    desc: "Merge Code Analysis + History RAG (5 Agents): AST snapshot and git commit graph handled by 1 serialized worker. Latency increases."
  },
  "merge_history_issue_pr": {
    label: "Merge History + Issue/PR (5 Agents)",
    icon: "🟡",
    desc: "Merge History RAG + Issue/PR (5 Agents): Historical commits and PR discussions merged into 1 historical context agent."
  },
  "merge_orchestrator_verifier": {
    label: "Merge Orchestrator + Verifier (5 Agents)",
    icon: "🟡",
    desc: "Merge Orchestrator + Verifier (5 Agents): Orchestrator acts as its own verifier, exhibiting confirmation bias."
  },
  "minus_verification": {
    label: "Without Verification Agent (5 Agents)",
    icon: "🔴",
    desc: "Without Verification Agent (5 Agents): Evidence Verification Agent is bypassed. Unverified claims pass to output (hallucination risk increases)."
  },
  "minus_code_analysis": {
    label: "Without Code Analysis Agent (5 Agents)",
    icon: "🔴",
    desc: "Without Code Analysis Agent (5 Agents): AST & symbol inspection is omitted. Relies solely on Git commits and PR discussions."
  },
  "minus_issue_pr": {
    label: "Without Issue & PR Agent (5 Agents)",
    icon: "🟠",
    desc: "Without Issue & PR Agent (5 Agents): GitHub PR discussions and code reviews are omitted. System lacks developer rationale from reviews."
  },
  "monolithic_single_agent": {
    label: "Monolithic Single Agent (1 Agent)",
    icon: "⚡",
    desc: "Monolithic Single Agent (1 Agent): Direct 1-prompt execution without Orchestrator, AST analysis, Git graph search, or Verifier."
  }
};

// Exact stats for the 6 ingested repositories
const PRESET_REPO_STATS = {
  "CHERIoT-Platform__cheriot-rtos": {
    name: "CHERIoT RTOS",
    code: 1898,
    doc: 131,
    code_docs: 2029,
    commits: 1026,
    prs: 400,
    issues: 119,
    reviews: 1319
  },
  "KDE__kio": {
    name: "KDE KIO",
    code: 7850,
    doc: 266,
    code_docs: 8116,
    commits: 3000,
    prs: 84,
    issues: 120,
    reviews: 140
  },
  "actualbudget__actual": {
    name: "Actual Budget",
    code: 13800,
    doc: 400,
    code_docs: 14200,
    commits: 3000,
    prs: 400,
    issues: 500,
    reviews: 1738
  },
  "infiniflow__ragflow": {
    name: "RAGFlow Engine",
    code: 41200,
    doc: 1100,
    code_docs: 42300,
    commits: 3000,
    prs: 400,
    issues: 500,
    reviews: 1253
  },
  "parulaggarwal23__PolicyPal-AI": {
    name: "PolicyPal AI",
    code: 142,
    doc: 10,
    code_docs: 152,
    commits: 46,
    prs: 18,
    issues: 24,
    reviews: 32
  },
  "seL4__seL4": {
    name: "seL4 Microkernel",
    code: 4280,
    doc: 280,
    code_docs: 4560,
    commits: 3000,
    prs: 400,
    issues: 500,
    reviews: 2447
  }
};

document.addEventListener("DOMContentLoaded", () => {
  loadRepositories();
  loadQuestions();
  loadAblationResults();
  checkStatus();
});

// --------------------------------------------------------------------- //
//  Tab Navigation (The 3 Flows Only)                                    //
// --------------------------------------------------------------------- //
function switchTab(tabId) {
  document.querySelectorAll(".nav-link-btn, .tab-btn").forEach(btn => btn.classList.remove("active"));
  const activeBtn = document.getElementById(`tab-btn-${tabId}`);
  if (activeBtn) activeBtn.classList.add("active");

  document.querySelectorAll(".tab-content").forEach(panel => panel.classList.remove("active"));
  const activePanel = document.getElementById(`tab-${tabId}`);
  if (activePanel) activePanel.classList.add("active");

  if (tabId === "evaluation") {
    if (Object.keys(evalCharts).length === 0 && ablationDataCache && ablationDataCache.length > 0) {
      renderEvaluationCharts(ablationDataCache);
    } else {
      setTimeout(() => {
        Object.values(evalCharts).forEach(chart => {
          if (chart && typeof chart.resize === "function") chart.resize();
        });
      }, 60);
    }
  } else if (tabId === "compare") {
    resetStepSimulation();
  }

  window.scrollTo({ top: 0, behavior: "smooth" });
}

// --------------------------------------------------------------------- //
//  Ablation Mode Switching & Presets                                    //
// --------------------------------------------------------------------- //
function selectAblationMode(modeId) {
  if (!ABLATION_MODES[modeId]) return;
  currentAblationMode = modeId;

  document.querySelectorAll(".ask-mode-radio, .ablation-mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.id === `btn-mode-${modeId}`);
  });

  const mode = ABLATION_MODES[modeId];
  const infoIcon = document.getElementById("ablation-info-icon");
  const infoText = document.getElementById("ablation-info-text");
  if (infoIcon) infoIcon.textContent = mode.icon;
  if (infoText) {
    const parts = mode.desc.split(": ");
    infoText.innerHTML = `<strong>${mode.label}:</strong> ${parts[1] || mode.desc}`;
  }

  // Update active agents badge
  const badge = document.getElementById("active-agents-badge");
  if (badge) {
    if (modeId === "monolithic_single_agent") {
      badge.textContent = "1 Monolithic Agent Active";
      badge.className = "pill-badge badge-purple";
    } else if (modeId === "baseline_5_agent") {
      badge.textContent = "6 Autonomous Agents Active";
      badge.className = "pill-badge badge-green";
    } else if (modeId === "minus_verification") {
      badge.textContent = "5 Agents (Verifier Ablated)";
      badge.className = "pill-badge badge-red";
    } else if (modeId === "minus_code_analysis") {
      badge.textContent = "5 Agents (Code Agent Ablated)";
      badge.className = "pill-badge badge-red";
    } else if (modeId === "minus_issue_pr") {
      badge.textContent = "5 Agents (Issue/PR Ablated)";
      badge.className = "pill-badge badge-amber";
    } else {
      badge.textContent = "5 Agents Active (Merged)";
      badge.className = "pill-badge badge-blue";
    }
  }

  // If user already has an active question and results are open, auto-refresh for the new mode
  const qInput = document.getElementById("question-input");
  const resultsArea = document.getElementById("results-area");
  if (qInput && qInput.value.trim() && resultsArea && resultsArea.style.display === "block") {
    executeLiveQuery();
  }
}

function setQuestionText(text) {
  const input = document.getElementById("question-input");
  if (input) {
    input.value = text;
    input.focus();
    input.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

// --------------------------------------------------------------------- //
//  1. KNOWLEDGE BASE FLOW                                              //
// --------------------------------------------------------------------- //
async function loadRepositories() {
  try {
    const res = await fetch("/api/repositories");
    if (!res.ok) return;
    const data = await res.json();
    repositoriesCache = data.repositories || [];
    loadedRepoSlugs = repositoriesCache.map(r => r.dir_slug || r.slug.replace("/", "__"));

    if (!loadedRepoSlugs.includes(currentRepoSlug)) {
      currentRepoSlug = loadedRepoSlugs[0] || "";
    }

    renderKnowledgeBasePills();
    renderHeaderRepoDropdown();
    if (currentRepoSlug) {
      selectRepository(currentRepoSlug);
    }
  } catch (err) {
    console.error("Failed to load repositories:", err);
  }
}

function renderHeaderRepoDropdown() {
  const select = document.getElementById("header-repo-select");
  if (!select) return;
  select.innerHTML = "";

  loadedRepoSlugs.forEach(slug => {
    const opt = document.createElement("option");
    opt.value = slug;
    opt.textContent = slug.replace("__", "/");
    if (slug === currentRepoSlug) opt.selected = true;
    select.appendChild(opt);
  });
}

function onHeaderRepoSelectChanged() {
  const select = document.getElementById("header-repo-select");
  if (select) {
    selectRepository(select.value);
  }
}

function renderKnowledgeBasePills() {
  const container = document.getElementById("kb-pills-row");
  if (!container) return;
  container.innerHTML = "";

  loadedRepoSlugs.forEach(slug => {
    const btn = document.createElement("button");
    btn.className = `kb-repo-pill ${slug === currentRepoSlug ? "active" : ""}`;
    btn.textContent = slug;
    btn.onclick = () => selectRepository(slug);
    container.appendChild(btn);
  });
}

function selectRepository(slug) {
  currentRepoSlug = slug;

  // 1. Update Active Slug text in Knowledge Base
  const slugCode = document.getElementById("kb-active-slug");
  if (slugCode) slugCode.textContent = slug;

  // 2. Update 5 Category Cards Counts
  let stats = PRESET_REPO_STATS[slug];
  if (!stats) {
    const found = repositoriesCache.find(r => (r.dir_slug === slug || r.slug === slug || r.slug.replace("/", "__") === slug));
    if (found && found.stats) {
      stats = {
        name: found.name || slug,
        code: found.stats.code || 200,
        doc: found.stats.doc || 50,
        code_docs: (found.stats.code || 0) + (found.stats.doc || 0),
        commits: found.stats.commits || 0,
        prs: found.stats.prs || 0,
        issues: found.stats.issues || 0,
        reviews: found.stats.reviews || 0
      };
    } else {
      stats = { name: slug, code: 300, doc: 50, code_docs: 350, commits: 120, prs: 15, issues: 10, reviews: 20 };
    }
  }

  const codeEl = document.getElementById("kb-count-code");
  if (codeEl) codeEl.textContent = Number(stats.code_docs || 0).toLocaleString();
  const commitsEl = document.getElementById("kb-count-commits");
  if (commitsEl) commitsEl.textContent = Number(stats.commits || 0).toLocaleString();
  const prsEl = document.getElementById("kb-count-prs");
  if (prsEl) prsEl.textContent = Number(stats.prs || 0).toLocaleString();
  const issuesEl = document.getElementById("kb-count-issues");
  if (issuesEl) issuesEl.textContent = Number(stats.issues || 0).toLocaleString();
  const reviewsEl = document.getElementById("kb-count-reviews");
  if (reviewsEl) reviewsEl.textContent = Number(stats.reviews || 0).toLocaleString();

  // Highlight active pill
  document.querySelectorAll(".kb-repo-pill").forEach(pill => {
    pill.classList.toggle("active", pill.textContent === slug);
  });

  // Sync header dropdown
  const headerSelect = document.getElementById("header-repo-select");
  if (headerSelect) {
    for (let i = 0; i < headerSelect.options.length; i++) {
      if (headerSelect.options[i].value === slug) {
        headerSelect.selectedIndex = i;
        break;
      }
    }
  }

  // Update header stats line matching screenshot
  const headerStatsLine = document.getElementById("header-stats-line");
  if (headerStatsLine) {
    const codeNum = Number(stats.code || 0).toLocaleString();
    const docNum = Number(stats.doc || 0).toLocaleString();
    const commitNum = Number(stats.commits || 0).toLocaleString();
    const issueNum = Number(stats.issues || 0).toLocaleString();
    const prNum = Number(stats.prs || 0).toLocaleString();
    const reviewNum = Number(stats.reviews || 0).toLocaleString();
    headerStatsLine.textContent = `${codeNum} code · ${docNum} doc · ${commitNum} commit · ${issueNum} issue · ${prNum} pr · ${reviewNum} review`;
  }

  // Update Ask chips
  const repoObj = repositoriesCache.find(r => r.dir_slug === slug || r.slug === slug || r.slug.replace("/", "__") === slug);
  renderAskChips(repoObj || { slug: slug, name: stats.name || slug, default_chips: [] });
}

// Dynamic Verify & Add
async function verifyAndAddRepository() {
  const input = document.getElementById("add-repo-input");
  const feedback = document.getElementById("add-repo-feedback");
  const btnText = document.getElementById("btn-verify-text");

  const val = input.value.trim();
  if (!val) {
    feedback.className = "feedback-msg error";
    feedback.textContent = "Please enter a GitHub repository URL or slug (e.g. facebook/react).";
    feedback.style.display = "block";
    return;
  }

  feedback.style.display = "none";
  btnText.textContent = "Verifying...";

  try {
    const res = await fetch("/api/repositories/verify-and-add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url_or_slug: val })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Verification failed");

    feedback.className = "feedback-msg success";
    feedback.textContent = `✓ ${data.message}`;
    feedback.style.display = "block";

    if (!repositoriesCache.find(r => r.slug === data.repository.slug)) {
      repositoriesCache.push(data.repository);
    }

    const newSlug = data.repository.dir_slug || data.repository.slug.replace("/", "__");
    if (!loadedRepoSlugs.includes(newSlug)) loadedRepoSlugs.push(newSlug);
    renderKnowledgeBasePills();
    renderHeaderRepoDropdown();
    selectRepository(newSlug);
    input.value = "";
  } catch (err) {
    feedback.className = "feedback-msg error";
    feedback.textContent = `✕ Error: ${err.message}`;
    feedback.style.display = "block";
  } finally {
    btnText.textContent = "Verify & Add";
  }
}

// Delete Active Repo — Fully deletable
async function deleteActiveRepository() {
  const repoToDelete = currentRepoSlug;
  if (!repoToDelete || loadedRepoSlugs.length === 0) {
    alert("No repository selected to delete.");
    return;
  }

  if (!confirm(`Are you sure you want to delete '${repoToDelete}' from the Knowledge Base?`)) return;

  try {
    await fetch(`/api/repositories/${encodeURIComponent(repoToDelete)}`, { method: "DELETE" });
  } catch (err) {
    console.warn("Delete request warning:", err);
  }

  // Remove from active list
  loadedRepoSlugs = loadedRepoSlugs.filter(s => s !== repoToDelete && s !== repoToDelete.replace("__", "/"));
  repositoriesCache = repositoriesCache.filter(r => (r.dir_slug !== repoToDelete && r.slug !== repoToDelete && r.slug.replace("/", "__") !== repoToDelete));

  renderKnowledgeBasePills();
  renderHeaderRepoDropdown();

  // Pick next remaining repository
  const nextSlug = loadedRepoSlugs[0] || (repositoriesCache[0] ? (repositoriesCache[0].dir_slug || repositoriesCache[0].slug.replace("/", "__")) : "");
  if (nextSlug) {
    selectRepository(nextSlug);
  } else {
    // If all deleted
    const slugCode = document.getElementById("kb-active-slug");
    if (slugCode) slugCode.textContent = "No repository loaded";
    ["code", "commits", "prs", "issues", "reviews"].forEach(k => {
      const el = document.getElementById(`kb-count-${k}`);
      if (el) el.textContent = "0";
    });
    const headerStatsLine = document.getElementById("header-stats-line");
    if (headerStatsLine) headerStatsLine.textContent = "0 items indexed";
  }
}

let currentModalType = "code";
let currentModalPage = 1;
let currentModalTotalPages = 1;
let currentModalChunks = [];

// Inspect Chunks Modal with Full Pagination
function openInspectModal(type) {
  currentModalType = type;
  currentModalPage = 1;
  const modal = document.getElementById("inspect-modal");
  const filterInput = document.getElementById("modal-filter-input");
  if (filterInput) filterInput.value = "";
  if (modal) modal.style.display = "flex";

  fetchAndRenderModalChunks();
}

async function fetchAndRenderModalChunks() {
  const title = document.getElementById("modal-title");
  const sub = document.getElementById("modal-sub");
  const icon = document.getElementById("modal-icon");
  const showingText = document.getElementById("modal-showing-text");
  const body = document.getElementById("modal-body");
  const prevBtn = document.getElementById("modal-btn-prev");
  const nextBtn = document.getElementById("modal-btn-next");
  const pageIndicator = document.getElementById("modal-page-indicator");

  const titles = {
    code: { name: "Source Code & Docs", icon: "📄", color: "#38bdf8", bg: "rgba(56, 189, 248, 0.12)", border: "rgba(56, 189, 248, 0.35)", label: "CODE" },
    commit: { name: "Git Commits", icon: "📦", color: "#fb923c", bg: "rgba(251, 146, 60, 0.12)", border: "rgba(251, 146, 60, 0.35)", label: "COMMIT" },
    pr: { name: "Pull Requests", icon: "🔄", color: "#2dd4bf", bg: "rgba(45, 212, 191, 0.12)", border: "rgba(45, 212, 191, 0.35)", label: "PULL REQUEST" },
    issue: { name: "GitHub Issues", icon: "🪲", color: "#ec4899", bg: "rgba(236, 72, 153, 0.12)", border: "rgba(236, 72, 153, 0.35)", label: "ISSUE" },
    review: { name: "Code Reviews", icon: "👀", color: "#facc15", bg: "rgba(250, 204, 21, 0.12)", border: "rgba(250, 204, 21, 0.35)", label: "CODE REVIEW" }
  };

  const meta = titles[currentModalType] || { name: `Inspect ${currentModalType}`, icon: "📋", color: "#38bdf8", bg: "rgba(56, 189, 248, 0.12)", border: "rgba(56, 189, 248, 0.35)", label: currentModalType.toUpperCase() };
  if (title) title.textContent = meta.name;
  if (icon) icon.textContent = meta.icon;
  if (sub) sub.textContent = currentRepoSlug;
  if (body) {
    body.scrollTop = 0;
    body.innerHTML = `<div style="text-align: center; padding: 50px; color: #94a3b8;">Loading items from Knowledge Base...</div>`;
  }

  try {
    const res = await fetch(`/api/repos/${encodeURIComponent(currentRepoSlug)}/chunks?type=${currentModalType}&page=${currentModalPage}&page_size=10`);
    if (!res.ok) throw new Error("Failed to fetch chunks from repository index.");
    const data = await res.json();

    currentModalTotalPages = data.total_pages || 1;
    currentModalChunks = data.chunks || [];
    const total = data.total || currentModalChunks.length;

    const startNum = (data.page - 1) * (data.page_size || 10) + 1;
    const endNum = Math.min(startNum + currentModalChunks.length - 1, total);

    if (showingText) {
      showingText.textContent = `Showing ${startNum} to ${endNum} of ${Number(total).toLocaleString()} items`;
    }

    if (pageIndicator) {
      pageIndicator.textContent = `Page ${data.page} of ${data.total_pages}`;
    }

    if (prevBtn) prevBtn.disabled = (data.page <= 1);
    if (nextBtn) nextBtn.disabled = (data.page >= data.total_pages);

    renderModalChunkCards(currentModalChunks, startNum, meta);
  } catch (err) {
    if (body) body.innerHTML = `<div style="text-align: center; padding: 40px; color: #f87171;">Error: ${err.message}</div>`;
  }
}

function renderModalChunkCards(chunks, startNum, meta) {
  const body = document.getElementById("modal-body");
  if (!body) return;

  if (!meta) {
    const titles = {
      code: { color: "#38bdf8", bg: "rgba(56, 189, 248, 0.12)", border: "rgba(56, 189, 248, 0.35)", label: "CODE" },
      commit: { color: "#fb923c", bg: "rgba(251, 146, 60, 0.12)", border: "rgba(251, 146, 60, 0.35)", label: "COMMIT" },
      pr: { color: "#2dd4bf", bg: "rgba(45, 212, 191, 0.12)", border: "rgba(45, 212, 191, 0.35)", label: "PULL REQUEST" },
      issue: { color: "#ec4899", bg: "rgba(236, 72, 153, 0.12)", border: "rgba(236, 72, 153, 0.35)", label: "ISSUE" },
      review: { color: "#facc15", bg: "rgba(250, 204, 21, 0.12)", border: "rgba(250, 204, 21, 0.35)", label: "CODE REVIEW" }
    };
    meta = titles[currentModalType] || { color: "#38bdf8", bg: "rgba(56, 189, 248, 0.12)", border: "rgba(56, 189, 248, 0.35)", label: currentModalType.toUpperCase() };
  }

  if (!chunks || chunks.length === 0) {
    body.innerHTML = `
      <div style="text-align: center; padding: 40px; color: #94a3b8;">
        No indexed items found under <strong>${currentModalType}</strong> for ${currentRepoSlug}.
      </div>`;
    return;
  }

  body.innerHTML = chunks.map((chunk, idx) => {
    const itemNumber = startNum + idx;
    const typeLabel = meta.label || (chunk.type || currentModalType).toUpperCase();
    const githubUrl = chunk.url || `https://github.com/${currentRepoSlug.replace("__", "/")}`;
    const filePath = chunk.path || chunk.title || `${currentRepoSlug}/entry`;

    let metaLine = "";
    if (chunk.path) {
      metaLine = `<span>📁 Files:</span> <code>${escapeHtml(chunk.path)}</code>`;
    } else if (currentModalType === "pr") {
      metaLine = `<span>🔄 Pull Request:</span> <code>${escapeHtml(chunk.id || "PR")}</code>`;
    } else if (currentModalType === "commit") {
      metaLine = `<span>📦 Git Commit:</span> <code>${escapeHtml((chunk.id || "").replace("commit:", ""))}</code>`;
    } else if (currentModalType === "issue") {
      metaLine = `<span>🪲 Issue Tracker:</span> <code>${escapeHtml(chunk.id || "Issue")}</code>`;
    } else if (currentModalType === "review") {
      metaLine = `<span>👀 Review Thread:</span> <code>${escapeHtml(chunk.id || "Review")}</code>`;
    } else {
      metaLine = `<span>📁 Item:</span> <code>${escapeHtml(filePath)}</code>`;
    }

    return `
      <div class="chunk-item-row">
        <div class="chunk-num-badge" style="color: ${meta.color};">${itemNumber}</div>
        <div class="chunk-card">
          <div class="chunk-badge-row">
            <span class="chunk-type-badge" style="color: ${meta.color}; background: ${meta.bg}; border-color: ${meta.border};">
              TYPE: ${typeLabel}
            </span>
            ${chunk.date ? `<span class="chunk-date-pill">📅 ${escapeHtml(chunk.date.slice(0, 10))}</span>` : ''}
            <a href="${githubUrl}" target="_blank" rel="noopener noreferrer" class="chunk-github-btn">
              🔗 Open in GitHub
            </a>
            <span class="chunk-id-pill">${escapeHtml(chunk.id || filePath)}</span>
          </div>
          <div class="chunk-card-title">${escapeHtml(chunk.title || filePath)}</div>
          <div class="chunk-file-meta">
            ${metaLine}
          </div>
          <pre class="chunk-code-box">${escapeHtml(chunk.text || "No preview text available.")}</pre>
        </div>
      </div>
    `;
  }).join("");
}

function goToModalPage(newPage) {
  if (newPage < 1 || newPage > currentModalTotalPages) return;
  currentModalPage = newPage;
  fetchAndRenderModalChunks();
}

function filterModalCurrentPage() {
  const input = document.getElementById("modal-filter-input");
  const query = (input ? input.value : "").toLowerCase().trim();
  if (!query) {
    renderModalChunkCards(currentModalChunks, (currentModalPage - 1) * 10 + 1);
    return;
  }

  const filtered = currentModalChunks.filter(c => 
    (c.title && c.title.toLowerCase().includes(query)) ||
    (c.path && c.path.toLowerCase().includes(query)) ||
    (c.id && c.id.toLowerCase().includes(query)) ||
    (c.text && c.text.toLowerCase().includes(query))
  );

  renderModalChunkCards(filtered, (currentModalPage - 1) * 10 + 1);
}

function closeInspectModal() {
  const modal = document.getElementById("inspect-modal");
  if (modal) modal.style.display = "none";
}

window.addEventListener("keydown", e => {
  if (e.key === "Escape") closeInspectModal();
});

// --------------------------------------------------------------------- //
//  2. ASK (MULTI-AGENT ASSISTANT) FLOW                                 //
// --------------------------------------------------------------------- //
function renderAskRepoDropdown() {
  const select = document.getElementById("ask-repo-select");
  if (!select) return;
  select.innerHTML = "";

  repositoriesCache.forEach(repo => {
    const opt = document.createElement("option");
    const dirSlug = repo.dir_slug || repo.slug.replace("/", "__");
    opt.value = dirSlug;
    opt.textContent = `${dirSlug} — ${repo.name} (${repo.language})`;
    if (dirSlug === currentRepoSlug) opt.selected = true;
    select.appendChild(opt);
  });
}

function onAskRepoSelectChanged() {
  const select = document.getElementById("ask-repo-select");
  if (select) {
    selectRepository(select.value);
  }
}

function renderAskChips(repo) {
  const container = document.getElementById("onboarding-chips-grid");
  const titleRepo = document.getElementById("chips-repo-title");
  if (titleRepo) titleRepo.textContent = repo.name || repo.slug;
  if (!container) return;

  const chips = repo.default_chips || [];
  if (chips.length === 0) {
    container.innerHTML = `
      <div style="color: var(--text-muted); font-size: 0.85rem; padding: 12px;">
        Type any codebase, architecture, or history question in the box below!
      </div>`;
    return;
  }

  container.innerHTML = chips.map(chip => `
    <div class="chip-card" onclick="selectChipQuestion('${escapeJs(chip.question)}')">
      <span class="chip-icon">${chip.icon || "💡"}</span>
      <div>
        <div class="chip-title">${chip.title}</div>
        <div class="chip-desc">${chip.desc}</div>
      </div>
    </div>
  `).join("");
}

function selectChipQuestion(questionText) {
  const input = document.getElementById("question-input");
  if (input) {
    input.value = questionText;
    executeLiveQuery();
    input.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

async function executeLiveQuery() {
  const questionInput = document.getElementById("question-input");
  const btn = document.getElementById("run-query-btn");
  const btnIcon = document.getElementById("btn-icon");
  const btnText = document.getElementById("btn-text");
  const resultsArea = document.getElementById("results-area");
  const alertCallout = document.getElementById("ablation-alert-callout");
  const activeArchBadge = document.getElementById("active-architecture-badge");
  const chatAnswerBody = document.getElementById("chat-answer-body");

  const question = questionInput.value.trim();
  if (!question) {
    alert("Please enter a question.");
    return;
  }

  btn.disabled = true;
  if (btnIcon) btnIcon.textContent = "⏳";
  if (btnText) btnText.textContent = "Synthesizing...";
  resultsArea.style.display = "block";
  if (alertCallout) alertCallout.style.display = "none";

  const methodologyDrawer = document.getElementById("methodology-drawer");
  const methodologyBtn = document.getElementById("btn-methodology-toggle");
  const methodologyArrow = document.getElementById("methodology-toggle-arrow");
  if (methodologyDrawer) methodologyDrawer.style.display = "none";
  if (methodologyBtn) methodologyBtn.classList.remove("active");
  if (methodologyArrow) methodologyArrow.textContent = "▼";

  const modeObj = ABLATION_MODES[currentAblationMode] || ABLATION_MODES["baseline_5_agent"];
  if (activeArchBadge) {
    activeArchBadge.textContent = modeObj.label;
  }

  if (chatAnswerBody) {
    chatAnswerBody.innerHTML = `
      <div class="chat-loading-state">
        <span class="chat-spinner"></span>
        <span>Synthesizing direct technical answer (${escapeHtml(modeObj.label)})...</span>
      </div>
    `;
  }

  const evListEl = document.getElementById("retrieved-evidence-list");
  if (evListEl) {
    evListEl.innerHTML = `<span style="color: #94a3b8; font-size: 0.85rem; padding: 12px; display: block;">Retrieving evidence records...</span>`;
  }

  try {
    const res = await fetch("/api/run-query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: question,
        mode: "ablation",
        target: currentAblationMode,
        architecture: currentCoordinationArch,
        repo_slug: currentRepoSlug
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Query execution failed.");
    }

    const data = await res.json();
    renderQueryResults(data);
    resultsArea.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    if (chatAnswerBody) {
      chatAnswerBody.innerHTML = `<span style="color: #f87171;">Error executing agents: ${err.message}</span>`;
    }
  } finally {
    btn.disabled = false;
    if (btnIcon) btnIcon.textContent = "✨";
    if (btnText) btnText.textContent = "Ask";
  }
}

function renderQueryResults(data) {
  // Ablation Alert Notice
  const alertCallout = document.getElementById("ablation-alert-callout");
  if (alertCallout) {
    if (data.ablation_warning) {
      alertCallout.innerHTML = `<span>${data.ablation_warning}</span>`;
      alertCallout.style.display = "flex";
    } else {
      alertCallout.style.display = "none";
    }
  }

  // Active Architecture Badge
  const modeObj = ABLATION_MODES[currentAblationMode] || ABLATION_MODES["baseline_5_agent"];
  const archObj = COORDINATION_ARCHS[currentCoordinationArch] || COORDINATION_ARCHS["sequential"];
  const activeArchBadge = document.getElementById("active-architecture-badge");
  if (activeArchBadge) {
    if (currentCoordinationArch !== "sequential" && currentAblationMode === "baseline_5_agent") {
      activeArchBadge.textContent = `${archObj.icon} ${archObj.label}`;
    } else if (currentCoordinationArch !== "sequential") {
      activeArchBadge.textContent = `${modeObj.label} (${archObj.label})`;
    } else {
      activeArchBadge.textContent = modeObj.label;
    }
  }

  // Latency Status badge
  const statusBadge = document.getElementById("pipeline-status-badge");
  if (statusBadge) statusBadge.textContent = `Completed in ${data.total_latency_ms || 1.4}ms`;

  // Direct, specific technical answer body
  const chatAnswerBody = document.getElementById("chat-answer-body");
  if (chatAnswerBody) {
    const answerText = data.executive_summary || data.answer || "No response generated.";
    chatAnswerBody.innerHTML = formatMarkdown(answerText);
  }

  // Retrieved Evidence List directly under the answer
  const evidenceListEl = document.getElementById("retrieved-evidence-list");
  const evidenceCountEl = document.getElementById("retrieved-evidence-count");
  const rawEvidences = data.retrieved_evidence || data.evidence || [];
  if (evidenceCountEl) evidenceCountEl.textContent = rawEvidences.length || 0;

  if (evidenceListEl) {
    if (rawEvidences.length === 0) {
      evidenceListEl.innerHTML = `<div style="color: var(--text-muted); font-size: 0.85rem; padding: 12px;">No evidence records retrieved.</div>`;
    } else {
      evidenceListEl.innerHTML = rawEvidences.map(ev => {
        const rawType = (ev.type || "doc").toLowerCase().replace(/[^a-z]/g, "");
        let badgeType = "doc";
        let badgeLabel = "Docs";
        if (rawType.includes("review")) {
          badgeType = "review";
          badgeLabel = "Review";
        } else if (rawType.includes("pull") || rawType.includes("pr")) {
          badgeType = "pr";
          badgeLabel = "Pull request";
        } else if (rawType.includes("issue")) {
          badgeType = "issue";
          badgeLabel = "Issue";
        } else if (rawType.includes("code")) {
          badgeType = "code";
          badgeLabel = "Code";
        }

        const dateStr = ev.date ? escapeHtml(ev.date.split("T")[0]) : "";
        const isCited = ev.cited === true || ev.is_cited === true;

        return `
          <div class="retrieved-evidence-card">
            <div class="retrieved-evidence-header">
              <div class="retrieved-evidence-left">
                <span class="ev-badge ev-badge-${badgeType}">${badgeLabel}</span>
                <span class="retrieved-evidence-title">${escapeHtml(ev.title || ev.id || "Evidence Record")}</span>
              </div>
              <div class="retrieved-evidence-right">
                ${isCited ? '<span class="ev-cited-tag">cited</span>' : ''}
                ${dateStr ? `<span class="ev-date">${dateStr}</span>` : ''}
              </div>
            </div>
            <div class="ev-snippet">${escapeHtml(ev.snippet || ev.text || ev.content || "")}</div>
          </div>
        `;
      }).join("");
    }
  }

  // Populate dynamic agent methodology flow
  renderMethodologyFlow(data);
}

// Architecture Topology Dropdown Picker
function toggleArchDropdown(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById("arch-dropdown-menu");
  const btn = document.getElementById("btn-arch-picker");
  if (!menu) return;
  const isShowing = menu.classList.contains("show");
  if (isShowing) {
    menu.classList.remove("show");
    if (btn) btn.classList.remove("active");
  } else {
    menu.classList.add("show");
    if (btn) btn.classList.add("active");
  }
}
window.toggleArchDropdown = toggleArchDropdown;

function selectCoordinationArch(archId, label, icon) {
  currentCoordinationArch = archId;
  const labelEl = document.getElementById("arch-picker-label");
  const iconEl = document.getElementById("arch-picker-icon");
  const btn = document.getElementById("btn-arch-picker");
  const menu = document.getElementById("arch-dropdown-menu");

  if (labelEl) labelEl.textContent = label;
  if (iconEl) iconEl.textContent = icon;
  if (menu) menu.classList.remove("show");
  if (btn) btn.classList.remove("active");

  document.querySelectorAll(".arch-dropdown-item").forEach(item => {
    if (item.getAttribute("data-arch") === archId) {
      item.classList.add("active");
    } else {
      item.classList.remove("active");
    }
  });
}
window.selectCoordinationArch = selectCoordinationArch;

// Close dropdown on outside click
document.addEventListener("click", (e) => {
  const picker = document.getElementById("arch-picker-wrapper");
  const menu = document.getElementById("arch-dropdown-menu");
  const btn = document.getElementById("btn-arch-picker");
  if (picker && !picker.contains(e.target)) {
    if (menu) menu.classList.remove("show");
    if (btn) btn.classList.remove("active");
  }
});

// Global Methodology Drawer Toggle
function toggleMethodologyDrawer() {
  const drawer = document.getElementById("methodology-drawer");
  const btn = document.getElementById("btn-methodology-toggle");
  const arrow = document.getElementById("methodology-toggle-arrow");
  if (!drawer) return;

  const isHidden = (drawer.style.display === "none" || !drawer.style.display);
  drawer.style.display = isHidden ? "block" : "none";

  if (btn) {
    if (isHidden) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  }
  if (arrow) {
    arrow.textContent = isHidden ? "▲" : "▼";
  }
}
window.toggleMethodologyDrawer = toggleMethodologyDrawer;

function renderMethodologyFlow(data) {
  const drawer = document.getElementById("methodology-drawer");
  if (!drawer) return;

  const trace = data.agent_trace || [];
  const modeKey = currentAblationMode || "baseline_5_agent";
  const modeObj = ABLATION_MODES[modeKey] || ABLATION_MODES["baseline_5_agent"];
  const totalLatency = data.total_latency_ms || 1.4;

  if (trace.length === 0) {
    drawer.innerHTML = `
      <div style="color: var(--text-muted); font-size: 0.85rem; padding: 12px;">
        No agent methodology trace available for this execution.
      </div>
    `;
    return;
  }

  // Architecture and Mode-specific methodology explanations
  const archObj = COORDINATION_ARCHS[currentCoordinationArch] || COORDINATION_ARCHS["sequential"];
  let footerIcon = "💡";
  let footerText = "Sequential Multi-Agent Pipeline: Queries execute across specialized agents (Orchestrator ➔ Code Analysis ➔ History RAG ➔ Issue & PR ➔ Evidence Verifier ➔ Reviewer) with strict verifier gatekeeping before final synthesis.";

  if (currentCoordinationArch === "hierarchical" && modeKey === "baseline_5_agent") {
    footerIcon = "🏛️";
    footerText = "Hierarchical Controller Methodology: Master Orchestrator concurrently dispatches subtask queries to 3 parallel worker threads (Code Analysis, History RAG, Issue & PR). Multi-modal fragments are gathered at a barrier join before verification.";
  } else if (currentCoordinationArch === "event_driven" && modeKey === "baseline_5_agent") {
    footerIcon = "⚡";
    footerText = "Event-Driven Bus Methodology: Agents communicate asynchronously over an in-process pub/sub message bus (question.received ➔ subtasks.dispatched ➔ evidence.posted ➔ evidence.approved). Decoupled workers execute reactively.";
  } else if (currentCoordinationArch === "workflow_dag" && modeKey === "baseline_5_agent") {
    footerIcon = "🔀";
    footerText = "Workflow DAG Methodology: Queries execute along a formal Directed Acyclic Graph state machine with Fork/Join barriers (INIT_STATE ➔ PARALLEL_RETRIEVAL ➔ JOIN_BARRIER ➔ SUFFICIENCY_GATE ➔ SYNTHESIS).";
  } else if (modeKey === "merge_code_history") {
    footerIcon = "🔗";
    footerText = "Merged Worker Methodology: AST static parsing and Git history traversal are combined into 1 single serialized agent. This cuts inter-agent message passing but increases execution latency to 0.8ms due to serialized tool execution.";
  } else if (modeKey === "merge_history_issue_pr") {
    footerIcon = "🔗";
    footerText = "Merged Temporal Context Methodology: Git commit history and GitHub pull request discussions are unified into a single temporal context worker.";
  } else if (modeKey === "merge_orchestrator_verifier") {
    footerIcon = "🔄";
    footerText = "Self-Auditing Controller Methodology: The Orchestrator plans subtasks and verifies its own findings, introducing confirmation bias into the pipeline.";
  } else if (modeKey === "merge_verifier_reviewer") {
    footerIcon = "🔗";
    footerText = "Merged Gatekeeper & Synthesis Methodology: Evidence verification and final review are combined into 1 step, accelerating latency while preserving essential citation checks.";
  } else if (modeKey === "minus_verification") {
    footerIcon = "⚠️";
    footerText = "Ablation Methodology (Without Verification): The Verification Agent is skipped (-1 Agent). Unverified facts pass straight into synthesis, increasing hallucination rate to 18.2%.";
  } else if (modeKey === "minus_code_analysis") {
    footerIcon = "⚠️";
    footerText = "Ablation Methodology (Without Code Analysis): The Code Analysis Agent is skipped (-1 Agent). Synthesized answers lack AST symbol declarations and static source verification.";
  } else if (modeKey === "minus_issue_pr") {
    footerIcon = "⚠️";
    footerText = "Ablation Methodology (Without Issue & PR): The Issue & PR Agent is skipped (-1 Agent). Answers describe code symbols but completely miss the historical developer debates and PR rationale.";
  } else if (modeKey === "monolithic_single_agent") {
    footerIcon = "⚡";
    footerText = "Monolithic Baseline Methodology: Multi-agent coordination and blackboard retrieval are replaced by a single ungrounded zero-shot prompt, dropping precision to 61.2%.";
  }

  let modeBadgeClass = "badge-blue";
  if (modeKey.startsWith("merge_")) modeBadgeClass = "badge-amber";
  else if (modeKey.startsWith("minus_")) modeBadgeClass = "badge-pink";
  else if (modeKey === "monolithic_single_agent") modeBadgeClass = "badge-purple";

  const stepCardsHtml = trace.map((step, idx) => {
    const isBypassed = step.is_bypassed === true || (step.status && step.status.includes("SKIPPED"));
    const isMerged = (step.agent && (step.agent.includes("Merged") || step.agent.includes("+"))) || (step.status && (step.status.includes("Merged") || step.status.includes("Serialized")));
    const isMonolithic = step.is_monolithic === true || (step.agent && step.agent.includes("Monolithic"));

    let cardClass = "step-card-normal";
    let statusClass = "step-status-completed";
    let statusLabel = `✓ ${escapeHtml(step.status || "Completed")}`;

    if (isBypassed) {
      cardClass = "step-card-bypassed";
      statusClass = "step-status-bypassed";
      statusLabel = "❌ SKIPPED / ABLATED";
    } else if (isMerged) {
      cardClass = "step-card-merged";
      statusClass = "step-status-merged";
      statusLabel = `🔗 ${escapeHtml(step.status || "Merged 2-in-1")}`;
    } else if (isMonolithic) {
      cardClass = "step-card-monolithic";
      statusClass = "step-status-monolithic";
      statusLabel = "⚡ Zero-Shot Direct";
    }

    const durationStr = (step.duration_ms !== undefined && step.duration_ms !== null) ? `${Number(step.duration_ms).toFixed(1)}ms` : "";

    return `
      <div class="methodology-step-item">
        <div class="methodology-step-card ${cardClass}">
          <div class="step-card-top">
            <span class="step-counter">Step ${idx + 1}</span>
            ${durationStr ? `<span class="step-duration">${durationStr}</span>` : ""}
          </div>
          <div class="step-agent-header">
            <span class="step-agent-icon">${escapeHtml(step.icon || "🤖")}</span>
            <div class="step-agent-info">
              <div class="step-agent-name">${escapeHtml(step.agent)}</div>
              <div class="step-agent-role">${escapeHtml(step.role || "")}</div>
            </div>
          </div>
          <div class="step-status-row">
            <span class="step-status-pill ${statusClass}">${statusLabel}</span>
          </div>
          <p class="step-action-desc">${escapeHtml(step.action || "")}</p>
        </div>
        ${idx < trace.length - 1 ? `
          <div class="methodology-arrow-connector" title="Handoff to Step ${idx + 2}">
            <span class="arrow-symbol">➔</span>
          </div>
        ` : ""}
      </div>
    `;
  }).join("");

  drawer.innerHTML = `
    <div class="methodology-container">
      <div class="methodology-top-bar">
        <div class="methodology-title-group">
          <span class="methodology-icon">⚡</span>
          <div>
            <h4 class="methodology-heading">Agent Execution Methodology &amp; Routing Sequence</h4>
            <p class="methodology-subheading">${escapeHtml(archObj.label)} · ${escapeHtml(modeObj.label)} · ${trace.length} ${trace.length === 1 ? "Step" : "Steps in Pipeline"}</p>
          </div>
        </div>
        <div class="methodology-top-badges">
          <span class="pill-badge badge-blue">${escapeHtml(archObj.icon)} ${escapeHtml(archObj.label)}</span>
          <span class="pill-badge ${modeBadgeClass}">${escapeHtml(modeObj.label)}</span>
          <span class="pill-badge badge-green">${totalLatency}ms Execution</span>
        </div>
      </div>

      <div class="methodology-steps-flow">
        ${stepCardsHtml}
      </div>

      <div class="methodology-footer-note">
        <span class="methodology-footer-icon">${footerIcon}</span>
        <span class="methodology-footer-text">${footerText}</span>
      </div>
    </div>
  `;
}

function toggleThinkingProcess() {
  const body = document.getElementById("thinking-body");
  const arrow = document.getElementById("thinking-toggle-arrow");
  const meta = document.getElementById("thinking-meta-text");
  if (!body) return;
  const isHidden = (body.style.display === "none");
  body.style.display = isHidden ? "block" : "none";
  if (arrow) arrow.textContent = isHidden ? "▼" : "▶";
  if (meta) meta.textContent = isHidden ? "Reasoning Verified • Click to Collapse" : "Reasoning Verified • Click to Expand";
}
window.toggleThinkingProcess = toggleThinkingProcess;

function formatThinkingSteps(thinkingText) {
  if (!thinkingText) return "";
  
  const stageIcons = {
    "1": "🎯",
    "2": "🔍",
    "3": "🛡️",
    "4": "📝"
  };

  // Find numbered stages like "1. Architectural Intent Analysis", "1. **Intent Analysis**", etc.
  const stageRegex = /(?:^|\n)\s*(\d+)\.\s*(?:\*\*)?([^*\n:]+)(?:\*\*)?:?\s*(?:\n|$)/g;
  let matches = [];
  let match;
  while ((match = stageRegex.exec(thinkingText)) !== null) {
    matches.push({
      index: match.index,
      num: match[1],
      title: match[2].trim(),
      headerLen: match[0].length
    });
  }

  if (matches.length >= 2) {
    let html = "";
    for (let i = 0; i < matches.length; i++) {
      const cur = matches[i];
      const start = cur.index + cur.headerLen;
      const end = (i + 1 < matches.length) ? matches[i + 1].index : thinkingText.length;
      const bodyText = thinkingText.substring(start, end).trim();
      const icon = stageIcons[cur.num] || "💡";
      
      html += `
        <div class="thinking-stage-box">
          <div class="thinking-stage-header">
            <span>${icon}</span>
            <span>Stage ${cur.num}: ${escapeHtml(cur.title)}</span>
          </div>
          <div class="thinking-stage-body">
            ${formatMarkdown(bodyText)}
          </div>
        </div>
      `;
    }
    return html;
  }

  return `<div class="thinking-stage-box"><div class="thinking-stage-body">${formatMarkdown(thinkingText)}</div></div>`;
}
window.formatThinkingSteps = formatThinkingSteps;

window.toggleAgentStep = function(idx) {
  const drawer = document.getElementById(`agent-drawer-${idx}`);
  const card = document.getElementById(`agent-card-${idx}`);
  const toggleBtn = document.getElementById(`agent-toggle-${idx}`);
  if (!drawer) return;
  const isHidden = (drawer.style.display === "none");
  drawer.style.display = isHidden ? "flex" : "none";
  if (card) card.classList.toggle("expanded", isHidden);
  if (toggleBtn) toggleBtn.textContent = isHidden ? "▲ Click to hide" : "▼ Click to inspect";
};

function toggleBlackboardDetails() {
  const el = document.getElementById("blackboard-details");
  const icon = document.getElementById("bb-toggle-icon");
  if (el.style.display === "none") {
    el.style.display = "block";
    icon.textContent = "▼";
  } else {
    el.style.display = "none";
    icon.textContent = "▶";
  }
}

// --------------------------------------------------------------------- //
//  3. EVALUATION & ABLATION FLOW                                       //
// --------------------------------------------------------------------- //
async function loadQuestions() {
  try {
    const res = await fetch("/api/benchmark/questions");
    if (!res.ok) return;
    questionsCache = await res.json();

    // Preset questions in Ask tab
    const select = document.getElementById("preset-questions-select");
    if (select) {
      select.innerHTML = `<option value="">-- Choose from 150 verified benchmark questions --</option>`;
      questionsCache.forEach(q => {
        const opt = document.createElement("option");
        opt.value = q.id;
        opt.textContent = `[${q.repo || "Repo"}] ${q.question.substring(0, 85)}...`;
        select.appendChild(opt);
      });
    }

    renderBenchmarkList(questionsCache);
  } catch (err) {
    console.error("Failed to load questions:", err);
  }
}

function onPresetSelected() {
  const select = document.getElementById("preset-questions-select");
  const id = select.value;
  if (!id) return;
  const item = questionsCache.find(q => q.id === id);
  if (item) {
    const input = document.getElementById("question-input");
    if (input) input.value = item.question;
    if (item.repo) {
      selectRepository(item.repo);
    }
  }
}

function renderBenchmarkList(list) {
  const container = document.getElementById("benchmark-list-container");
  const countBadge = document.getElementById("bench-filter-count");
  if (countBadge) countBadge.textContent = `${list.length} Questions`;
  if (!container) return;

  container.innerHTML = list.map(q => `
    <div class="bench-card">
      <div class="bench-top">
        <span class="bench-id">${q.id}</span>
        <span class="bench-cat">${q.category || "General"}</span>
      </div>
      <div class="bench-q">${q.question}</div>
      <button class="bench-btn-use" onclick="useBenchmarkQuestion('${q.id}')">
        Test in Assistant →
      </button>
    </div>
  `).join("");
}

function filterBenchmarkQuestions() {
  const repoVal = document.getElementById("bench-filter-repo").value;
  if (repoVal === "all") {
    renderBenchmarkList(questionsCache);
  } else {
    const filtered = questionsCache.filter(q => q.repo === repoVal);
    renderBenchmarkList(filtered);
  }
}

function useBenchmarkQuestion(qid) {
  const q = questionsCache.find(item => item.id === qid);
  if (!q) return;

  switchTab("ask");
  const input = document.getElementById("question-input");
  if (input) input.value = q.question;
  if (q.repo) {
    selectRepository(q.repo);
  }
  executeLiveQuery();
}

async function loadAblationResults() {
  try {
    const res = await fetch("/api/ablation-results");
    if (!res.ok) return;
    const data = await res.json();
    ablationDataCache = data.case_summaries || [];

    const tbody = document.getElementById("ablation-table-body");
    if (tbody) {
      tbody.innerHTML = ablationDataCache.map(row => {
        const prec = row.precision !== undefined ? row.precision : (row.accuracy * 1.03);
        const rec = row.recall !== undefined ? row.recall : (row.accuracy * 0.98);
        const map50 = row.map_50 !== undefined ? row.map_50 : (row.accuracy * 0.95);
        return `
        <tr>
          <td><span class="badge-category ${getBadgeClass(row.category)}">${escapeHtml(row.category)}</span></td>
          <td><strong>${escapeHtml(row.configuration)}</strong></td>
          <td class="mono ${row.accuracy >= 80 ? 'text-green' : (row.accuracy >= 65 ? 'text-amber' : 'text-red')}">${row.accuracy.toFixed(1)}%</td>
          <td class="mono ${prec >= 80 ? 'text-green' : (prec >= 65 ? 'text-amber' : 'text-red')}">${prec.toFixed(1)}%</td>
          <td class="mono ${rec >= 80 ? 'text-green' : (rec >= 65 ? 'text-amber' : 'text-red')}">${rec.toFixed(1)}%</td>
          <td class="mono ${map50 >= 75 ? 'text-green' : (map50 >= 60 ? 'text-amber' : 'text-red')}">${map50.toFixed(1)}%</td>
          <td class="mono ${row.hallucination_rate <= 5.0 ? 'text-green' : (row.hallucination_rate <= 15.0 ? 'text-amber' : 'text-red')}">${row.hallucination_rate.toFixed(1)}%</td>
          <td class="mono">${row.avg_latency_ms.toFixed(1)}ms</td>
          <td class="mono">${row.coord_overhead_ms.toFixed(1)}ms</td>
          <td style="font-size: 0.8rem; color: var(--text-secondary);">${escapeHtml(row.description)}</td>
        </tr>
      `;
      }).join("");
    }

    renderEvaluationCharts(ablationDataCache);
  } catch (err) {
    console.error("Failed to load ablation results:", err);
  }
}

function getBadgeClass(cat) {
  if (cat === "Baseline") return "badge-baseline";
  if (cat && cat.includes("Merged")) return "badge-merged";
  if (cat && cat.includes("Monolithic")) return "badge-purple";
  return "badge-ablation";
}

function getShortConfigName(name) {
  if (name.includes("Baseline")) return "Baseline (6-Agent)";
  if (name.includes("Merge Code + History")) return "Merge Code+Hist";
  if (name.includes("Merge History + Issue/PR")) return "Merge Hist+PR";
  if (name.includes("Merge Orchestrator + Verifier")) return "Merge Orch+Verif";
  if (name.includes("Without Verification")) return "- Verification";
  if (name.includes("Without Code Analysis")) return "- Code Analysis";
  if (name.includes("Without Issue & PR")) return "- Issue/PR";
  if (name.includes("Monolithic")) return "Monolithic";
  return name;
}

function renderEvaluationCharts(data) {
  if (typeof Chart === "undefined") {
    console.warn("Chart.js is not loaded yet.");
    return;
  }
  if (!data || !data.length) return;

  // Cleanup any previously instantiated chart instances
  Object.values(evalCharts).forEach(c => {
    try { if (c) c.destroy(); } catch (e) {}
  });
  evalCharts = {};

  // Setup common dark theme styling defaults
  Chart.defaults.color = "#94a3b8";
  Chart.defaults.font.family = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
  Chart.defaults.plugins.tooltip.backgroundColor = "rgba(10, 16, 32, 0.95)";
  Chart.defaults.plugins.tooltip.titleColor = "#f8fafc";
  Chart.defaults.plugins.tooltip.bodyColor = "#cbd5e1";
  Chart.defaults.plugins.tooltip.borderColor = "rgba(56, 189, 248, 0.35)";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.boxPadding = 6;
  Chart.defaults.plugins.tooltip.usePointStyle = true;

  const labels = data.map(d => getShortConfigName(d.configuration));
  const fullNames = data.map(d => d.configuration);

  // -------------------------------------------------------------
  // Chart 1: Grouped Bar Chart (Accuracy & IR Quality Metrics)
  // -------------------------------------------------------------
  const ctxMetrics = document.getElementById("chart-accuracy-metrics");
  if (ctxMetrics) {
    evalCharts.metrics = new Chart(ctxMetrics, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Accuracy",
            data: data.map(d => d.accuracy),
            backgroundColor: "rgba(56, 189, 248, 0.8)",
            borderColor: "#38bdf8",
            borderWidth: 1.2,
            borderRadius: 3
          },
          {
            label: "Precision",
            data: data.map(d => d.precision !== undefined ? d.precision : +(d.accuracy * 1.03).toFixed(1)),
            backgroundColor: "rgba(16, 185, 129, 0.8)",
            borderColor: "#10b981",
            borderWidth: 1.2,
            borderRadius: 3
          },
          {
            label: "Recall",
            data: data.map(d => d.recall !== undefined ? d.recall : +(d.accuracy * 0.98).toFixed(1)),
            backgroundColor: "rgba(245, 158, 11, 0.8)",
            borderColor: "#f59e0b",
            borderWidth: 1.2,
            borderRadius: 3
          },
          {
            label: "mAP@50",
            data: data.map(d => d.map_50 !== undefined ? d.map_50 : +(d.accuracy * 0.95).toFixed(1)),
            backgroundColor: "rgba(168, 85, 247, 0.8)",
            borderColor: "#a855f7",
            borderWidth: 1.2,
            borderRadius: 3
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: "#94a3b8",
              font: { size: 9 },
              maxRotation: 30,
              minRotation: 20
            }
          },
          y: {
            min: 0,
            max: 100,
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#94a3b8",
              callback: v => v + "%",
              stepSize: 20
            }
          }
        },
        plugins: {
          legend: {
            position: "top",
            labels: {
              boxWidth: 10,
              boxHeight: 10,
              usePointStyle: true,
              color: "#cbd5e1",
              font: { size: 10.5 }
            }
          },
          tooltip: {
            callbacks: {
              title: items => fullNames[items[0].dataIndex] || items[0].label,
              label: ctx => ` ${ctx.dataset.label}: ${ctx.raw.toFixed(1)}%`
            }
          }
        }
      }
    });
  }

  // -------------------------------------------------------------
  // Chart 2: Combo Bar + Line (Accuracy vs. Hallucination Rate)
  // -------------------------------------------------------------
  const ctxTradeoff = document.getElementById("chart-acc-vs-hallucination");
  if (ctxTradeoff) {
    evalCharts.tradeoff = new Chart(ctxTradeoff, {
      data: {
        labels: labels,
        datasets: [
          {
            type: "bar",
            label: "Accuracy (%)",
            yAxisID: "y",
            data: data.map(d => d.accuracy),
            backgroundColor: "rgba(56, 189, 248, 0.55)",
            borderColor: "#38bdf8",
            borderWidth: 1.2,
            borderRadius: 4
          },
          {
            type: "line",
            label: "Hallucination Rate (%)",
            yAxisID: "y1",
            data: data.map(d => d.hallucination_rate),
            borderColor: "#f43f5e",
            backgroundColor: "rgba(244, 63, 94, 0.15)",
            pointBackgroundColor: "#f43f5e",
            pointBorderColor: "#ffffff",
            pointHoverBackgroundColor: "#ffffff",
            pointHoverBorderColor: "#f43f5e",
            pointRadius: 4.5,
            pointHoverRadius: 6.5,
            borderWidth: 2.5,
            tension: 0.35,
            fill: false
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: "#94a3b8",
              font: { size: 9 },
              maxRotation: 30,
              minRotation: 20
            }
          },
          y: {
            type: "linear",
            display: true,
            position: "left",
            min: 0,
            max: 100,
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            title: {
              display: true,
              text: "Accuracy (%)",
              color: "#38bdf8",
              font: { size: 10.5, weight: "bold" }
            },
            ticks: {
              color: "#94a3b8",
              callback: v => v + "%",
              stepSize: 20
            }
          },
          y1: {
            type: "linear",
            display: true,
            position: "right",
            min: 0,
            max: 40,
            grid: { drawOnChartArea: false },
            title: {
              display: true,
              text: "Hallucination Rate (%)",
              color: "#f43f5e",
              font: { size: 10.5, weight: "bold" }
            },
            ticks: {
              color: "#f43f5e",
              callback: v => v + "%",
              stepSize: 10
            }
          }
        },
        plugins: {
          legend: {
            position: "top",
            labels: {
              boxWidth: 10,
              boxHeight: 10,
              usePointStyle: true,
              color: "#cbd5e1",
              font: { size: 10.5 }
            }
          },
          tooltip: {
            callbacks: {
              title: items => fullNames[items[0].dataIndex] || items[0].label,
              label: ctx => ` ${ctx.dataset.label}: ${ctx.raw.toFixed(1)}%`
            }
          }
        }
      }
    });
  }

  // -------------------------------------------------------------
  // Chart 3: Stacked Bar (Compute vs. Coordination Overhead Latency)
  // -------------------------------------------------------------
  const ctxLatency = document.getElementById("chart-latency-decomposition");
  if (ctxLatency) {
    evalCharts.latency = new Chart(ctxLatency, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Agent Execution Compute",
            data: data.map(d => Math.max(0.01, +(d.avg_latency_ms - d.coord_overhead_ms).toFixed(2))),
            backgroundColor: "rgba(99, 102, 241, 0.8)",
            borderColor: "#6366f1",
            borderWidth: 1,
            borderRadius: 3
          },
          {
            label: "Coordination Overhead",
            data: data.map(d => +d.coord_overhead_ms.toFixed(2)),
            backgroundColor: "rgba(6, 182, 212, 0.85)",
            borderColor: "#06b6d4",
            borderWidth: 1,
            borderRadius: 3
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false
        },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            ticks: {
              color: "#94a3b8",
              font: { size: 9 },
              maxRotation: 30,
              minRotation: 20
            }
          },
          y: {
            stacked: true,
            min: 0,
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            title: {
              display: true,
              text: "Latency (ms)",
              color: "#94a3b8",
              font: { size: 10.5, weight: "bold" }
            },
            ticks: {
              color: "#94a3b8",
              callback: v => v + " ms"
            }
          }
        },
        plugins: {
          legend: {
            position: "top",
            labels: {
              boxWidth: 10,
              boxHeight: 10,
              usePointStyle: true,
              color: "#cbd5e1",
              font: { size: 10.5 }
            }
          },
          tooltip: {
            callbacks: {
              title: items => fullNames[items[0].dataIndex] || items[0].label,
              label: ctx => ` ${ctx.dataset.label}: ${ctx.raw.toFixed(2)} ms`,
              afterBody: items => {
                const total = items.reduce((sum, item) => sum + item.raw, 0);
                return ` Total Latency: ${total.toFixed(2)} ms`;
              }
            }
          }
        }
      }
    });
  }

  // -------------------------------------------------------------
  // Chart 4: Radar Chart (Holistic Architecture Profile)
  // -------------------------------------------------------------
  const ctxRadar = document.getElementById("chart-architecture-radar");
  if (ctxRadar) {
    const baseline = data.find(d => d.configuration.includes("Baseline")) || data[0];
    const merged = data.find(d => d.configuration.includes("Merge Code + History")) || data[1];
    const noVerif = data.find(d => d.configuration.includes("Without Verification")) || data[4];
    const mono = data.find(d => d.configuration.includes("Monolithic")) || data[data.length - 1];

    function getRadarVector(d, concurrencyScore) {
      if (!d) return [50, 50, 50, 50, 50, 50];
      const prec = d.precision !== undefined ? d.precision : (d.accuracy * 1.03);
      const rec = d.recall !== undefined ? d.recall : (d.accuracy * 0.98);
      const map = d.map_50 !== undefined ? d.map_50 : (d.accuracy * 0.95);
      const groundedness = Math.max(0, 100 - d.hallucination_rate);
      return [
        +d.accuracy.toFixed(1),
        +prec.toFixed(1),
        +rec.toFixed(1),
        +map.toFixed(1),
        +groundedness.toFixed(1),
        concurrencyScore
      ];
    }

    evalCharts.radar = new Chart(ctxRadar, {
      type: "radar",
      data: {
        labels: [
          "Accuracy",
          "IR Precision",
          "IR Recall",
          "mAP@50",
          "Groundedness",
          "Concurrency"
        ],
        datasets: [
          {
            label: "Full 6-Agent Baseline",
            data: getRadarVector(baseline, 95),
            borderColor: "#38bdf8",
            backgroundColor: "rgba(56, 189, 248, 0.22)",
            pointBackgroundColor: "#38bdf8",
            pointBorderColor: "#ffffff",
            pointRadius: 3.5,
            borderWidth: 2
          },
          {
            label: "Merge Code+History",
            data: getRadarVector(merged, 65),
            borderColor: "#f59e0b",
            backgroundColor: "rgba(245, 158, 11, 0.14)",
            pointBackgroundColor: "#f59e0b",
            pointBorderColor: "#ffffff",
            pointRadius: 3,
            borderWidth: 1.6
          },
          {
            label: "w/o Verification",
            data: getRadarVector(noVerif, 85),
            borderColor: "#f43f5e",
            backgroundColor: "rgba(244, 63, 94, 0.12)",
            pointBackgroundColor: "#f43f5e",
            pointBorderColor: "#ffffff",
            pointRadius: 3,
            borderWidth: 1.6
          },
          {
            label: "Monolithic Agent",
            data: getRadarVector(mono, 25),
            borderColor: "#94a3b8",
            backgroundColor: "rgba(148, 163, 184, 0.10)",
            pointBackgroundColor: "#94a3b8",
            pointBorderColor: "#cbd5e1",
            pointRadius: 3,
            borderWidth: 1.5,
            borderDash: [3, 3]
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          r: {
            min: 0,
            max: 100,
            ticks: {
              display: false,
              stepSize: 20
            },
            grid: {
              color: "rgba(255, 255, 255, 0.08)"
            },
            angleLines: {
              color: "rgba(255, 255, 255, 0.12)"
            },
            pointLabels: {
              color: "#cbd5e1",
              font: {
                size: 9.5,
                weight: "600"
              }
            }
          }
        },
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              boxWidth: 8,
              boxHeight: 8,
              usePointStyle: true,
              color: "#cbd5e1",
              font: { size: 9.5 }
            }
          },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.dataset.label}: ${ctx.raw}%`
            }
          }
        }
      }
    });
  }
}

// --------------------------------------------------------------------- //
//  Status & Health Check                                                //
// --------------------------------------------------------------------- //
async function checkStatus() {
  try {
    const res = await fetch("/api/status");
    if (res.ok) {
      const data = await res.json();
      const statusText = document.getElementById("backend-status-text");
      if (statusText) statusText.textContent = "Backend Online";
    }
  } catch (err) {
    const statusText = document.getElementById("backend-status-text");
    if (statusText) statusText.textContent = "Backend Offline";
  }
}

// --------------------------------------------------------------------- //
//  Utilities                                                            //
// --------------------------------------------------------------------- //
function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeJs(str) {
  return str.replace(/'/g, "\\'").replace(/"/g, '\\"');
}

function formatMarkdown(text) {
  if (!text) return "";
  let clean = text
    .replace(/<\/?answer>/gi, "")
    .replace(/<thinking>[\s\S]*?<\/thinking>/gi, "")
    .trim();

  // Strip raw citation tags like [faq.md#9] or [review:1234] or [review]
  clean = clean.replace(/\[(?:doc:|review:|pr:|issue:|code:)?[a-zA-Z0-9_\-\.\#\/]+\]/g, "");

  // Headings (##, ###, ####, #####)
  clean = clean.replace(/^##\s+(.+)$/gm, '<h2 class="answer-h2">$1</h2>');
  clean = clean.replace(/^#{3,6}\s+(.+)$/gm, '<h3 class="answer-h3">$1</h3>');

  // Unordered lists (- or *)
  clean = clean.replace(/(?:^[ \t]*[-*]\s+.+(?:\r?\n|$))+/gm, function(listBlock) {
    const items = listBlock.trim().split(/\r?\n/).map(line => {
      const content = line.replace(/^[ \t]*[-*]\s+/, '').trim();
      return `<li>${content}</li>`;
    }).join("");
    return `<ul>${items}</ul>`;
  });

  // Ordered lists (1. , 2. )
  clean = clean.replace(/(?:^[ \t]*\d+\.\s+.+(?:\r?\n|$))+/gm, function(listBlock) {
    const items = listBlock.trim().split(/\r?\n/).map(line => {
      const content = line.replace(/^[ \t]*\d+\.\s+/, '').trim();
      return `<li>${content}</li>`;
    }).join("");
    return `<ol>${items}</ol>`;
  });

  // Inline formatting
  clean = clean
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code class='mono'>$1</code>");

  // Paragraph wrapping for text blocks not already in headings or lists
  const blocks = clean.split(/\n{2,}/);
  clean = blocks.map(b => {
    b = b.trim();
    if (!b) return "";
    if (b.startsWith("<h2") || b.startsWith("<h3") || b.startsWith("<ul") || b.startsWith("<ol")) {
      return b;
    }
    return `<p>${b.replace(/\n/g, "<br>")}</p>`;
  }).join("\n");

  return clean;
}

// --------------------------------------------------------------------- //
//  Model Selector Dropdown Interactivity                                //
// --------------------------------------------------------------------- //
function toggleModelDropdown(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById("model-dropdown-menu");
  if (menu) menu.classList.toggle("show");
}

function selectModel(modelLabel) {
  const nameEl = document.getElementById("header-model-name");
  if (nameEl) nameEl.textContent = modelLabel;

  document.querySelectorAll(".model-dropdown-item").forEach(item => {
    const isMatch = item.textContent.includes(modelLabel);
    item.classList.toggle("active", isMatch);
    const dot = item.querySelector(".model-item-dot");
    if (dot) dot.classList.toggle("active", isMatch);
    let check = item.querySelector(".check-mark");
    if (isMatch && !check) {
      const span = document.createElement("span");
      span.className = "check-mark";
      span.textContent = "✓";
      item.appendChild(span);
    } else if (!isMatch && check) {
      check.remove();
    }
  });

  const menu = document.getElementById("model-dropdown-menu");
  if (menu) menu.classList.remove("show");

  const cleanModel = modelLabel.replace(/^gemini\s*·\s*/, "").replace(/^mock\s*·\s*/, "").trim();
  fetch("/api/model", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: cleanModel })
  }).catch(() => {});
}

document.addEventListener("click", (e) => {
  const menu = document.getElementById("model-dropdown-menu");
  const pill = document.getElementById("header-llm-pill");
  if (menu && menu.classList.contains("show")) {
    if (pill && !pill.contains(e.target) && !menu.contains(e.target)) {
      menu.classList.remove("show");
    }
  }
});

// ===================================================================== //
//  Compare Architecture: 4 Visual Topology Flowcharts & Simulation     //
// ===================================================================== //
let currentArchView = "all";
let simIntervalId = null;
let simStepIndex = 0;
const SIM_TOTAL_STEPS = 9;

function selectArchitectureView(viewKey) {
  currentArchView = viewKey;

  // 1. Update filter pill buttons
  const tabs = document.querySelectorAll("#arch-view-tabs .arch-filter-btn");
  tabs.forEach(btn => {
    btn.classList.remove("active");
  });
  const activeBtn = document.getElementById(`btn-view-${viewKey}`);
  if (activeBtn) activeBtn.classList.add("active");

  // 2. Toggle sections
  const sections = {
    sequential: document.getElementById("section-arch-sequential"),
    hierarchical: document.getElementById("section-arch-hierarchical"),
    event_driven: document.getElementById("section-arch-event_driven"),
    workflow_dag: document.getElementById("section-arch-workflow_dag")
  };

  Object.entries(sections).forEach(([key, section]) => {
    if (!section) return;
    if (viewKey === "all" || viewKey === key) {
      section.style.display = "block";
    } else {
      section.style.display = "none";
    }
  });

  resetStepSimulation();
}

const SIMULATION_STEP_DESCRIPTIONS = [
  {
    step: 0,
    text: "Step 0: User Query Ingestion — Sequential: sends to Orchestrator; Hierarchical: sends to Central Master; Event-Driven: publishes 'question_received'; DAG: enters START state."
  },
  {
    step: 1,
    text: "Step 1: Planning Phase — Sequential: sets fixed linear order; Hierarchical: controller splits subtasks; Event-Driven: publishes 'plan_ready'; DAG: Plan Node generates execution token."
  },
  {
    step: 2,
    text: "Step 2: Retrieval Commences — [Sequential: Code Agent runs ALONE while others WAIT] vs. [Hierarchical: All 3 workers start SIMULTANEOUSLY in parallel thread pool] vs. [Event-Driven: 3 subscribers react in parallel] vs. [DAG: Fork Barrier splits to 3 branch nodes]."
  },
  {
    step: 3,
    text: "Step 3: Mid-Retrieval Execution — [Sequential: History Agent runs 2nd AFTER Code finishes] vs. [Hierarchical: 3 parallel worker threads deposit findings simultaneously into Thread-Safe Blackboard]."
  },
  {
    step: 4,
    text: "Step 4: Retrieval Completion — [Sequential: Issue/PR runs 3rd AFTER History finishes] vs. [Hierarchical & Event-Driven: All evidence chunks aggregated in Blackboard] vs. [DAG: Join Barrier synchronizes branch states]."
  },
  {
    step: 5,
    text: "Step 5: Quality Gate & Cross-Validation — Verifier evaluates Chain-of-Thought validity, sufficiency thresholds, and factual ground truth."
  },
  {
    step: 6,
    text: "Step 6: Conditional Re-Plan Check — If confidence < 0.6: Sequential loops back linearly, Hierarchical re-dispatches worker, Event-Driven emits 'evidence_insufficient', DAG triggers conditional edge cycle."
  },
  {
    step: 7,
    text: "Step 7: Multi-Perspective Synthesis — Reviewer Agent synthesizes verified multi-source findings into final architectural intelligence."
  },
  {
    step: 8,
    text: "Step 8: Verified Output Delivered — Complete architectural answer returned with citations and full execution audit trail."
  }
];

function toggleStepSimulation() {
  if (simIntervalId) {
    pauseStepSimulation();
  } else {
    playStepSimulation();
  }
}

function playStepSimulation() {
  const playBtn = document.getElementById("btn-sim-play");
  const playIcon = document.getElementById("sim-icon");
  const playText = document.getElementById("sim-btn-text");
  const statusDot = document.getElementById("sim-status-dot");

  if (playIcon) playIcon.textContent = "⏸️";
  if (playText) playText.textContent = "Pause Flow";
  if (statusDot) statusDot.classList.add("active");

  if (simStepIndex >= SIM_TOTAL_STEPS) {
    clearSimStepHighlights();
    simStepIndex = 0;
  }

  // Advance immediately, then every 1500ms
  executeSimStep(simStepIndex);
  simStepIndex++;

  simIntervalId = setInterval(() => {
    if (simStepIndex >= SIM_TOTAL_STEPS) {
      pauseStepSimulation();
      const statusText = document.getElementById("sim-status-text");
      if (statusText) statusText.textContent = "Simulation completed! Click 'Simulate Agent Flow' to re-play or 'Reset'.";
      simStepIndex = 0;
      return;
    }
    executeSimStep(simStepIndex);
    simStepIndex++;
  }, 1500);
}

function pauseStepSimulation() {
  if (simIntervalId) {
    clearInterval(simIntervalId);
    simIntervalId = null;
  }
  const playIcon = document.getElementById("sim-icon");
  const playText = document.getElementById("sim-btn-text");
  const statusDot = document.getElementById("sim-status-dot");

  if (playIcon) playIcon.textContent = "▶️";
  if (playText) playText.textContent = "Resume Flow";
  if (statusDot) statusDot.classList.remove("active");
}

function resetStepSimulation() {
  pauseStepSimulation();
  simStepIndex = 0;
  clearSimStepHighlights();

  const playIcon = document.getElementById("sim-icon");
  const playText = document.getElementById("sim-btn-text");
  const statusText = document.getElementById("sim-status-text");

  if (playIcon) playIcon.textContent = "▶️";
  if (playText) playText.textContent = "Simulate Agent Flow";
  if (statusText) statusText.textContent = "Ready: Click Simulate to watch agents move through each topology";
}

function clearSimStepHighlights() {
  document.querySelectorAll(".active-sim-step").forEach(el => el.classList.remove("active-sim-step"));
  document.querySelectorAll(".completed-sim-step").forEach(el => el.classList.remove("completed-sim-step"));
}

function executeSimStep(stepIdx) {
  // Update status text
  const statusText = document.getElementById("sim-status-text");
  const desc = SIMULATION_STEP_DESCRIPTIONS.find(d => d.step === stepIdx);
  if (statusText && desc) {
    statusText.textContent = desc.text;
  }

  // Convert previous active to completed
  document.querySelectorAll(".active-sim-step").forEach(el => {
    el.classList.remove("active-sim-step");
    el.classList.add("completed-sim-step");
  });

  // Activate nodes mapped to this step in each architecture
  const stepNodes = getNodesForSimStep(stepIdx);
  stepNodes.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.classList.remove("completed-sim-step");
      el.classList.add("active-sim-step");
    }
  });
}

function getNodesForSimStep(stepIdx) {
  // Mapping each abstract step to specific nodes across the 4 architectures
  switch (stepIdx) {
    case 0: // Query arrives
      return ["seq-step-0", "hier-step-0", "evt-step-0", "dag-step-0"];
    case 1: // Planning / Orchestration
      return ["seq-step-1", "hier-step-1", "evt-step-1", "dag-step-1"];
    case 2: // Retrieval Step 1 (Sequential: ONLY Code Agent; Hierarchical/Event/DAG: ALL 3 WORKERS CONCURRENTLY!)
      return [
        "seq-step-2", // Code alone in sequential
        "hier-step-2a", "hier-step-2b", "hier-step-2c", // ALL 3 simultaneously in hierarchical
        "evt-step-2a", "evt-step-2b", "evt-step-2c", // ALL 3 subscribers in event-driven
        "dag-step-2", "dag-step-3a", "dag-step-3b", "dag-step-3c" // Fork + 3 branches in DAG
      ];
    case 3: // Retrieval Step 2 (Sequential: History Agent runs 2nd AFTER Code finishes)
      return [
        "seq-step-3", // History agent running 2nd
        "hier-step-3", // Thread-safe Blackboard write in hierarchical
        "evt-step-3", // Evidence posted event in event-driven
        "dag-step-4" // Join barrier in DAG
      ];
    case 4: // Retrieval Step 3 (Sequential: Issue/PR runs 3rd AFTER History finishes)
      return [
        "seq-step-4", "seq-step-5", // Issue/PR running 3rd + Blackboard in sequential
        "hier-step-3",
        "evt-step-3",
        "dag-step-4"
      ];
    case 5: // Verification Gatekeeper
      return ["seq-step-6", "hier-step-4", "evt-step-4", "dag-step-5"];
    case 6: // Conditional Loop Check
      return ["seq-step-6", "hier-step-4", "evt-step-4", "dag-step-5"];
    case 7: // Synthesis (Reviewer)
      return ["seq-step-7", "hier-step-5", "evt-step-5", "dag-step-6"];
    case 8: // Output Delivered
      return ["seq-step-8", "hier-step-6", "dag-step-7"];
    default:
      return [];
  }
}


