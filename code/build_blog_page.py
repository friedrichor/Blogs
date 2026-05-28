#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NAME = "Webwright"
DEFAULT_SOURCE = ROOT / "notes" / f"{DEFAULT_NAME}.md"
DEFAULT_PROJECT_ROOT = ROOT / "external_projects" / DEFAULT_NAME
DEFAULT_OUT = ROOT / "pages" / DEFAULT_NAME

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
CODE_REF_RE = re.compile(r"#L(\d+)(?:-L?(\d+))?")
CODE_EXTENSIONS = (".py", ".yaml", ".yml", ".json", ".md", ".sh", ".toml")


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[`*_~\[\]().:：\"'“”/\\]", "", text).strip().lower()
    text = re.sub(r"\s+", "-", text)
    return text or hashlib.sha1(text.encode()).hexdigest()[:8]


def split_link_target(target: str) -> tuple[str, int | None, int | None]:
    path = target
    start = end = None
    if "#" in target:
        path, fragment = target.split("#", 1)
        match = CODE_REF_RE.search("#" + fragment)
        if match:
            start = int(match.group(1))
            end = int(match.group(2) or start)
    return path, start, end


def is_code_path(path_text: str) -> bool:
    return not path_text.startswith(("http://", "https://", "#")) and path_text.endswith(CODE_EXTENSIONS)


def build_file_id(path_text: str) -> str:
    digest = hashlib.sha1(path_text.encode("utf-8")).hexdigest()[:12]
    return f"file-{digest}"


def language_for_path(path_text: str) -> str:
    suffix = Path(path_text).suffix.lower()
    return {
        ".py": "python",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".md": "markdown",
        ".sh": "bash",
        ".toml": "toml",
    }.get(suffix, "text")


def find_last_code_ref(lines: list[str], before: int) -> tuple[str, int | None, int | None, str] | None:
    for index in range(before - 1, max(-1, before - 8), -1):
        for match in reversed(list(LINK_RE.finditer(lines[index]))):
            label, target = match.group(1), match.group(2)
            path, start, end = split_link_target(target)
            if is_code_path(path):
                return path, start, end, label
    return None


def details_end(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        if lines[index].strip() == "</details>":
            return index
    return None


def details_language(lines: list[str], start: int, end: int) -> str:
    for line in lines[start : end + 1]:
        stripped = line.strip()
        if stripped.startswith("```"):
            return stripped[3:].strip() or "text"
    return "text"


def read_snippet(
    project_root: Path,
    path_text: str,
    start: int | None,
    end: int | None,
) -> tuple[list[dict], str | None]:
    source_path = (project_root / path_text).resolve()
    try:
        source_path.relative_to(project_root)
    except ValueError:
        return [], "Refused to read a file outside the external project."
    if not source_path.exists():
        return [], f"File not found: {path_text}"

    raw_lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if start is None:
        start = 1
        end = len(raw_lines)
    if end is None:
        end = start
    start = max(1, start)
    end = min(max(start, end), len(raw_lines))
    selected = raw_lines[start - 1 : end]
    return [{"number": start + offset, "text": text} for offset, text in enumerate(selected)], None


def read_full_file(project_root: Path, path_text: str) -> tuple[list[dict], str | None]:
    source_path = (project_root / path_text).resolve()
    try:
        source_path.relative_to(project_root)
    except ValueError:
        return [], "Refused to read a file outside the external project."
    if not source_path.exists():
        return [], f"File not found: {path_text}"
    raw_lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [{"number": index + 1, "text": text} for index, text in enumerate(raw_lines)], None


def write_file_payloads(markdown: str, project_root: Path, files_dir: Path) -> dict[str, dict]:
    code_paths: dict[str, str] = {}
    for match in LINK_RE.finditer(markdown):
        path_text, _, _ = split_link_target(match.group(2))
        if is_code_path(path_text):
            code_paths[path_text] = build_file_id(path_text)

    files_dir.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, dict] = {}
    for path_text, file_id in code_paths.items():
        lines, error = read_full_file(project_root, path_text)
        payload = {
            "id": file_id,
            "path": path_text,
            "language": language_for_path(path_text),
            "lines": lines,
            "error": error,
        }
        payloads[file_id] = payload
        (files_dir / f"{file_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return payloads


def build_snippet_id(path_text: str, start: int | None, end: int | None) -> str:
    key = f"{path_text}:{start or ''}:{end or ''}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"snippet-{digest}"


def preprocess_markdown(markdown: str, project_root: Path, snippets_dir: Path) -> tuple[str, dict[str, dict]]:
    lines = markdown.splitlines()
    output: list[str] = []
    snippet_payloads: dict[str, dict] = {}
    index = 0
    while index < len(lines):
        if lines[index].strip() == "<details>":
            end = details_end(lines, index)
            ref = find_last_code_ref(lines, len(output))
            if end is not None and ref is not None:
                path_text, start, finish, label = ref
                lang = details_language(lines, index, end)
                snippet_id = build_snippet_id(path_text, start, finish)
                snippet_lines, error = read_snippet(project_root, path_text, start, finish)
                title_range = f":{start}" if start and start == finish else f":{start}-{finish}" if start else ""
                snippet_payload = {
                    "id": snippet_id,
                    "title": f"{path_text}{title_range}",
                    "path": path_text,
                    "start": start,
                    "end": finish,
                    "language": lang,
                    "label": label,
                    "lines": snippet_lines,
                    "error": error,
                }
                snippet_payloads[snippet_id] = snippet_payload
                (snippets_dir / f"{snippet_id}.json").write_text(
                    json.dumps(snippet_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                output.extend(
                    [
                        f'<details class="code-snippet" data-snippet="{snippet_id}">',
                        f"<summary>展开查看原始代码 <code>{html.escape(path_text + title_range)}</code></summary>",
                        '<div class="snippet-toolbar">',
                        (
                            f'<button type="button" class="open-file-button" data-code-path="{html.escape(path_text, quote=True)}" '
                            f'data-file-id="{build_file_id(path_text)}" data-start="{start or ""}" data-end="{finish or ""}">在侧栏打开完整文件</button>'
                        ),
                        "</div>",
                        '<div class="snippet-body" data-state="idle">点击展开后加载代码片段。</div>',
                        "</details>",
                    ]
                )
                index = end + 1
                continue
        output.append(lines[index])
        index += 1
    return "\n".join(output), snippet_payloads


def render_inline(text: str) -> str:
    placeholders: list[str] = []

    def stash(value: str) -> str:
        placeholders.append(value)
        return f"\x00{len(placeholders) - 1}\x00"

    def link_repl(match: re.Match) -> str:
        label = render_inline(match.group(1))
        raw_target = match.group(2)
        path_text, start, end = split_link_target(raw_target)
        target = html.escape(raw_target, quote=True)
        if raw_target.startswith(("http://", "https://")):
            return stash(f'<a href="{target}" target="_blank" rel="noopener noreferrer">{label}</a>')
        if is_code_path(path_text):
            data_path = html.escape(path_text, quote=True)
            data_file = build_file_id(path_text)
            data_start = "" if start is None else str(start)
            data_end = "" if end is None else str(end)
            return stash(
                f'<a href="{target}" class="code-ref-trigger" data-code-path="{data_path}" '
                f'data-file-id="{data_file}" data-start="{data_start}" data-end="{data_end}">{label}</a>'
            )
        return stash(f'<a href="{target}">{label}</a>')

    text = LINK_RE.sub(link_repl, text)
    text = re.sub(r"`([^`]+)`", lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), text)
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    for idx, value in enumerate(placeholders):
        escaped = escaped.replace(f"\x00{idx}\x00", value)
    return escaped


def render_markdown(markdown: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    html_blocks: list[str] = []
    toc: list[tuple[int, str, str]] = []
    paragraph: list[str] = []
    list_stack: list[dict] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    quote_lines: list[str] = []
    in_raw = False
    raw_lines: list[str] = []
    table_lines: list[str] = []
    skipping_source_toc = False
    source_toc_level = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            html_blocks.append(f"<p>{render_inline(' '.join(paragraph))}</p>")
            paragraph = []

    def flush_list() -> None:
        while list_stack:
            state = list_stack.pop()
            if state["li_open"]:
                html_blocks.append("</li>")
            html_blocks.append(f"</{state['tag']}>")

    def add_list_item(tag: str, indent: int, text: str) -> None:
        while list_stack and indent < list_stack[-1]["indent"]:
            state = list_stack.pop()
            if state["li_open"]:
                html_blocks.append("</li>")
            html_blocks.append(f"</{state['tag']}>")

        if list_stack and indent == list_stack[-1]["indent"] and tag != list_stack[-1]["tag"]:
            state = list_stack.pop()
            if state["li_open"]:
                html_blocks.append("</li>")
            html_blocks.append(f"</{state['tag']}>")

        if not list_stack or indent > list_stack[-1]["indent"]:
            html_blocks.append(f"<{tag}>")
            list_stack.append({"tag": tag, "indent": indent, "li_open": False})

        if list_stack and indent == list_stack[-1]["indent"] and list_stack[-1]["li_open"]:
            html_blocks.append("</li>")

        html_blocks.append(f"<li>{render_inline(text)}")
        list_stack[-1]["li_open"] = True

    def flush_code() -> None:
        nonlocal code_lines, code_lang
        if not code_lines and not code_lang:
            return
        lang_class = f" language-{html.escape(code_lang)}" if code_lang else ""
        code = html.escape("\n".join(code_lines))
        html_blocks.append(f'<pre><code class="{lang_class.strip()}">{code}</code></pre>')
        code_lines = []
        code_lang = ""

    def flush_quote() -> None:
        nonlocal quote_lines
        if not quote_lines:
            return
        rendered_parts: list[str] = []
        paragraph_parts: list[str] = []
        quote_list_items: list[str] = []
        quote_list_type: str | None = None

        def flush_quote_paragraph() -> None:
            nonlocal paragraph_parts
            if paragraph_parts:
                rendered_parts.append(f"<p>{render_inline(' '.join(paragraph_parts))}</p>")
                paragraph_parts = []

        def flush_quote_list() -> None:
            nonlocal quote_list_items, quote_list_type
            if quote_list_items and quote_list_type:
                rendered_parts.append(
                    f"<{quote_list_type}>" + "".join(quote_list_items) + f"</{quote_list_type}>"
                )
                quote_list_items = []
                quote_list_type = None

        for quote_line in quote_lines:
            if quote_line == "":
                flush_quote_paragraph()
                flush_quote_list()
                continue
            ordered = re.match(r"^\d+\.\s+(.+)$", quote_line)
            unordered = re.match(r"^[-*]\s+(.+)$", quote_line)
            if ordered or unordered:
                flush_quote_paragraph()
                current_type = "ol" if ordered else "ul"
                if quote_list_type and quote_list_type != current_type:
                    flush_quote_list()
                quote_list_type = current_type
                quote_list_items.append(f"<li>{render_inline((ordered or unordered).group(1))}</li>")
                continue
            flush_quote_list()
            paragraph_parts.append(quote_line)
        flush_quote_paragraph()
        flush_quote_list()
        rendered = "".join(rendered_parts)
        if rendered:
            html_blocks.append(f"<blockquote>{rendered}</blockquote>")
        quote_lines = []

    def flush_table() -> None:
        nonlocal table_lines
        if not table_lines:
            return
        rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in table_lines]
        if len(rows) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
            head = "".join(f"<th>{render_inline(cell)}</th>" for cell in rows[0])
            body_rows = []
            for row in rows[2:]:
                body_rows.append("<tr>" + "".join(f"<td>{render_inline(cell)}</td>" for cell in row) + "</tr>")
            html_blocks.append(f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>")
        else:
            for line in table_lines:
                html_blocks.append(f"<p>{render_inline(line)}</p>")
        table_lines = []

    for line in lines:
        stripped = line.strip()
        heading_for_skip = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if skipping_source_toc:
            if heading_for_skip and len(heading_for_skip.group(1)) <= source_toc_level:
                skipping_source_toc = False
            else:
                continue
        if (
            heading_for_skip
            and heading_for_skip.group(2).strip() == "目录"
            and not in_code
            and not in_raw
        ):
            flush_paragraph()
            flush_list()
            flush_code()
            flush_quote()
            flush_table()
            skipping_source_toc = True
            source_toc_level = len(heading_for_skip.group(1))
            continue
        if in_code:
            if stripped.startswith("```"):
                flush_code()
                in_code = False
            else:
                code_lines.append(line)
            continue
        if in_raw:
            raw_lines.append(line)
            if stripped == "</details>":
                html_blocks.append("\n".join(raw_lines))
                raw_lines = []
                in_raw = False
            continue
        if stripped.startswith("```"):
            flush_quote()
            flush_paragraph()
            flush_list()
            flush_table()
            in_code = True
            code_lang = stripped[3:].strip()
            continue
        if stripped.startswith("<details"):
            flush_quote()
            flush_paragraph()
            flush_list()
            flush_table()
            in_raw = True
            raw_lines = [line]
            continue
        if not stripped:
            flush_quote()
            flush_paragraph()
            flush_list()
            flush_table()
            continue
        if stripped == "---":
            flush_quote()
            flush_paragraph()
            flush_list()
            flush_table()
            html_blocks.append("<hr>")
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_quote()
            flush_paragraph()
            flush_list()
            table_lines.append(line)
            continue
        flush_table()
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_quote()
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            anchor = slugify(title)
            if level <= 3:
                toc.append((level, anchor, re.sub(r"`", "", title)))
            html_blocks.append(f'<h{level} id="{anchor}">{render_inline(title)}</h{level}>')
            continue
        quote = re.match(r"^>\s?(.*)$", stripped)
        if quote:
            flush_paragraph()
            flush_list()
            flush_table()
            quote_lines.append(quote.group(1).strip())
            continue
        ordered = re.match(r"^(\s*)\d+\.\s+(.+)$", line)
        unordered = re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if ordered or unordered:
            flush_quote()
            flush_paragraph()
            current_type = "ol" if ordered else "ul"
            match = ordered or unordered
            indent = len(match.group(1).replace("\t", "    "))
            item_text = match.group(2)
            add_list_item(current_type, indent, item_text)
            continue
        flush_list()
        flush_quote()
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    flush_code()
    flush_quote()
    flush_table()
    toc_html = "".join(
        f'<a class="toc-level-{level}" href="#{anchor}">{html.escape(title)}</a>' for level, anchor, title in toc
    )
    return "\n".join(html_blocks), toc_html


def write_assets(
    out_dir: Path,
    title: str,
    body: str,
    toc: str,
    file_payloads: dict[str, dict],
    snippet_payloads: dict[str, dict],
) -> None:
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <div class="app-shell" id="app-shell">
    <aside class="toc" id="toc" aria-label="目录">
      <div class="toc-header">
        <div class="toc-title">目录</div>
        <button type="button" class="toc-toggle" id="toc-toggle" aria-label="折叠目录">‹</button>
      </div>
      <nav>{toc}</nav>
    </aside>
    <button type="button" class="toc-rail" id="toc-rail" aria-label="展开目录">目录</button>
    <main class="document">
      {body}
    </main>
    <div class="code-resizer" id="code-resizer" aria-hidden="true"></div>
    <aside class="code-viewer" id="code-viewer" aria-hidden="true">
      <div class="code-viewer-header">
        <div>
          <div class="code-viewer-kicker">Code view</div>
          <div class="code-viewer-title" id="code-viewer-title">未选择文件</div>
        </div>
        <button type="button" class="code-viewer-close" id="code-viewer-close" aria-label="关闭代码侧栏">×</button>
      </div>
      <div class="code-viewer-meta" id="code-viewer-meta"></div>
      <div class="code-viewer-body" id="code-viewer-body">点击任意代码路径打开完整文件。</div>
    </aside>
  </div>
  <script src="assets/code-data.js"></script>
  <script src="assets/app.js"></script>
</body>
</html>
""",
        encoding="utf-8",
    )
    (out_dir / "assets" / "style.css").write_text(STYLE, encoding="utf-8")
    code_data = (
        "window.CODE_FILE_DATA = "
        + json.dumps(file_payloads, ensure_ascii=False, separators=(",", ":"))
        + ";\nwindow.CODE_SNIPPET_DATA = "
        + json.dumps(snippet_payloads, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    )
    (out_dir / "assets" / "code-data.js").write_text(code_data, encoding="utf-8")
    (out_dir / "assets" / "app.js").write_text(APP_JS, encoding="utf-8")


STYLE = r"""
:root {
  color-scheme: light;
  --bg: #f7f8fb;
  --paper: #ffffff;
  --text: #1f2937;
  --muted: #6b7280;
  --line: #d8dee9;
  --accent: #0f766e;
  --code-bg: #f7f7f4;
  --code-text: #242424;
  --code-line: #e7e4dc;
  --code-muted: #8a8176;
  --code-highlight: #fff4c2;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 16px/1.68 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  overflow: hidden;
}

button {
  font: inherit;
}

.app-shell {
  --toc-width: 280px;
  --code-width: 0px;
  display: grid;
  grid-template-columns: var(--toc-width) minmax(0, 1fr) 0 0;
  height: 100vh;
  min-width: 0;
  transition: grid-template-columns .16s ease;
}

body.toc-collapsed .app-shell {
  --toc-width: 0px;
}

body.code-viewer-open .app-shell {
  --code-width: min(720px, 46vw);
  grid-template-columns: var(--toc-width) minmax(360px, 1fr) 6px var(--code-width);
}

body.code-viewer-open.toc-collapsed .app-shell {
  grid-template-columns: 0 minmax(360px, 1fr) 6px var(--code-width);
}

.document {
  min-width: 0;
  height: 100vh;
  overflow: auto;
  padding: 48px max(28px, calc((100% - 920px) / 2)) 80px;
}

.toc {
  min-width: 0;
  height: 100vh;
  padding: 28px 22px;
  border-right: 1px solid var(--line);
  background: rgba(255,255,255,.82);
  overflow: auto;
  transition: opacity .12s ease, visibility .12s ease;
}

body.toc-collapsed .toc {
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  padding-left: 0;
  padding-right: 0;
}

.toc-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.toc-title {
  margin-bottom: 12px;
  font-weight: 700;
}

.toc-toggle,
.toc-rail {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #ffffff;
  color: var(--muted);
  cursor: pointer;
}

.toc-toggle {
  width: 28px;
  height: 28px;
  margin-bottom: 12px;
  line-height: 1;
}

.toc-rail {
  position: fixed;
  left: 10px;
  top: 12px;
  z-index: 12;
  display: none;
  padding: 5px 8px;
  font-size: 13px;
  box-shadow: 0 6px 18px rgba(31, 41, 55, .12);
}

body.toc-collapsed .toc-rail {
  display: block;
}

.toc a {
  display: block;
  color: var(--muted);
  text-decoration: none;
  padding: 4px 0;
  font-size: 14px;
}

.toc a:hover { color: var(--accent); }
.toc-level-2 { padding-left: 0; }
.toc-level-3 { padding-left: 14px !important; }

.document ul,
.document ol {
  padding-left: 1.45rem;
}

.document li > ul,
.document li > ol {
  margin-top: 2px;
  margin-bottom: 4px;
}

h1, h2, h3, h4 {
  line-height: 1.25;
  letter-spacing: 0;
  color: #111827;
}
h1 { font-size: 34px; margin: 0 0 24px; }
h2 { font-size: 26px; margin-top: 44px; padding-top: 10px; border-top: 1px solid var(--line); }
h3 { font-size: 20px; margin-top: 28px; }

a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 3px; }
a.code-ref-trigger {
  font-weight: 650;
  cursor: pointer;
}
a.code-ref-trigger:hover {
  color: #115e59;
}
code {
  border: 1px solid #d6dbe5;
  border-radius: 5px;
  background: #eef2f7;
  padding: 1px 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .9em;
}

blockquote {
  margin: 14px 0;
  padding: 8px 16px;
  border-left: 3px solid var(--accent);
  background: #eef7f5;
  color: #334155;
}

hr { border: 0; border-top: 1px solid var(--line); margin: 30px 0; }

table {
  width: 100%;
  border-collapse: collapse;
  margin: 18px 0;
  background: var(--paper);
  border: 1px solid var(--line);
}
th, td {
  border: 1px solid var(--line);
  padding: 8px 10px;
  vertical-align: top;
}
th { background: #f1f5f9; text-align: left; }

.code-snippet {
  margin: 12px 0 22px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: var(--paper);
  overflow: hidden;
}
.code-snippet summary {
  cursor: pointer;
  padding: 10px 14px;
  font-weight: 650;
  background: #f8fafc;
}
.code-snippet summary code { font-weight: 500; }
.snippet-toolbar {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 8px 12px;
  border-top: 1px solid var(--line);
  background: #fbfdff;
  font-size: 13px;
}
.open-file-button {
  border: 1px solid #b6c2d2;
  border-radius: 6px;
  background: #ffffff;
  color: #0f766e;
  cursor: pointer;
  padding: 3px 8px;
}
.open-file-button:hover {
  border-color: #0f766e;
}
.snippet-body {
  min-height: 44px;
  padding: 12px;
  color: var(--muted);
  border-top: 1px solid var(--line);
}
.code-table {
  width: 100%;
  overflow: auto;
  background: var(--code-bg);
  color: var(--code-text);
}
.code-row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  min-width: max-content;
  font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.line-no {
  user-select: none;
  text-align: right;
  color: var(--code-muted);
  padding: 0 12px;
  border-right: 1px solid var(--code-line);
  background: #f1f0eb;
}
.line-code {
  white-space: pre;
  padding: 0 14px;
}

.code-viewer {
  display: grid;
  grid-template-rows: auto auto 1fr;
  min-width: 0;
  height: 100vh;
  background: #faf9f5;
  color: #2f2a24;
  border-left: 1px solid #ddd8ce;
  opacity: 0;
  visibility: hidden;
  overflow: hidden;
}

body.code-viewer-open .code-viewer {
  opacity: 1;
  visibility: visible;
}

.code-resizer {
  display: none;
  height: 100vh;
  background: #e4dfd4;
  cursor: col-resize;
}

body.code-viewer-open .code-resizer {
  display: block;
}

body.is-resizing,
body.is-resizing * {
  cursor: col-resize !important;
  user-select: none !important;
}

.code-viewer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
  padding: 14px 16px 12px;
  border-bottom: 1px solid #e4dfd4;
  background: #fffdf8;
}

.code-viewer-kicker {
  color: #8a6a35;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

.code-viewer-title {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font: 14px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.code-viewer-close {
  width: 30px;
  height: 30px;
  border: 1px solid #d8d2c7;
  border-radius: 6px;
  background: #ffffff;
  color: #5f574d;
  cursor: pointer;
  font-size: 20px;
  line-height: 1;
}

.code-viewer-close:hover {
  border-color: #b79a5b;
  background: #faf6ec;
}

.code-viewer-meta {
  min-height: 32px;
  padding: 7px 16px;
  border-bottom: 1px solid #e4dfd4;
  color: #746b60;
  font-size: 13px;
  background: #f8f5ee;
}

.code-viewer-body {
  overflow: auto;
  color: var(--code-text);
  background: var(--code-bg);
}

.code-viewer-body .code-row {
  min-width: max-content;
}

.code-row.is-target {
  background: var(--code-highlight);
}

.code-row.is-target .line-no {
  color: #6c531c;
  background: #f7e8ad;
}

@media (max-width: 1260px) {
  body.code-viewer-open .app-shell {
    --code-width: min(620px, 48vw);
  }
}

@media (max-width: 640px) {
  .app-shell,
  body.code-viewer-open .app-shell,
  body.code-viewer-open.toc-collapsed .app-shell {
    grid-template-columns: 0 1fr 0 0;
  }
  body.code-viewer-open .app-shell {
    grid-template-columns: 0 0 0 1fr;
  }
  .document {
    padding: 32px 16px 64px;
  }
  h1 { font-size: 28px; }
  h2 { font-size: 22px; }
  .code-resizer { display: none !important; }
}
"""


APP_JS = r"""
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
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build BLOG_TO_CODE_MAPPING.md as a static HTML page.")
    parser.add_argument("--name", default=DEFAULT_NAME, help="Page/project name. Defaults to Webwright.")
    parser.add_argument("--source", type=Path, help="Markdown note path. Defaults to notes/<name>.md.")
    parser.add_argument("--project-root", type=Path, help="External project root. Defaults to external_projects/<name>.")
    parser.add_argument("--out", type=Path, help="Output directory. Defaults to pages/<name>.")
    args = parser.parse_args()

    source = (args.source or ROOT / "notes" / f"{args.name}.md").resolve()
    project_root = (args.project_root or ROOT / "external_projects" / args.name).resolve()
    out_dir = (args.out or ROOT / "pages" / args.name).resolve()
    if not source.exists():
        raise SystemExit(f"Note not found: {source}")
    if not project_root.exists():
        raise SystemExit(f"External project not found: {project_root}")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "snippets").mkdir(parents=True)

    markdown = source.read_text(encoding="utf-8")
    file_payloads = write_file_payloads(markdown, project_root, out_dir / "files")
    transformed, snippet_payloads = preprocess_markdown(markdown, project_root, out_dir / "snippets")
    body, toc = render_markdown(transformed)
    title_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = title_match.group(1) if title_match else "Blog to Code Mapping"
    write_assets(out_dir, title, body, toc, file_payloads, snippet_payloads)
    print(f"Built {out_dir}")


if __name__ == "__main__":
    main()
