
const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

const viewer = document.getElementById("code-viewer");
const viewerTitle = document.getElementById("code-viewer-title");
const viewerMeta = document.getElementById("code-viewer-meta");
const viewerBody = document.getElementById("code-viewer-body");
const viewerClose = document.getElementById("code-viewer-close");
const appShell = document.getElementById("app-shell");
const tocToggle = document.getElementById("toc-toggle");
const tocRail = document.getElementById("toc-rail");
const codeResizer = document.getElementById("code-resizer");

const fileCache = new Map();
const embeddedFiles = window.CODE_FILE_DATA || {};
const embeddedSnippets = window.CODE_SNIPPET_DATA || {};
let tocCollapsedBeforeCodeOpen = false;

async function loadJson(kind, id) {
  const embedded = kind === "files" ? embeddedFiles[id] : embeddedSnippets[id];
  if (embedded) return embedded;
  const response = await fetch(`${kind}/${id}.json`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadSnippet(details) {
  const body = details.querySelector(".snippet-body");
  if (!body || body.dataset.state === "loaded" || body.dataset.state === "loading") return;
  body.dataset.state = "loading";
  body.textContent = "正在加载代码片段...";
  try {
    const id = details.dataset.snippet;
    const data = await loadJson("snippets", id);
    if (data.error) {
      body.dataset.state = "error";
      body.textContent = data.error;
      return;
    }
    const rows = data.lines.map((line) => (
      `<div class="code-row"><span class="line-no">${line.number}</span><span class="line-code">${escapeHtml(line.text)}</span></div>`
    )).join("");
    body.innerHTML = `<div class="code-table" role="region" aria-label="${escapeHtml(data.title)}">${rows}</div>`;
    body.dataset.state = "loaded";
  } catch (error) {
    body.dataset.state = "error";
    body.textContent = `加载失败：${error.message}`;
  }
}

function renderCodeRows(lines, start, end) {
  const first = Number(start || 0);
  const last = Number(end || start || 0);
  return lines.map((line) => {
    const inTarget = first > 0 && line.number >= first && line.number <= last;
    return `<div class="code-row${inTarget ? " is-target" : ""}" data-line="${line.number}">
      <span class="line-no">${line.number}</span><span class="line-code">${escapeHtml(line.text)}</span>
    </div>`;
  }).join("");
}

async function fetchFile(fileId) {
  if (fileCache.has(fileId)) return fileCache.get(fileId);
  const data = await loadJson("files", fileId);
  fileCache.set(fileId, data);
  return data;
}

function scrollToTargetLine(start) {
  if (!start) return;
  requestAnimationFrame(() => {
    const row = viewerBody.querySelector(`[data-line="${start}"]`);
    if (row) row.scrollIntoView({ block: "center" });
  });
}

async function openCodeViewer(trigger) {
  const path = trigger.dataset.codePath;
  const fileId = trigger.dataset.fileId;
  const start = trigger.dataset.start ? Number(trigger.dataset.start) : null;
  const end = trigger.dataset.end ? Number(trigger.dataset.end) : start;
  if (!document.body.classList.contains("code-viewer-open")) {
    tocCollapsedBeforeCodeOpen = document.body.classList.contains("toc-collapsed");
  }
  document.body.classList.add("code-viewer-open");
  document.body.classList.add("toc-collapsed");
  viewer.setAttribute("aria-hidden", "false");
  viewerTitle.textContent = path;
  viewerMeta.textContent = start ? `定位到第 ${start}${end && end !== start ? `-${end}` : ""} 行` : "完整文件";
  viewerBody.textContent = "正在加载完整文件...";

  try {
    const data = await fetchFile(fileId);
    if (data.error) {
      viewerBody.textContent = data.error;
      return;
    }
    viewerBody.innerHTML = `<div class="code-table" role="region" aria-label="${escapeHtml(path)}">${renderCodeRows(data.lines, start, end)}</div>`;
    scrollToTargetLine(start);
  } catch (error) {
    viewerBody.textContent = `加载失败：${error.message}`;
  }
}

function closeCodeViewer() {
  document.body.classList.remove("code-viewer-open");
  document.body.classList.toggle("toc-collapsed", tocCollapsedBeforeCodeOpen);
  viewer.setAttribute("aria-hidden", "true");
}

function setTocCollapsed(collapsed) {
  document.body.classList.toggle("toc-collapsed", collapsed);
  tocToggle.setAttribute("aria-label", collapsed ? "展开目录" : "折叠目录");
}

function setCodeWidthFromClientX(clientX) {
  const viewportWidth = window.innerWidth;
  const minWidth = Math.min(360, viewportWidth);
  const maxWidth = Math.max(minWidth, Math.floor(viewportWidth * 0.72));
  const nextWidth = Math.min(maxWidth, Math.max(minWidth, viewportWidth - clientX));
  appShell.style.setProperty("--code-width", `${nextWidth}px`);
}

document.querySelectorAll("details.code-snippet").forEach((details) => {
  details.addEventListener("toggle", () => {
    if (details.open) loadSnippet(details);
  });
});

document.querySelectorAll("[data-code-path]").forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    openCodeViewer(trigger);
  });
});

viewerClose.addEventListener("click", closeCodeViewer);
tocToggle.addEventListener("click", () => setTocCollapsed(true));
tocRail.addEventListener("click", () => setTocCollapsed(false));

codeResizer.addEventListener("pointerdown", (event) => {
  if (!document.body.classList.contains("code-viewer-open")) return;
  event.preventDefault();
  codeResizer.setPointerCapture(event.pointerId);
  document.body.classList.add("is-resizing");
});

codeResizer.addEventListener("pointermove", (event) => {
  if (!document.body.classList.contains("is-resizing")) return;
  setCodeWidthFromClientX(event.clientX);
});

codeResizer.addEventListener("pointerup", (event) => {
  if (codeResizer.hasPointerCapture(event.pointerId)) {
    codeResizer.releasePointerCapture(event.pointerId);
  }
  document.body.classList.remove("is-resizing");
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeCodeViewer();
});
