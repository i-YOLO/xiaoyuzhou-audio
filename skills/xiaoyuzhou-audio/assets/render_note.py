#!/usr/bin/env python3
"""Render one Markdown note as a portable sibling HTML reading page."""

from __future__ import annotations

import argparse
import hashlib
import html
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import os

from markdown_it import MarkdownIt


def source_information(source: str) -> dict[str, str]:
    for line in source.splitlines():
        parts = line.rsplit(" · ", 3)
        if len(parts) != 4:
            continue
        link = re.fullmatch(r"\[原文\]\((https?://[^)]+)\)", parts[3])
        if link:
            return {"line": line, "channel": parts[0], "date": parts[1],
                    "duration": parts[2], "url": link[1]}
    return {}


def plain_text(value: str) -> str:
    value = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_`]", "", value)
    return value.strip()


def slugify(value: str, used: dict[str, int]) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value.lower()).strip("-") or "section"
    count = used.get(slug, 0)
    used[slug] = count + 1
    return slug if count == 0 else f"{slug}-{count + 1}"


def prepare_markdown(source: str) -> tuple[str, str]:
    title_match = re.search(r"^#\s+(.+)$", source, re.MULTILINE)
    title = plain_text(title_match.group(1)) if title_match else "未命名笔记"
    body = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    if title_match:
        body = re.sub(r"^#\s+.+$", "", body, count=1, flags=re.MULTILINE)
    info = source_information(source)
    if info:
        body = body.replace(info["line"], "", 1)
    return title, body.strip()


def rewrite_internal_href(href: str, source_path: Path) -> str:
    # Keep relative links relative, so a single episode folder can be moved intact.
    parsed = urlsplit(href)
    if parsed.scheme != "file":
        return href
    target = Path(unquote(parsed.path)).resolve()
    result = quote(os.path.relpath(target, source_path.parent).replace(os.sep, "/"), safe="/:")
    return result + ("#" + parsed.fragment if parsed.fragment else "")


def render_markdown(
    source: str, source_path: Path
) -> tuple[str, list[dict[str, str | int]]]:
    md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
    md.enable(["table", "strikethrough"])
    tokens = md.parse(source)
    toc: list[dict[str, str | int]] = []
    used: dict[str, int] = {}
    current_section = ""

    for index, token in enumerate(tokens):
        if token.type == "inline" and token.children:
            for child in token.children:
                if child.type == "link_open":
                    href = child.attrGet("href")
                    if href:
                        child.attrSet("href", rewrite_internal_href(href, source_path))
            if len(token.children) == 1 and token.children[0].type == "code_inline" and index > 0:
                tokens[index - 1].attrSet("class", "formula")

        if token.type != "heading_open" or index + 1 >= len(tokens):
            continue
        level = int(token.tag[1])
        heading = plain_text(tokens[index + 1].content)
        if level == 2:
            current_section = heading
        anchor = slugify(heading, used)
        token.attrSet("id", anchor)
        if level in (2, 3):
            toc.append({"level": level, "title": heading, "anchor": anchor})

    return md.renderer.render(tokens, md.options, {}), toc


def toc_html(items: list[dict[str, str | int]]) -> str:
    links = []
    for item in items:
        title = html.escape(str(item["title"]))
        anchor = quote(str(item["anchor"]))
        links.append(
            f'<a class="toc-link level-{item["level"]}" href="#{anchor}">{title}</a>'
        )
    return "\n".join(links)


def build_html(source_path: Path, source: str) -> str:
    title, body = prepare_markdown(source)
    article_html, toc = render_markdown(body, source_path)
    info = source_information(source)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    source_href = quote(source_path.name)
    body_class = "note-page"
    eyebrow = "小宇宙播客 · 阅读笔记"
    meta_markup = ""
    if info:
        meta_markup = (
            '<div class="meta source-information">'
            f'<span>{html.escape(info["channel"])}</span>'
            f'<span>{html.escape(info["date"])}</span>'
            f'<span>{html.escape(info["duration"])}</span>'
            f'<a href="{html.escape(info["url"], quote=True)}">原文</a></div>'
        )

    return f"""<!doctype html>
<html lang="zh-CN" data-ui-version="xiaoyuzhou-reading-v0.4">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(title)}的播客笔记阅读视图">
  <title>{html.escape(title)} · 播客笔记</title>
  <style>
    /* 阅读页 v0.4：纸质杂志风。单栏窄宽、衬线正文、单一强调色；亮色 / 纸张 / 暗色三档主题。 */
    :root {{
      color-scheme: light;
      --paper: #fbf9f5;
      --ink: #26221f;
      --body: #3a3531;
      --muted: #7a716a;
      --faint: #a69d94;
      --line: #e7e0d6;
      --accent: #b4492f;
      --accent-soft: #f6e9e2;
      --code-bg: #f3eee6;
      --selection: #f1d4c6;
      --serif: "Songti SC", "Source Han Serif SC", "Noto Serif SC", "Noto Serif CJK SC", "Iowan Old Style", Georgia, "PingFang SC", "Microsoft YaHei", serif;
      --sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
      --col: 680px;
      --side: 188px;
      --gap: 64px;
      --wrap: calc(var(--side) + var(--gap) + var(--col) + 56px);
    }}
    :root[data-theme="paper"] {{
      --paper: #f2e9d8;
      --ink: #2f2518;
      --body: #43382a;
      --muted: #6f6350;
      --faint: #9a8d76;
      --line: #ddd0b6;
      --accent: #a23b22;
      --accent-soft: #ead9c0;
      --code-bg: #eadfc8;
      --selection: #e3c9a8;
    }}
    :root[data-theme="dark"] {{
      color-scheme: dark;
      --paper: #171514;
      --ink: #f0eae2;
      --body: #d9d2c9;
      --muted: #9b9188;
      --faint: #70675f;
      --line: #2e2926;
      --accent: #e5906f;
      --accent-soft: #2c211c;
      --code-bg: #24201d;
      --selection: #5a3a2c;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; scroll-padding-top: 88px; -webkit-text-size-adjust: 100%; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--body);
      font-family: var(--serif);
      font-size: 18px;
      line-height: 1.95;
      -webkit-font-smoothing: antialiased;
      transition: background-color .25s, color .25s;
    }}
    ::selection {{ background: var(--selection); }}
    a {{ text-underline-offset: 4px; }}

    .progress {{ position: fixed; top: 0; left: 0; z-index: 30; width: 0; height: 2px; background: var(--accent); }}

    .topbar {{ position: sticky; top: 0; z-index: 20; border-bottom: 1px solid var(--line); background: var(--paper); }}
    @supports (background: color-mix(in srgb, red 50%, blue)) {{
      .topbar {{ background: color-mix(in srgb, var(--paper) 88%, transparent); -webkit-backdrop-filter: blur(14px); backdrop-filter: blur(14px); }}
    }}
    .topbar-inner {{ max-width: var(--wrap); margin: auto; padding: 13px 28px; display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    .brand {{ color: var(--muted); font: 600 13px/1 var(--sans); letter-spacing: .16em; text-decoration: none; }}
    .topbar-actions {{ display: flex; align-items: center; gap: 16px; font-family: var(--sans); }}
    .source-link {{ padding-bottom: 1px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 13px; line-height: 1.3; text-decoration: none; }}
    .source-link:hover {{ border-color: var(--accent); color: var(--accent); }}
    .theme-toggle {{ min-width: 52px; padding: 6px 11px; border: 1px solid var(--line); border-radius: 999px; background: transparent; color: var(--muted); font: 500 12px/1 var(--sans); cursor: pointer; }}
    .theme-toggle:hover {{ border-color: var(--accent); color: var(--accent); }}
    .source-link:focus-visible, .theme-toggle:focus-visible, .toc-link:focus-visible, .mobile-toc summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 3px; }}

    .masthead-inner {{ max-width: var(--wrap); margin: auto; padding: 76px 28px 48px; display: grid; grid-template-columns: var(--side) minmax(0, var(--col)); column-gap: var(--gap); }}
    .masthead-inner > * {{ grid-column: 2; }}
    .eyebrow {{ margin: 0 0 20px; color: var(--accent); font: 600 12px/1 var(--sans); letter-spacing: .22em; }}
    h1 {{ margin: 0; color: var(--ink); font-family: var(--serif); font-size: clamp(30px, 4.4vw, 44px); line-height: 1.32; font-weight: 700; letter-spacing: .01em; text-wrap: balance; overflow-wrap: anywhere; }}
    .meta {{ margin-top: 26px; color: var(--muted); font: 400 14px/1.9 var(--sans); }}
    .meta > * {{ display: inline; }}
    .meta > * + *::before {{ content: "·"; margin: 0 .65em; color: var(--faint); text-decoration: none; }}
    .meta a {{ color: var(--accent); text-decoration: none; }}
    .meta a:hover {{ text-decoration: underline; }}

    .layout {{ max-width: var(--wrap); margin: auto; padding: 12px 28px 120px; display: grid; grid-template-columns: var(--side) minmax(0, var(--col)); column-gap: var(--gap); align-items: start; }}
    .toc {{ position: sticky; top: 84px; max-height: calc(100vh - 112px); overflow: auto; padding-top: 6px; font-family: var(--sans); }}
    .toc-title {{ margin: 0 0 14px; color: var(--faint); font-size: 11px; font-weight: 600; letter-spacing: .2em; }}
    .toc-link {{ display: block; padding: 5px 0 5px 14px; border-left: 2px solid transparent; color: var(--muted); font-size: 13px; line-height: 1.5; text-decoration: none; transition: color .15s, border-color .15s; }}
    .toc-link.level-2 {{ margin-top: 12px; color: var(--ink); font-weight: 600; }}
    .toc-title + .toc-link.level-2 {{ margin-top: 0; }}
    .toc-link.level-3 {{ padding-left: 24px; }}
    .toc-link:hover {{ color: var(--accent); }}
    .toc-link.active {{ border-left-color: var(--accent); color: var(--accent); }}

    article {{ min-width: 0; }}
    article p {{ margin: 0 0 1.15em; text-wrap: pretty; overflow-wrap: anywhere; }}
    article h2 {{ display: flex; align-items: center; gap: 16px; margin: 76px 0 30px; color: var(--accent); font: 600 13px/1.4 var(--sans); letter-spacing: .26em; }}
    article h2::after {{ content: ""; flex: 1; height: 1px; background: var(--line); }}
    article > h2:first-of-type {{ margin-top: 8px; }}
    article h3 {{ margin: 58px 0 16px; color: var(--ink); font-family: var(--serif); font-size: 24px; line-height: 1.5; font-weight: 700; letter-spacing: .005em; }}
    article h2 + h3 {{ margin-top: 0; }}
    article h4 {{ margin: 34px 0 10px; color: var(--ink); font-size: 18px; line-height: 1.5; }}
    article ul, article ol {{ margin: 0 0 1.3em; padding-left: 1.4em; }}
    article li {{ margin-bottom: .45em; padding-left: 2px; }}
    article li::marker {{ color: var(--accent); }}
    article strong {{ color: var(--ink); font-weight: 700; }}
    article a {{ color: var(--accent); text-decoration-thickness: 1px; }}
    article blockquote {{ margin: 1.8em 0; padding: 2px 0 2px 22px; border-left: 2px solid var(--accent); color: var(--muted); }}
    article blockquote p:last-child {{ margin-bottom: 0; }}
    article code {{ padding: .12em .38em; border-radius: 5px; background: var(--code-bg); color: var(--ink); font: .86em var(--mono); overflow-wrap: anywhere; }}
    article pre {{ overflow: auto; margin: 1.6em 0; padding: 16px 18px; border-radius: 10px; background: var(--code-bg); font-size: 14px; line-height: 1.7; }}
    article pre code {{ padding: 0; background: transparent; font-size: inherit; }}
    article table {{ display: block; overflow-x: auto; width: 100%; margin: 1.8em 0; border-collapse: collapse; font: 15px/1.7 var(--sans); }}
    article th, article td {{ padding: 10px 16px 10px 0; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    article th {{ border-bottom: 1.5px solid var(--ink); color: var(--ink); font-size: 13px; font-weight: 600; white-space: nowrap; }}
    article hr {{ margin: 3em 0; border: 0; border-top: 1px solid var(--line); }}
    article img {{ max-width: 100%; height: auto; border-radius: 8px; }}
    article p.formula {{ margin: 1.6em 0; padding: 18px 20px; border-radius: 10px; background: var(--accent-soft); text-align: center; }}
    article p.formula code {{ padding: 0; background: transparent; color: var(--accent); font-size: 16px; font-weight: 600; }}

    /* 核心结论：摘要式引言，不使用卡片与色块 */
    #核心结论 + p {{ position: relative; margin: 0 0 8px; color: var(--ink); font-size: 21px; line-height: 1.85; font-weight: 500; }}
    #核心结论 + p::before {{ content: "\\201C"; display: block; height: 30px; margin-bottom: 2px; color: var(--accent); font: 700 72px/1 var(--serif); }}

    .sync-note {{ margin-top: 80px; padding-top: 20px; border-top: 1px solid var(--line); color: var(--faint); font: 12px/1.6 var(--sans); }}
    .mobile-toc {{ display: none; }}

    @media (max-width: 900px) {{
      :root {{ --gap: 0px; }}
      .topbar-inner {{ padding: 12px 24px; }}
      .masthead-inner, .layout {{ grid-template-columns: minmax(0, var(--col)); justify-content: center; }}
      .masthead-inner > * {{ grid-column: 1; }}
      .masthead-inner {{ padding: 52px 24px 30px; }}
      .layout {{ padding: 8px 24px 88px; }}
      .toc {{ display: none; }}
      .mobile-toc {{ display: block; margin: 0 0 40px; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); font-family: var(--sans); }}
      .mobile-toc summary {{ padding: 12px 0; color: var(--muted); font-size: 13px; cursor: pointer; }}
      .mobile-toc nav {{ padding: 0 0 10px; }}
    }}
    @media (max-width: 600px) {{
      body {{ font-size: 17px; line-height: 1.9; }}
      .topbar-inner {{ padding: 11px 20px; }}
      .topbar-actions {{ gap: 12px; }}
      .masthead-inner {{ padding: 40px 20px 26px; }}
      .layout {{ padding: 4px 20px 72px; }}
      .meta {{ margin-top: 20px; }}
      article h2 {{ margin: 60px 0 24px; }}
      article h3 {{ margin: 46px 0 14px; font-size: 21px; }}
      #核心结论 + p {{ font-size: 19px; }}
      #核心结论 + p::before {{ height: 26px; font-size: 60px; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      body, .toc-link {{ transition: none; }}
    }}
    @media print {{
      :root, :root[data-theme] {{ --paper: #fff; --ink: #000; --body: #111; --muted: #444; --line: #bbb; --accent: #000; --code-bg: #f2f2f2; color-scheme: light; }}
      .progress, .topbar, .toc, .mobile-toc {{ display: none !important; }}
      body {{ background: #fff; font-size: 11pt; line-height: 1.8; }}
      .masthead-inner, .layout {{ display: block; max-width: none; padding-left: 0; padding-right: 0; }}
      article h2, article h3 {{ break-after: avoid; }}
      article a {{ color: inherit; text-decoration: none; }}
      #核心结论 + p::before {{ color: #000; }}
    }}
  </style>
  <script>
    (function () {{
      var theme;
      try {{ theme = localStorage.getItem('xiaoyuzhou-reading-theme'); }} catch (error) {{}}
      if (theme !== 'light' && theme !== 'paper' && theme !== 'dark') {{
        theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
      }}
      document.documentElement.dataset.theme = theme;
    }})();
  </script>
</head>
<body class="{body_class}">
  <div class="progress" aria-hidden="true"></div>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">播客笔记</span>
      <div class="topbar-actions">
        <a class="source-link" href="{source_href}">查看 Markdown</a>
        <button class="theme-toggle" type="button" aria-label="切换阅读主题" title="切换阅读主题：亮色 / 纸张 / 暗色">亮色</button>
      </div>
    </div>
  </header>
  <section class="masthead">
    <div class="masthead-inner">
      <p class="eyebrow">{html.escape(eyebrow)}</p>
      <h1>{html.escape(title)}</h1>
      {meta_markup}
    </div>
  </section>
  <main class="layout">
    <aside class="toc" aria-label="文章目录"><p class="toc-title">本篇目录</p>{toc_html(toc)}</aside>
    <article>
      <details class="mobile-toc"><summary>展开本篇目录</summary><nav>{toc_html(toc)}</nav></details>
      {article_html}
      <footer class="sync-note">此页面由 Markdown 自动生成。生成时间：{generated}</footer>
    </article>
  </main>
  <script>
    const progress = document.querySelector('.progress');
    const links = [...document.querySelectorAll('.toc-link')];
    const headings = links.map(link => document.getElementById(decodeURIComponent(link.hash.slice(1)))).filter(Boolean);
    const themeToggle = document.querySelector('.theme-toggle');
    const themes = [['light', '亮色'], ['paper', '纸张'], ['dark', '暗色']];
    function showTheme() {{
      const index = Math.max(0, themes.findIndex(item => item[0] === document.documentElement.dataset.theme));
      themeToggle.textContent = themes[index][1];
      themeToggle.setAttribute('aria-label', '切换阅读主题，当前' + themes[index][1]);
      return index;
    }}
    showTheme();
    themeToggle.addEventListener('click', () => {{
      const next = themes[(showTheme() + 1) % themes.length][0];
      document.documentElement.dataset.theme = next;
      showTheme();
      try {{ localStorage.setItem('xiaoyuzhou-reading-theme', next); }} catch (error) {{}}
    }});
    function updateReadingState() {{
      const scrollable = document.documentElement.scrollHeight - innerHeight;
      progress.style.width = `${{scrollable > 0 ? Math.min(100, scrollY / scrollable * 100) : 0}}%`;
      let current = headings[0];
      for (const heading of headings) {{ if (heading.getBoundingClientRect().top <= 120) current = heading; }}
      links.forEach(link => link.classList.toggle('active', current && decodeURIComponent(link.hash.slice(1)) === current.id));
    }}
    addEventListener('scroll', updateReadingState, {{ passive: true }});
    updateReadingState();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    output = (args.output or source.with_suffix(".html")).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(source, source.read_text(encoding="utf-8")), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
