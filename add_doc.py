#!/usr/bin/env python3
"""
知识库内容添加工具
用法：
  python3 add_doc.py --html   <本地HTML文件>   [--source 文档名]
  python3 add_doc.py --md     <本地MD文件>     [--source 文档名]
  python3 add_doc.py --cooper <resourceId>    [--source 文档名]
  python3 add_doc.py --text   <本地TXT文件>    [--source 文档名] [--title 文档标题]

执行后会更新 data/chunks.json，重启 app 即可生效。
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CHUNKS_FILE = "data/chunks.json"


# ── HTML 解析（支持 XWiki / Cooper 导出的 HTML）────────────────────────────

def parse_html(html_path: str, source: str) -> list[dict]:
    from bs4 import BeautifulSoup

    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    # 移除导航、侧边栏等无关区域
    for tag in soup.select("nav, .wiki-toc, #xwikimaincontainer + *"):
        tag.decompose()

    # 找正文
    main = (soup.find("div", id="xwikicontent") or
            soup.find("div", class_="wiki-content") or
            soup.find("article") or
            soup.find("body"))
    if not main:
        raise ValueError("找不到正文区域")

    img_dir = Path(html_path).parent / (Path(html_path).stem + "_files")
    return _split_by_headings(main, source, str(img_dir))


def _split_by_headings(container, source: str, img_dir: str) -> list[dict]:
    """按 h2/h3/h4 切分成 chunks"""
    chunks = []
    heading_stack = []  # [(level, text)]
    current_nodes = []

    def flush(nodes, heading_stack):
        if not nodes and not heading_stack:
            return
        heading = heading_stack[-1][1] if heading_stack else source
        path = " > ".join(h[1] for h in heading_stack) if heading_stack else source
        md, text = nodes_to_md(nodes, img_dir)
        if text.strip():
            chunks.append({
                "heading": heading,
                "path": path,
                "content_md": md.strip(),
                "content_text": text.strip(),
                "source": source,
            })

    for el in container.children:
        if not hasattr(el, "name"):
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5"):
            flush(current_nodes, heading_stack)
            current_nodes = []
            level = int(el.name[1])
            title = el.get_text(strip=True)
            # trim stack to current level
            heading_stack = [(l, t) for l, t in heading_stack if l < level]
            heading_stack.append((level, title))
        else:
            current_nodes.append(el)

    flush(current_nodes, heading_stack)
    return chunks


def nodes_to_md(nodes, img_dir: str) -> tuple[str, str]:
    """把 BeautifulSoup 节点列表转成 (markdown, plain_text)"""
    md_parts, text_parts = [], []
    for node in nodes:
        md, txt = node_to_md(node, img_dir)
        md_parts.append(md)
        text_parts.append(txt)
    return "\n\n".join(p for p in md_parts if p.strip()), \
           " ".join(p for p in text_parts if p.strip())


def node_to_md(node, img_dir: str) -> tuple[str, str]:
    if not hasattr(node, "name"):
        t = str(node).strip()
        return t, t

    tag = node.name
    text = node.get_text(" ", strip=True)

    if tag in ("h1","h2","h3","h4","h5","h6"):
        level = int(tag[1])
        return f"{'#'*level} {text}", text

    if tag == "p":
        inner_md = _inline_md(node, img_dir)
        return inner_md, text

    if tag in ("ul", "ol"):
        items = []
        for li in node.find_all("li", recursive=False):
            items.append(f"- {li.get_text(' ', strip=True)}")
        return "\n".join(items), text

    if tag == "table":
        return _table_to_md(node), text

    if tag == "img":
        src = node.get("src", "")
        data_uri = _img_to_data_uri(src, img_dir)
        alt = node.get("alt", "图片")
        if data_uri:
            return f"![{alt}]({data_uri})", ""
        return "", ""

    if tag in ("strong", "b"):
        return f"**{text}**", text

    if tag in ("em", "i"):
        return f"*{text}*", text

    # 递归子节点
    parts_md, parts_txt = [], []
    for child in node.children:
        m, t = node_to_md(child, img_dir)
        parts_md.append(m)
        parts_txt.append(t)
    return " ".join(p for p in parts_md if p.strip()), \
           " ".join(p for p in parts_txt if p.strip())


def _inline_md(node, img_dir: str) -> str:
    parts = []
    for child in node.children:
        if not hasattr(child, "name"):
            parts.append(str(child))
        elif child.name == "img":
            src = child.get("src", "")
            data_uri = _img_to_data_uri(src, img_dir)
            alt = child.get("alt", "图片")
            if data_uri:
                parts.append(f"![{alt}]({data_uri})")
        elif child.name in ("strong", "b"):
            parts.append(f"**{child.get_text()}**")
        elif child.name in ("em", "i"):
            parts.append(f"*{child.get_text()}*")
        else:
            parts.append(child.get_text())
    return "".join(parts)


def _table_to_md(table) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    # 在第一行后插入分隔行
    header_cols = len(rows[0].split("|")) - 2
    sep = "| " + " | ".join(["---"] * header_cols) + " |"
    return rows[0] + "\n" + sep + "\n" + "\n".join(rows[1:])


def _img_to_data_uri(src: str, img_dir: str) -> str:
    """把图片转成 base64 data URI；找不到返回空字符串"""
    if src.startswith("data:"):
        return src
    # 本地相对路径
    candidates = [
        src,
        os.path.join(img_dir, os.path.basename(src)),
        os.path.join(img_dir, src.lstrip("./")),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = Path(path).suffix.lower().lstrip(".")
            mime = "image/png" if ext in ("png","") else f"image/{ext}"
            return f"data:{mime};base64,{b64}"
    return ""


# ── Markdown 文件解析 ────────────────────────────────────────────────────────

def parse_md(md_path: str, source: str) -> list[dict]:
    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    chunks = []
    heading_stack = []
    current_lines = []

    def flush():
        if not current_lines:
            return
        text = re.sub(r"!\[.*?\]\([^\)]+\)", "", "\n".join(current_lines))
        text = re.sub(r"[#*`>\-|]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        heading = heading_stack[-1][1] if heading_stack else source
        path = " > ".join(h[1] for h in heading_stack) if heading_stack else source
        if text:
            chunks.append({
                "heading": heading,
                "path": path,
                "content_md": "\n".join(current_lines).strip(),
                "content_text": text,
                "source": source,
            })

    for line in content.split("\n"):
        m = re.match(r"^(#{1,5})\s+(.+)", line)
        if m:
            flush()
            current_lines = []
            level = len(m.group(1))
            title = m.group(2).strip()
            heading_stack = [(l, t) for l, t in heading_stack if l < level]
            heading_stack.append((level, title))
        else:
            current_lines.append(line)

    flush()
    return chunks


# ── 纯文本解析（按空行分段）──────────────────────────────────────────────────

def parse_text(txt_path: str, source: str, title: str) -> list[dict]:
    with open(txt_path, encoding="utf-8") as f:
        content = f.read()

    chunks = []
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", content) if p.strip()]
    for i, para in enumerate(paragraphs):
        chunks.append({
            "heading": title or source,
            "path": f"{title or source} > 第{i+1}段",
            "content_md": para,
            "content_text": para,
            "source": source,
        })
    return chunks


# ── Cooper 文档解析 ──────────────────────────────────────────────────────────

def parse_cooper(resource_id: str, source: str) -> list[dict]:
    print(f"正在从 Cooper 获取文档 {resource_id}...")
    result = subprocess.run(
        ["dws", "doc", "fetch", resource_id, "--app", "cooper"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"dws 命令失败: {result.stderr}")

    content = result.stdout
    if not source:
        # 从第一行标题取
        first_line = content.split("\n")[0].lstrip("# ").strip()
        source = first_line or f"Cooper-{resource_id}"

    # 写临时 md 文件再解析
    tmp = f"/tmp/cooper_{resource_id}.md"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    return parse_md(tmp, source)


# ── 合并写入 chunks.json ─────────────────────────────────────────────────────

def merge_into_kb(new_chunks: list[dict], source: str):
    with open(CHUNKS_FILE, encoding="utf-8") as f:
        existing = json.load(f)

    # 确保每个 chunk 都有 doc_name
    for c in new_chunks:
        if "doc_name" not in c:
            c["doc_name"] = source

    # 删除同 doc_name 的旧 chunks（支持更新）
    kept = [c for c in existing
            if (c.get("doc_name") or c.get("source")) != source]
    removed = len(existing) - len(kept)
    if removed:
        print(f"  移除旧版 {source} 的 {removed} 个 chunks")

    # 重新编号
    start_id = max((c.get("id", 0) for c in kept), default=0) + 1
    for i, chunk in enumerate(new_chunks):
        chunk["id"] = start_id + i

    merged = kept + new_chunks
    with open(CHUNKS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False)

    print(f"  ✅ 新增 {len(new_chunks)} 个 chunks，知识库共 {len(merged)} 个")


# ── 主入口 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="向知识库添加文档")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--html",   metavar="FILE",        help="本地 HTML 文件路径")
    group.add_argument("--md",     metavar="FILE",        help="本地 Markdown 文件路径")
    group.add_argument("--text",   metavar="FILE",        help="本地纯文本文件路径")
    group.add_argument("--cooper", metavar="RESOURCE_ID", help="Cooper 文档 resourceId")
    parser.add_argument("--source", default="",  help="来源标识（用于去重/更新）")
    parser.add_argument("--title",  default="",  help="文档标题（纯文本模式用）")
    args = parser.parse_args()

    os.chdir(Path(__file__).parent)

    if args.html:
        source = args.source or Path(args.html).stem
        print(f"解析 HTML 文件: {args.html}")
        chunks = parse_html(args.html, source)
    elif args.md:
        source = args.source or Path(args.md).stem
        print(f"解析 Markdown 文件: {args.md}")
        chunks = parse_md(args.md, source)
    elif args.text:
        source = args.source or Path(args.text).stem
        print(f"解析文本文件: {args.text}")
        chunks = parse_text(args.text, source, args.title)
    elif args.cooper:
        source = args.source or args.cooper
        chunks = parse_cooper(args.cooper, source)

    print(f"  解析出 {len(chunks)} 个 chunks")
    merge_into_kb(chunks, source)
    print("\n重启 app 后新内容即可生效（知识库在 @st.cache_resource 中，需要重启加载）")


if __name__ == "__main__":
    main()
