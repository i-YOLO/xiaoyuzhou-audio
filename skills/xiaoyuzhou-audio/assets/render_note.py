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
<html lang="zh-CN" data-ui-version="xiaoyuzhou-reading-v0.3">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(title)}的播客笔记阅读视图">
  <title>{html.escape(title)} · 播客笔记</title>
  <style>
    :root {{
      color-scheme: light;
      --paper: #fbfcfa;
      --surface: #ffffff;
      --ink: #202421;
      --muted: #68716b;
      --line: #d9dedb;
      --accent: #087f6b;
      --accent-soft: #e8f5f1;
      --warm: #9a5b13;
      --warm-soft: #fff4df;
      --berry: #9b4051;
      --blue: #356b8c;
      --max: 760px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; scroll-padding-top: 96px; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      font-size: 17px;
      line-height: 1.85;
      letter-spacing: 0;
    }}
    .progress {{ position: fixed; inset: 0 auto auto 0; z-index: 20; width: 0; height: 3px; background: var(--accent); }}
    .topbar {{ border-bottom: 1px solid var(--line); background: rgba(251, 252, 250, .94); }}
    .topbar-inner {{ max-width: 1240px; margin: auto; padding: 12px 28px; display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    .brand {{ color: var(--ink); font-size: 14px; font-weight: 700; text-decoration: none; }}
    .source-link {{ color: var(--accent); font-size: 14px; text-decoration: none; }}
    .source-link:hover {{ text-decoration: underline; }}
    .masthead {{ border-bottom: 1px solid var(--line); background: var(--surface); }}
    .masthead-inner {{ max-width: 920px; margin: auto; padding: 64px 28px 52px; }}
    .eyebrow {{ margin: 0 0 12px; color: var(--accent); font-size: 13px; font-weight: 700; text-transform: uppercase; }}
    h1 {{ max-width: 850px; margin: 0; font-family: ui-serif, "Songti SC", STSong, serif; font-size: 58px; line-height: 1.18; font-weight: 700; }}
    .rating {{ margin-top: 22px; color: var(--warm); font-size: 18px; font-weight: 700; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 7px 18px; margin-top: 20px; color: var(--muted); font-size: 14px; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }}
    .tags span {{ padding: 3px 9px; border: 1px solid #bfd8d1; border-radius: 4px; color: #28685c; background: var(--accent-soft); font-size: 13px; }}
    .layout {{ max-width: 1240px; margin: auto; display: grid; grid-template-columns: 240px minmax(0, var(--max)); gap: 76px; align-items: start; padding: 54px 28px 96px; }}
    .toc {{ position: sticky; top: 28px; max-height: calc(100vh - 56px); overflow: auto; padding-right: 18px; }}
    .toc-title {{ margin: 0 0 14px; color: var(--muted); font-size: 12px; font-weight: 700; }}
    .toc-link {{ display: block; margin: 0 0 7px; border-left: 2px solid var(--line); padding: 3px 0 3px 12px; color: var(--muted); font-size: 13px; line-height: 1.45; text-decoration: none; }}
    .toc-link.level-3 {{ padding-left: 24px; font-size: 12px; }}
    .toc-link:hover, .toc-link.active {{ border-left-color: var(--accent); color: var(--accent); }}
    article {{ min-width: 0; }}
    article h2, article h3 {{ position: relative; font-family: ui-serif, "Songti SC", STSong, serif; letter-spacing: 0; }}
    article h2 {{ margin: 68px 0 22px; padding-top: 10px; border-top: 1px solid var(--line); font-size: 29px; line-height: 1.35; }}
    article h2:first-child {{ margin-top: 0; }}
    article h3 {{ margin: 42px 0 16px; font-size: 22px; line-height: 1.45; }}
    article p {{ margin: 0 0 20px; }}
    article ul, article ol {{ margin: 0 0 24px; padding-left: 1.5em; }}
    article li {{ margin-bottom: 8px; padding-left: 4px; }}
    article strong {{ color: #111411; }}
    article a {{ color: var(--accent); text-underline-offset: 3px; }}
    article blockquote {{ margin: 28px 0; border-left: 4px solid var(--warm); padding: 16px 22px; background: var(--warm-soft); color: #4f3a20; }}
    article blockquote p:last-child {{ margin-bottom: 0; }}
    article code {{ padding: 2px 5px; border-radius: 3px; background: #eef1ef; color: #8a3f28; font-family: "SFMono-Regular", Consolas, monospace; font-size: .88em; overflow-wrap: anywhere; }}
    article pre {{ overflow: auto; padding: 18px; border: 1px solid var(--line); background: #f2f4f2; }}
    article pre code {{ padding: 0; background: transparent; }}
    article table {{ width: 100%; margin: 26px 0; border-collapse: collapse; font-size: 15px; }}
    article th, article td {{ border-bottom: 1px solid var(--line); padding: 10px 12px; text-align: left; vertical-align: top; }}
    article th {{ background: #f1f5f3; }}
    article img {{ max-width: 100%; height: auto; }}
    #我的感受 {{ margin-top: 0; border-top: 0; color: #7b470d; }}
    .metadata-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 24px; margin: 0; padding: 18px 22px 18px 42px; border: 1px solid var(--line); background: #f6f8f6; font-size: 14px; line-height: 1.55; }}
    .metadata-grid li {{ margin: 5px 0; }}
    #核心概念地图 {{ margin-bottom: 14px; border-top-color: #9bc9bc; color: #075e50; }}
    #核心概念地图 + p {{ max-width: 700px; margin-bottom: 28px; color: #45534d; font-size: 18px; line-height: 1.75; }}
    .concept-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 0 0 42px; padding: 0; list-style: none; counter-reset: concept; }}
    .concept-card {{ position: relative; min-height: 210px; margin: 0; padding: 52px 22px 22px; overflow: hidden; border: 1px solid var(--line); border-top: 4px solid var(--accent); background: var(--surface); font-size: 15px; line-height: 1.72; counter-increment: concept; }}
    .concept-card::before {{ content: "0" counter(concept); position: absolute; top: 15px; left: 22px; color: var(--accent); font-family: "SFMono-Regular", Consolas, monospace; font-size: 13px; font-weight: 700; }}
    .concept-card::after {{ content: ""; position: absolute; top: -34px; right: -34px; width: 82px; height: 82px; border: 1px solid currentColor; border-radius: 50%; opacity: .12; }}
    .concept-card:nth-child(3n + 2) {{ border-top-color: var(--warm); color: #493921; }}
    .concept-card:nth-child(3n + 2)::before {{ color: var(--warm); }}
    .concept-card:nth-child(3n) {{ border-top-color: var(--blue); color: #263b49; }}
    .concept-card:nth-child(3n)::before {{ color: var(--blue); }}
    .concept-card p {{ margin: 0; }}
    .concept-card strong {{ display: block; margin-bottom: 10px; color: var(--ink); font-family: ui-serif, "Songti SC", STSong, serif; font-size: 20px; line-height: 1.35; }}
    #核心结论 + p {{ margin: 0 0 38px; padding: 22px 24px; border-left: 4px solid var(--berry); background: #fbf2f4; color: #4f3037; font-family: ui-serif, "Songti SC", STSong, serif; font-size: 20px; line-height: 1.7; }}
    #核心观点 + ol {{ padding: 0; list-style: none; counter-reset: insight; }}
    #核心观点 + ol > li {{ position: relative; margin: 0; padding: 15px 8px 15px 46px; border-bottom: 1px solid var(--line); counter-increment: insight; }}
    #核心观点 + ol > li::before {{ content: counter(insight, decimal-leading-zero); position: absolute; top: 18px; left: 4px; color: var(--accent); font-family: "SFMono-Regular", Consolas, monospace; font-size: 12px; font-weight: 700; }}
    .formula {{ margin: 24px 0 28px; padding: 22px; border: 1px solid #a8c9d9; background: #edf6fa; text-align: center; }}
    .formula code {{ padding: 0; background: transparent; color: #174f6e; font-size: 17px; font-weight: 700; }}
    .sync-note {{ margin-top: 72px; padding-top: 22px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }}
    .mobile-toc {{ display: none; }}
    @media (max-width: 960px) {{
      h1 {{ font-size: 46px; }}
      .layout {{ grid-template-columns: minmax(0, var(--max)); justify-content: center; gap: 24px; padding-top: 28px; }}
      .toc {{ display: none; }}
      .mobile-toc {{ display: block; margin-bottom: 34px; border: 1px solid var(--line); background: var(--surface); }}
      .mobile-toc summary {{ cursor: pointer; padding: 12px 16px; font-size: 14px; font-weight: 700; }}
      .mobile-toc nav {{ padding: 0 16px 12px; }}
    }}
    @media (max-width: 600px) {{
      body {{ font-size: 16px; line-height: 1.78; }}
      .topbar-inner {{ padding: 10px 18px; }}
      .masthead-inner {{ padding: 42px 20px 36px; }}
      h1 {{ font-size: 36px; }}
      .layout {{ padding: 22px 20px 64px; }}
      article h2 {{ margin-top: 54px; font-size: 25px; }}
      article h3 {{ margin-top: 34px; font-size: 20px; }}
      .metadata-grid, .concept-grid {{ grid-template-columns: 1fr; }}
      .concept-card {{ min-height: 0; }}
      #核心概念地图 + p {{ font-size: 17px; }}
      #核心结论 + p {{ font-size: 18px; }}
    }}
    @media print {{
      .progress, .topbar, .toc, .mobile-toc {{ display: none !important; }}
      body {{ background: #fff; font-size: 11pt; }}
      .masthead-inner, .layout {{ max-width: 100%; padding-left: 0; padding-right: 0; }}
      .layout {{ display: block; }}
      article h2, article h3 {{ break-after: avoid; }}
      article a {{ color: inherit; }}
    }}
    /* Tutti-inspired macOS reading layer: content structure stays unchanged. */
    :root {{
      --paper: #edf5fb;
      --surface: rgba(255, 255, 255, .78);
      --ink: #18202a;
      --muted: #66717e;
      --line: rgba(71, 92, 116, .16);
      --accent: #0a84ff;
      --accent-soft: rgba(10, 132, 255, .10);
      --warm: #b36b1e;
      --warm-soft: rgba(255, 159, 10, .10);
      --berry: #af4d72;
      --blue: #2878c8;
      --panel-shadow: 0 18px 50px rgba(43, 73, 105, .10), 0 2px 8px rgba(43, 73, 105, .06);
      --max: 1040px;
    }}
    body {{
      min-height: 100vh;
      background:
        radial-gradient(circle at 8% 0%, rgba(131, 196, 255, .28), transparent 31rem),
        radial-gradient(circle at 94% 20%, rgba(255, 222, 188, .32), transparent 34rem),
        linear-gradient(180deg, #eef6fc 0%, #f7fafc 44%, #edf3f7 100%);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      font-size: 17px;
      line-height: 1.82;
      letter-spacing: -.006em;
    }}
    .topbar {{
      position: sticky;
      top: 12px;
      z-index: 18;
      border: 0;
      background: transparent;
    }}
    .topbar-inner {{
      width: calc(100% - 32px);
      max-width: 1320px;
      margin: auto;
      padding: 10px 12px 10px 18px;
      border: 1px solid rgba(255, 255, 255, .72);
      border-radius: 18px;
      background: rgba(248, 251, 254, .72);
      box-shadow: 0 10px 32px rgba(48, 74, 102, .10);
      backdrop-filter: blur(24px) saturate(1.45);
      -webkit-backdrop-filter: blur(24px) saturate(1.45);
    }}
    .brand {{ display: inline-flex; align-items: center; gap: 9px; font-size: 13px; font-weight: 720; letter-spacing: -.01em; }}
    .brand::before {{ content: ""; width: 9px; height: 9px; border-radius: 50%; background: #32d74b; box-shadow: 0 0 0 5px rgba(50, 215, 75, .11); }}
    .topbar-actions {{ display: flex; align-items: center; gap: 7px; }}
    .source-link, .theme-toggle {{
      border: 1px solid rgba(72, 94, 119, .12);
      border-radius: 11px;
      padding: 7px 11px;
      color: #27679e;
      background: rgba(255, 255, 255, .58);
      font: 650 12px/1.2 inherit;
      text-decoration: none;
    }}
    .source-link:hover, .theme-toggle:hover {{ border-color: rgba(10, 132, 255, .28); background: rgba(10, 132, 255, .08); text-decoration: none; }}
    .theme-toggle {{ width: 32px; height: 32px; padding: 0; cursor: pointer; color: var(--ink); }}
    .masthead {{ border: 0; background: transparent; }}
    .masthead-inner {{ max-width: 1320px; padding: 88px 28px 42px; }}
    .eyebrow {{ margin-bottom: 15px; color: var(--accent); font-size: 12px; font-weight: 760; letter-spacing: .08em; }}
    h1 {{
      max-width: 900px;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", sans-serif;
      font-size: clamp(42px, 6vw, 66px);
      line-height: 1.08;
      font-weight: 780;
      letter-spacing: -.045em;
    }}
    .meta {{ gap: 8px; margin-top: 24px; }}
    .meta span, .meta a {{
      border: 1px solid rgba(78, 101, 127, .12);
      border-radius: 999px;
      padding: 5px 10px;
      background: rgba(255, 255, 255, .52);
      color: var(--muted);
      font-size: 12px;
    }}
    .tags {{ margin-top: 10px; }}
    .tags span {{ color: #1769aa; background: var(--accent-soft); }}
    .layout {{ max-width: 1320px; grid-template-columns: 220px minmax(0, 1fr); gap: 32px; padding: 24px 28px 110px; }}
    .toc {{
      top: 92px;
      padding: 18px 16px;
      border: 1px solid rgba(255, 255, 255, .72);
      border-radius: 20px;
      background: rgba(255, 255, 255, .56);
      box-shadow: 0 12px 34px rgba(45, 73, 103, .07);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
    }}
    .toc-title {{ margin-bottom: 12px; color: #8a95a0; font-size: 11px; letter-spacing: .08em; }}
    .toc-link {{ margin-bottom: 4px; border: 0; border-radius: 9px; padding: 6px 9px; }}
    .toc-link.level-3 {{ padding-left: 20px; }}
    .toc-link:hover, .toc-link.active {{ border: 0; color: #086fcf; background: rgba(10, 132, 255, .10); }}
    article {{
      padding: 46px 52px 52px;
      border: 1px solid rgba(255, 255, 255, .82);
      border-radius: 28px;
      background: rgba(255, 255, 255, .76);
      box-shadow: var(--panel-shadow);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
    }}
    article h2, article h3 {{
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", sans-serif;
      font-weight: 740;
      letter-spacing: -.025em;
    }}
    article h2 {{ margin-top: 64px; border-top-color: rgba(72, 94, 119, .13); font-size: 28px; }}
    article h3 {{ font-size: 21px; }}
    article p {{ margin-bottom: 19px; }}
    article a {{ color: #0878db; text-decoration-thickness: 1px; }}
    article blockquote, #核心结论 + p {{
      border: 1px solid rgba(255, 159, 10, .16);
      border-left: 4px solid var(--warm);
      border-radius: 16px;
      background: rgba(255, 248, 236, .76);
    }}
    article pre, .formula {{ border-radius: 16px; }}
    article table {{
      display: block;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 16px;
      border-collapse: separate;
      border-spacing: 0;
      background: rgba(255, 255, 255, .55);
    }}
    article th {{ background: rgba(10, 132, 255, .07); }}
    .metadata-grid {{
      border-color: rgba(72, 94, 119, .13);
      border-radius: 18px;
      background: rgba(236, 244, 250, .66);
    }}
    .concept-grid {{ gap: 12px; }}
    .concept-card {{
      min-height: 190px;
      border: 1px solid rgba(72, 94, 119, .13);
      border-top: 1px solid rgba(72, 94, 119, .13);
      border-radius: 19px;
      background: rgba(255, 255, 255, .68);
      box-shadow: 0 8px 22px rgba(43, 73, 105, .06);
    }}
    .concept-card::after {{ border: 0; background: var(--accent-soft); }}
    .concept-card strong {{ font-family: inherit; font-weight: 730; }}
    #核心结论 + p {{ border-left-color: var(--accent); background: rgba(229, 242, 255, .70); color: #244966; font-family: inherit; }}
    .progress {{ height: 2px; background: linear-gradient(90deg, #0a84ff, #5ac8fa); }}
    .sync-note {{ opacity: .72; }}
    .dashboard-band {{ border: 0; background: transparent; }}
    .dashboard-inner {{ max-width: 1160px; padding: 12px 28px 34px; }}
    .metric-strip {{ gap: 12px; border: 0; background: transparent; }}
    .metric {{
      min-height: 140px;
      border: 1px solid rgba(255, 255, 255, .78) !important;
      border-radius: 20px;
      background: rgba(255, 255, 255, .64);
      box-shadow: 0 10px 28px rgba(43, 73, 105, .07);
      backdrop-filter: blur(16px);
    }}
    .metric.primary {{ color: #f7fbff; background: linear-gradient(145deg, #0a84ff, #0969c7); }}
    .metric.primary span, .metric.primary small {{ color: rgba(255, 255, 255, .76); }}
    .visual-grid {{ gap: 12px; margin-top: 12px; }}
    .viz-panel, .topic-map {{
      border: 1px solid rgba(255, 255, 255, .78);
      border-radius: 22px;
      background: rgba(255, 255, 255, .64);
      box-shadow: 0 10px 28px rgba(43, 73, 105, .07);
      backdrop-filter: blur(16px);
    }}
    .viz-panel h2, .topic-map h2, .topic-node span {{ font-family: inherit; }}
    .rating-panel {{ color: #f7fbff; background: linear-gradient(150deg, #147dd6, #58a9ec); }}
    .rating-panel .panel-index, .rating-panel header p {{ color: rgba(255, 255, 255, .70); }}
    .topic-map {{ margin-top: 12px; background: rgba(25, 34, 45, .88); }}
    .topic-nodes {{ gap: 8px; border: 0; background: transparent; }}
    .topic-node {{ border-radius: 15px; background: rgba(255, 255, 255, .08); }}
    .topic-node:hover {{ background: rgba(10, 132, 255, .22); }}
    .library-home .masthead {{ color: var(--ink); background: transparent; }}
    .library-home .masthead-inner {{ min-height: 320px; padding: 90px 28px 46px; }}
    .library-home .eyebrow {{ color: var(--accent); }}
    .library-home .masthead-copy, .library-home .masthead .meta {{ color: var(--muted); }}
    .library-home .layout {{ max-width: 1320px; grid-template-columns: 210px minmax(0, 1fr); gap: 32px; padding-top: 24px; }}
    .library-home article {{ padding: 38px 42px 46px; }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) {{ color-scheme: dark; }}
    }}
    :root[data-theme="dark"] {{
      color-scheme: dark;
      --paper: #111820;
      --surface: rgba(30, 39, 49, .82);
      --ink: #edf3f8;
      --muted: #9da9b5;
      --line: rgba(202, 218, 233, .13);
      --accent-soft: rgba(10, 132, 255, .18);
      --warm-soft: rgba(255, 159, 10, .13);
    }}
    :root[data-theme="dark"] body {{
      background:
        radial-gradient(circle at 8% 0%, rgba(25, 99, 157, .25), transparent 31rem),
        radial-gradient(circle at 94% 20%, rgba(113, 76, 45, .22), transparent 34rem),
        #111820;
    }}
    :root[data-theme="dark"] .topbar-inner,
    :root[data-theme="dark"] .toc,
    :root[data-theme="dark"] article,
    :root[data-theme="dark"] .metric,
    :root[data-theme="dark"] .viz-panel {{
      border-color: rgba(255, 255, 255, .09);
      background: rgba(29, 38, 48, .74);
    }}
    :root[data-theme="dark"] .source-link,
    :root[data-theme="dark"] .theme-toggle,
    :root[data-theme="dark"] .meta span {{ border-color: rgba(255, 255, 255, .09); background: rgba(255, 255, 255, .06); color: #9ecfff; }}
    :root[data-theme="dark"] article strong {{ color: #fff; }}
    :root[data-theme="dark"] article table,
    :root[data-theme="dark"] .concept-card,
    :root[data-theme="dark"] .metadata-grid {{ border-color: rgba(255, 255, 255, .10); background: rgba(255, 255, 255, .045); }}
    :root[data-theme="dark"] article th {{ background: rgba(10, 132, 255, .13); }}
    :root[data-theme="dark"] article code {{ background: rgba(255, 255, 255, .08); color: #ffb28f; }}
    :root[data-theme="dark"] article blockquote {{ color: #f1d5ae; background: rgba(255, 159, 10, .08); }}
    :root[data-theme="dark"] #核心结论 + p {{ color: #c8e4fb; background: rgba(10, 132, 255, .10); }}
    @media (max-width: 960px) {{
      .layout, .library-home .layout {{
        grid-template-columns: minmax(0, var(--max));
        justify-content: center;
        gap: 24px;
      }}
      article {{ padding: 38px 34px 44px; }}
      .library-home article {{ padding: 34px; }}
      article p, article li, article a {{ overflow-wrap: anywhere; }}
    }}
    @media (max-width: 600px) {{
      .topbar {{ top: 8px; }}
      .topbar-inner {{ width: calc(100% - 20px); padding-left: 13px; border-radius: 15px; }}
      .source-link {{ display: none; }}
      .masthead-inner {{ padding: 62px 20px 28px; }}
      h1 {{ font-size: 38px; }}
      .layout {{ padding: 14px 12px 64px; }}
      article, .library-home article {{ padding: 28px 20px 36px; border-radius: 22px; }}
      .metric-strip {{ grid-template-columns: repeat(2, 1fr); }}
      .metric {{ min-height: 118px; padding: 16px; }}
      .visual-grid {{ grid-template-columns: 1fr; }}
      .topic-nodes {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    :root {{ --paper: #F5F7FB; --surface: #FFFFFF; --ink: #1F2937;
      --muted: #64748B; --accent: #2563EB; --accent-soft: #EFF6FF; --line: #E2E8F0; }}
    body {{ background: var(--paper); }}
    .topbar-inner, .toc, article {{ background: var(--surface); border-color: var(--line); }}
    .source-link, .eyebrow, .toc-link.active, .source-information a {{ color: var(--accent); }}
    .metadata-grid, .concept-card {{ background: #F8FAFC; border-color: var(--line); }}
    article strong {{ color: var(--ink); }}
    :root[data-theme="dark"] {{ --paper: #0F172A; --surface: #172033;
      --ink: #E5E7EB; --muted: #A8B3C5; --accent: #60A5FA;
      --accent-soft: #1D3358; --line: #2B3B55; }}
    :root[data-theme="dark"] body {{ background: var(--paper); }}
    :root[data-theme="dark"] .topbar-inner, :root[data-theme="dark"] .toc,
    :root[data-theme="dark"] article {{ background: var(--surface); border-color: var(--line); }}
    :root[data-theme="dark"] .metadata-grid, :root[data-theme="dark"] .concept-card {{
      background: #1C2940; border-color: var(--line); }}
  </style>
</head>
<body class="{body_class}">
  <div class="progress" aria-hidden="true"></div>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">播客笔记</span>
      <div class="topbar-actions">
        <a class="source-link" href="{source_href}">查看 Markdown</a>
        <button class="theme-toggle" type="button" aria-label="切换明暗主题" title="切换明暗主题">◐</button>
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
    try {{
      const savedTheme = localStorage.getItem('xiaoyuzhou-reading-theme');
      document.documentElement.dataset.theme = savedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    }} catch (error) {{}}
    themeToggle?.addEventListener('click', () => {{
      const current = document.documentElement.dataset.theme;
      const systemDark = matchMedia('(prefers-color-scheme: dark)').matches;
      const next = current ? (current === 'dark' ? 'light' : 'dark') : (systemDark ? 'light' : 'dark');
      document.documentElement.dataset.theme = next;
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
