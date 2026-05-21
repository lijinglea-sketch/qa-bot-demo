import base64
import json
import os
import re
from pathlib import Path

from openai import OpenAI
import streamlit as st

from knowledge_base import KnowledgeBase
import github_sync

COOPER_DOC_URL = "https://cooper.didichuxing.com/didocs/2207954380581"
# 视觉模型：在 .streamlit/secrets.toml 中配置 VISION_MODEL = "模型名"
# Kimi 视觉模型参考：moonshot-v1-8k-vision-preview / kimi-vl-a3b-thinking
# 若不配置则默认尝试 moonshot-v1-8k-vision-preview，失败自动降级为纯文字
def _get_vision_model():
    try:
        import streamlit as _st
        return _st.secrets.get("VISION_MODEL", "moonshot-v1-8k-vision-preview")
    except Exception:
        return "moonshot-v1-8k-vision-preview"

VISION_MODEL = _get_vision_model()

st.set_page_config(
    page_title="工艺规范答疑助手",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
section.main > div { max-width: 900px; margin: 0 auto; }
.conf-high { color: #1a7f37; font-size: 13px; font-weight: 500; }
.conf-low  { color: #cf222e; font-size: 13px; font-weight: 500; }
.ref-header { font-size: 13px; color: #57606a; margin-bottom: 4px; }
.kw-chip {
    display: inline-block; background: #fff8c5; border: 1px solid #d4a72c;
    border-radius: 10px; padding: 1px 8px; font-size: 12px; color: #633c01; margin: 2px;
}
</style>
""", unsafe_allow_html=True)

# ── 初始化 ────────────────────────────────────────────────
@st.cache_resource
def init_kb():
    github_sync.pull()
    kb = KnowledgeBase()
    full = "data/chunks_full.json"
    kb.load_from_file(full if os.path.exists(full) else "data/chunks.json")
    return kb

@st.cache_resource
def init_client():
    return OpenAI(
        api_key=st.secrets["KIMI_API_KEY"],
        base_url="https://api.moonshot.cn/v1"
    )

kb     = init_kb()
client = init_client()

# ── Prompts ───────────────────────────────────────────────
SYSTEM_PROMPT = """你是地图数据生产平台的工艺规范答疑助手，专门解答数据制作规范的问题。

回答规则：
1. 严格基于编号参考资料回答，每处引用必须用 [数字] 标注来源
2. 回答简洁清晰，必要时列条目
3. 如参考资料中没有明确说明，回复：「规范中未明确说明此问题，建议转人工确认。[置信度：低]」
4. 回答最后一行必须单独输出：[置信度：高] 或 [置信度：低]
"""

VISION_STEP1_PROMPT = """你是地图数据生产场景识别专家。分析图片内容，提取关键信息用于工艺规范检索。

只输出 JSON，不要其他文字：
{
  "scene_desc": "场景简要描述（1-2句）",
  "keywords": ["检索词1", "检索词2", "检索词3"],
  "data_issues": "如有数据截图请描述当前数据问题，否则为null"
}

keywords 要具体，贴近地图数据生产术语，如 ["单向道路", "出入口大门", "内部路方向"]
"""

VISION_AGENT_PROMPT = """你是地图数据生产工艺专家。用户提供了现场图片或数据截图，请结合工艺规范给出具体制作建议。

## 图像场景
{scene_desc}{data_issues_text}

## 参考工艺规范（共 {n} 条，引用时用 [数字] 标注）
{context}
{user_question_text}
---

请按以下格式输出：

## 📸 图像解读
（分析图中关键要素：道路类型、通行方向、大门形态、数据现状等）

## ✅ 制作建议
（具体可操作的制作步骤，引用对应规范编号）

## ⚠️ 注意事项
（容易出错的点和边界情况）

## 📄 规范依据
（列出引用的规范条目编号及章节名）
"""

LOW_CONF_KEYWORDS = ["未明确", "建议转人工", "无法确认", "不确定"]

# ── Session State ─────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

# ── 标题 ─────────────────────────────────────────────────
st.title("🗺️ 工艺规范答疑助手")
st.caption(f"工艺规范知识库 · [在 Cooper 中查看完整文档]({COOPER_DOC_URL})")

tab_chat, tab_exam = st.tabs(["💬 答疑", "📝 考试"])


# ── 渲染参考原文（含图片）────────────────────────────────
def render_references(chunks: list):
    if not chunks:
        return
    st.markdown('<p class="ref-header">📎 参考原文（点击展开）</p>', unsafe_allow_html=True)
    for i, chunk in enumerate(chunks, 1):
        section_path = chunk.get("path", chunk.get("heading", ""))
        label        = f"[{i}] {section_path.split(' > ')[-1]}"
        section_name = section_path.split(" > ")[-1]
        with st.expander(label):
            content_md  = chunk.get("content_md", chunk.get("content_text", ""))
            img_pattern = re.compile(r'!\[([^\]]*)\]\((data:image/[^;]+;base64,[^\)]+|https?://[^\)]+)\)')
            parts       = img_pattern.split(content_md)
            text_buf, j = [], 0
            while j < len(parts):
                if j % 3 == 0:
                    seg = parts[j].strip()
                    if seg: text_buf.append(seg)
                elif j % 3 == 2:
                    if text_buf:
                        st.markdown("\n\n".join(text_buf)); text_buf = []
                    img_url = parts[j]
                    if img_url.startswith("data:"):
                        _, b64d = img_url.split(",", 1)
                        st.image(base64.b64decode(b64d))
                    else:
                        st.image(img_url)
                j += 1
            if text_buf:
                st.markdown("\n\n".join(text_buf))
            st.caption(f"📍 {section_path}")
            st.markdown(
                f'<a href="{COOPER_DOC_URL}" target="_blank" style="font-size:12px">'
                f'↗ 在 Cooper 中查看（Ctrl+F 搜索：{section_name}）</a>',
                unsafe_allow_html=True
            )


def uri_to_bytes(uri: str) -> bytes:
    _, b64d = uri.split(",", 1)
    return base64.b64decode(b64d)


# ════════════════════════════════════════════════════════
# TAB 1：答疑（含图像分析）
# ════════════════════════════════════════════════════════
with tab_chat:

    new_msg_slot = st.container()

    # ── 聊天输入框（含图片附件）──────────────────────────
    # accept_file="multiple" 在输入框右侧显示 📎 按钮，支持选择多张图片
    chat_input = st.chat_input(
        "输入问题，点击右侧 📎 可附加图片（支持多张）",
        accept_file="multiple",
        file_type=["png", "jpg", "jpeg"],
    )

    st.divider()

    # ── 历史消息（倒序）──────────────────────────────────
    for msg in reversed(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                st.markdown(msg["content"])
                if "confidence" in msg:
                    conf = msg["confidence"]
                    icon = "✅" if conf == "高" else "⚠️"
                    cls  = "conf-high" if conf == "高" else "conf-low"
                    st.markdown(f'<span class="{cls}">{icon} 置信度：{conf}</span>',
                                unsafe_allow_html=True)
                if msg.get("keywords"):
                    kw_html = " ".join(f'<span class="kw-chip">{k}</span>' for k in msg["keywords"])
                    st.markdown(f"🔍 {kw_html}", unsafe_allow_html=True)
                if msg.get("ref_chunks"):
                    render_references(msg["ref_chunks"])
            else:
                st.markdown(msg["content"])
                for uri in msg.get("images", []):
                    st.image(uri_to_bytes(uri), width=260)

    # ── 处理输入 ──────────────────────────────────────────
    if chat_input is not None:
        # 兼容两种返回类型：纯字符串（旧版）或 ChatInputValue（新版）
        if isinstance(chat_input, str):
            user_text = chat_input
            images    = []
        else:
            user_text = chat_input.text or ""
            images    = []
            for f in (chat_input.files or []):
                b64 = base64.b64encode(f.read()).decode()
                images.append(f"data:{f.type};base64,{b64}")

        if not user_text and not images:
            st.stop()

        # 记录用户消息
        st.session_state.messages.append({
            "role": "user", "content": user_text, "images": images
        })
        with new_msg_slot:
            with st.chat_message("user"):
                if user_text:
                    st.markdown(user_text)
                for uri in images:
                    st.image(uri_to_bytes(uri), width=260)

        # ── 有图片：Agent 三步推理 ─────────────────────────
        if images:
            image_blocks = [
                {"type": "image_url", "image_url": {"url": uri}} for uri in images
            ]
            vision_ok = True  # 标记视觉是否成功

            with new_msg_slot:
                with st.chat_message("assistant"):
                    try:
                        # Step 1：识别场景，提取检索词
                        with st.status("识别图像内容…", expanded=False) as s1:
                            resp1 = client.chat.completions.create(
                                model=VISION_MODEL, max_tokens=400,
                                messages=[
                                    {"role": "system", "content": VISION_STEP1_PROMPT},
                                    {"role": "user", "content": image_blocks + [{
                                        "type": "text",
                                        "text": "分析图片场景。" + (f"用户补充说明：{user_text}" if user_text else "")
                                    }]}
                                ]
                            )
                            raw1 = resp1.choices[0].message.content.strip()
                            try:
                                m     = re.search(r'\{.*\}', raw1, re.DOTALL)
                                step1 = json.loads(m.group()) if m else {}
                            except Exception:
                                step1 = {"scene_desc": raw1, "keywords": [], "data_issues": None}
                            s1.update(label="✅ 图像识别完成", state="complete")

                        scene_desc  = step1.get("scene_desc", "")
                        keywords    = step1.get("keywords", [])
                        data_issues = step1.get("data_issues")

                        # Step 2：检索知识库
                        with st.status("检索相关工艺规范…", expanded=False) as s2:
                            search_q = " ".join(keywords) + (" " + user_text if user_text else "")
                            results  = kb.search(search_q.strip() or scene_desc, top_k=5)
                            s2.update(label=f"✅ 找到 {len(results)} 条相关规范", state="complete")

                        # Step 3：综合推理
                        with st.status("生成制作建议…", expanded=False) as s3:
                            context = "\n\n---\n\n".join(
                                f"[{i}] {r['path']}\n{r['content_text']}"
                                for i, r in enumerate(results, 1)
                            )
                            agent_prompt = VISION_AGENT_PROMPT.format(
                                scene_desc=scene_desc,
                                data_issues_text=f"\n**当前数据问题**：{data_issues}" if data_issues else "",
                                n=len(results),
                                context=context,
                                user_question_text=f"\n**用户问题**：{user_text}\n" if user_text else ""
                            )
                            resp2 = client.chat.completions.create(
                                model=VISION_MODEL, max_tokens=1500,
                                messages=[{"role": "user", "content": image_blocks + [
                                    {"type": "text", "text": agent_prompt}
                                ]}]
                            )
                            answer = resp2.choices[0].message.content.strip()
                            s3.update(label="✅ 分析完成", state="complete")

                        cited_nums = sorted(set(int(n) for n in re.findall(r'\[(\d+)\]', answer)))
                        ref_chunks = [results[n-1] for n in cited_nums if 1 <= n <= len(results)]

                        if keywords:
                            kw_html = " ".join(f'<span class="kw-chip">{k}</span>' for k in keywords)
                            st.markdown(f"🔍 {kw_html}", unsafe_allow_html=True)
                        st.markdown(answer)
                        if ref_chunks:
                            render_references(ref_chunks)

                        st.session_state.messages.append({
                            "role": "assistant", "content": answer,
                            "keywords": keywords, "ref_chunks": ref_chunks
                        })

                    except Exception as vision_err:
                        # 视觉模型不可用，降级为纯文字问答
                        vision_ok = False
                        st.warning(
                            f"⚠️ 图像分析不可用（模型 `{VISION_MODEL}` 不支持视觉输入），"
                            f"已切换为纯文字问答。\n\n"
                            f"**解决方法**：在 `.streamlit/secrets.toml` 中配置 `VISION_MODEL = \"正确的视觉模型名\"`"
                        )

            # 视觉失败时，把图片附加说明当文字问答继续处理
            if not vision_ok and user_text:
                images = []  # 清空图片，走文字分支
                # 把"用户上传了图片"写入问题上下文
                user_text_with_note = f"{user_text}\n\n（用户同时上传了 {len(image_blocks)} 张图片，但图像分析暂不可用）"
                results = kb.search(user_text, top_k=5)
                context = "\n\n---\n\n".join(
                    f"[{i}] 章节：{r['path']}\n{r['content_text']}"
                    for i, r in enumerate(results, 1)
                )
                with new_msg_slot:
                    with st.chat_message("assistant"):
                        with st.spinner("查阅规范中…"):
                            response = client.chat.completions.create(
                                model="moonshot-v1-32k", max_tokens=1024,
                                messages=[
                                    {"role": "system", "content": SYSTEM_PROMPT},
                                    {"role": "user", "content": f"参考资料：\n\n{context}\n\n---\n\n问题：{user_text_with_note}"}
                                ]
                            )
                        raw_answer = response.choices[0].message.content
                        confidence = "低" if (any(kw in raw_answer for kw in LOW_CONF_KEYWORDS) or "[置信度：低]" in raw_answer) else "高"
                        clean      = re.sub(r'\[置信度[：:][高低]\]', '', raw_answer).strip()
                        cited_nums = sorted(set(int(n) for n in re.findall(r'\[(\d+)\]', clean)))
                        ref_chunks = [results[n-1] for n in cited_nums if 1 <= n <= len(results)]
                        st.markdown(clean)
                        if ref_chunks:
                            render_references(ref_chunks)
                st.session_state.messages.append({
                    "role": "assistant", "content": clean,
                    "confidence": confidence, "ref_chunks": ref_chunks
                })

        # ── 无图片：普通问答 ───────────────────────────────
        else:
            results = kb.search(user_text, top_k=5)
            context = "\n\n---\n\n".join(
                f"[{i}] 章节：{r['path']}\n{r['content_text']}"
                for i, r in enumerate(results, 1)
            )
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages[:-1]
                if m["role"] in ("user", "assistant")
            ]
            user_content = [{"type": "text",
                             "text": f"参考资料（共{len(results)}条）：\n\n{context}\n\n---\n\n问题：{user_text}"}]

            with new_msg_slot:
                with st.chat_message("assistant"):
                    with st.spinner("查阅规范中…"):
                        response = client.chat.completions.create(
                            model="moonshot-v1-32k", max_tokens=1024,
                            messages=[
                                {"role": "system", "content": SYSTEM_PROMPT},
                                *history,
                                {"role": "user", "content": user_content}
                            ]
                        )
                    raw_answer = response.choices[0].message.content
                    confidence = "低" if (any(kw in raw_answer for kw in LOW_CONF_KEYWORDS) or "[置信度：低]" in raw_answer) else "高"
                    clean      = re.sub(r'\[置信度[：:][高低]\]', '', raw_answer).strip()
                    cited_nums = sorted(set(int(n) for n in re.findall(r'\[(\d+)\]', clean)))
                    ref_chunks = [results[n-1] for n in cited_nums if 1 <= n <= len(results)]

                    st.markdown(clean)
                    cls  = "conf-high" if confidence == "高" else "conf-low"
                    icon = "✅" if confidence == "高" else "⚠️"
                    st.markdown(f'<span class="{cls}">{icon} 置信度：{confidence}</span>',
                                unsafe_allow_html=True)
                    if ref_chunks:
                        render_references(ref_chunks)

                    if confidence == "低" or st.button("🙋 转人工", key=f"t{len(st.session_state.messages)}"):
                        with st.status("正在通知运营同学…") as s:
                            import time; time.sleep(1)
                            s.update(label="✅ 已通知运营同学", state="complete")
                        st.info("📱 运营同学将通过语音/图片方式为您解答")

            st.session_state.messages.append({
                "role": "assistant", "content": clean,
                "confidence": confidence, "ref_chunks": ref_chunks
            })


# ════════════════════════════════════════════════════════
# TAB 2：考试
# ════════════════════════════════════════════════════════
EXAM_GEN_PROMPT = """你是地图数据生产平台的考试出题专家。根据以下工艺规范内容，出{n}道考试题。

题型要求：{types}

输出严格遵守如下 JSON 格式（只输出 JSON，不要其他文字）：
[
  {{"id":1,"type":"choice","question":"题干","options":["A. ...","B. ...","C. ...","D. ..."],"answer":"A","explanation":"解析..."}},
  {{"id":2,"type":"judge","question":"题干","options":["正确","错误"],"answer":"正确","explanation":"解析..."}},
  {{"id":3,"type":"short","question":"题干","options":[],"answer":"参考答案...","explanation":""}}
]

规范内容：
{context}
"""

EXAM_GRADE_PROMPT = """你是考试评分专家。根据标准答案对学员答案逐题评分。

只输出 JSON：
[{{"id":1,"correct":true,"score":20,"comment":"评语"}}]

题目与标准答案：
{qa_pairs}

学员答案：
{student_answers}
"""

if "exam_questions" not in st.session_state: st.session_state.exam_questions = []
if "exam_answers"   not in st.session_state: st.session_state.exam_answers   = {}
if "exam_results"   not in st.session_state: st.session_state.exam_results   = []

with tab_exam:
    all_docs = {}
    for c in kb.chunks:
        doc = c.get("doc_name") or c.get("source", "未知")
        all_docs.setdefault(doc, []).append(c)

    c1, c2, c3, c4 = st.columns([3, 1, 2, 1])
    with c1:
        selected_doc = st.selectbox("选择文档", list(all_docs.keys()), label_visibility="collapsed")
    with c2:
        n_questions  = st.number_input("题目数", min_value=3, max_value=20, value=5, label_visibility="collapsed")
    with c3:
        q_types      = st.multiselect("题型", ["选择题","判断题","简答题"], default=["选择题","判断题"], label_visibility="collapsed")
    with c4:
        gen_btn      = st.button("生成试题", type="primary", use_container_width=True)

    if gen_btn:
        if not q_types:
            st.warning("请至少选择一种题型")
        else:
            doc_chunks = all_docs.get(selected_doc, [])
            context    = "\n\n---\n\n".join(f"【{c['path']}】\n{c['content_text']}" for c in doc_chunks)
            prompt     = EXAM_GEN_PROMPT.format(n=n_questions, types="、".join(q_types), context=context[:8000])
            with st.spinner("正在生成试题…"):
                resp = client.chat.completions.create(
                    model="moonshot-v1-32k", max_tokens=3000,
                    messages=[{"role": "user", "content": prompt}]
                )
            raw = resp.choices[0].message.content.strip()
            m   = re.search(r'\[.*\]', raw, re.DOTALL)
            if m:
                try:
                    st.session_state.exam_questions = json.loads(m.group())
                    st.session_state.exam_answers   = {}
                    st.session_state.exam_results   = []
                except Exception as e:
                    st.error(f"解析题目失败：{e}")
            else:
                st.error("模型未返回有效 JSON，请重试")

    questions = st.session_state.exam_questions
    if questions:
        st.divider()
        submitted  = bool(st.session_state.exam_results)
        label_map  = {"choice": "选择题", "judge": "判断题", "short": "简答题"}

        for q in questions:
            qid, qtype = q["id"], q["type"]
            st.markdown(f"**第 {qid} 题** <span style='font-size:12px;color:#888'>（{label_map.get(qtype,qtype)}）</span>",
                        unsafe_allow_html=True)
            st.markdown(q["question"])

            if submitted:
                result   = next((r for r in st.session_state.exam_results if r["id"] == qid), {})
                user_ans = st.session_state.exam_answers.get(str(qid), "（未作答）")
                correct  = result.get("correct", False)
                st.markdown(f"{'✅' if correct else '❌'} 你的答案：**{user_ans}**")
                if not correct:
                    st.markdown(f"正确答案：**{q['answer']}**")
                if q.get("explanation"):
                    st.caption(f"解析：{q['explanation']}")
                if result.get("comment"):
                    st.caption(f"点评：{result['comment']}")
            else:
                if qtype in ("choice", "judge"):
                    opts = q.get("options", [])
                    prev = st.session_state.exam_answers.get(str(qid))
                    idx  = opts.index(prev) if prev in opts else 0
                    ans  = st.radio("", opts, index=idx, key=f"q{qid}", label_visibility="collapsed")
                else:
                    ans  = st.text_area("", value=st.session_state.exam_answers.get(str(qid), ""),
                                        key=f"q{qid}", height=80, label_visibility="collapsed",
                                        placeholder="请输入你的回答…")
                st.session_state.exam_answers[str(qid)] = ans
            st.markdown("---")

        if not submitted:
            if st.button("提交答卷", type="primary", use_container_width=True):
                qa_pairs    = "\n".join(f"第{q['id']}题：{q['question']}\n答案：{q['answer']}" for q in questions)
                student_ans = "\n".join(f"第{k}题：{v}" for k, v in st.session_state.exam_answers.items())
                with st.spinner("评分中…"):
                    resp = client.chat.completions.create(
                        model="moonshot-v1-32k", max_tokens=2000,
                        messages=[{"role": "user", "content": EXAM_GRADE_PROMPT.format(
                            qa_pairs=qa_pairs, student_answers=student_ans
                        )}]
                    )
                m = re.search(r'\[.*\]', resp.choices[0].message.content.strip(), re.DOTALL)
                if m:
                    try:
                        st.session_state.exam_results = json.loads(m.group())
                        st.rerun()
                    except Exception as e:
                        st.error(f"评分解析失败：{e}")
                else:
                    st.error("评分返回格式异常，请重试")
        else:
            total         = len(questions)
            correct_count = sum(1 for r in st.session_state.exam_results if r.get("correct"))
            score         = round(correct_count / total * 100)
            (st.success if score >= 80 else st.warning if score >= 60 else st.error)(
                f"得分：**{score} 分**（{correct_count}/{total} 题正确）"
            )
            if st.button("重新出题", use_container_width=True):
                st.session_state.exam_questions = []
                st.session_state.exam_answers   = {}
                st.session_state.exam_results   = []
                st.rerun()


# ── 侧边栏 ────────────────────────────────────────────────
with st.sidebar:
    with st.expander("➕ 添加文档", expanded=False):
        add_type = st.radio("来源类型", ["上传文件", "Cooper ID"], horizontal=True,
                            label_visibility="collapsed")
        category = st.selectbox("分类", ["工艺", "常见问题", "其他"])

        if add_type == "上传文件":
            uploaded = st.file_uploader("选择文件", type=["html","htm","md","txt"],
                                        label_visibility="collapsed")
            src_name = st.text_input("文档名称", placeholder="例如：交通事件规范V2.0", key="up_src")
            if st.button("导入", use_container_width=True, disabled=not (uploaded and src_name)):
                import tempfile, sys as _sys
                _sys.path.insert(0, ".")
                import add_doc as _ad
                suffix = Path(uploaded.name).suffix.lower()
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(uploaded.read()); tmp_path = tmp.name
                with st.spinner("解析中…"):
                    try:
                        if suffix in (".html", ".htm"): chunks_new = _ad.parse_html(tmp_path, src_name)
                        elif suffix == ".md":            chunks_new = _ad.parse_md(tmp_path, src_name)
                        else:                            chunks_new = _ad.parse_text(tmp_path, src_name, src_name)
                        for c in chunks_new: c["category"] = category
                        _ad.merge_into_kb(chunks_new, src_name)
                        github_sync.push(f"add doc: {src_name}")
                        init_kb.clear(); st.success(f"✅ 导入 {len(chunks_new)} 个片段"); st.rerun()
                    except Exception as e:
                        st.error(f"导入失败：{e}")
                    finally:
                        os.unlink(tmp_path)
        else:
            cooper_id = st.text_input("resourceId", placeholder="例如：2207954380581", key="cp_id")
            src_name  = st.text_input("文档名称", placeholder="例如：大比例尺工艺规范", key="cp_src")
            if st.button("从 Cooper 导入", use_container_width=True, disabled=not (cooper_id and src_name)):
                import sys as _sys; _sys.path.insert(0, "."); import add_doc as _ad
                with st.spinner("从 Cooper 读取中…"):
                    try:
                        chunks_new = _ad.parse_cooper(cooper_id.strip(), src_name)
                        for c in chunks_new: c["category"] = category
                        _ad.merge_into_kb(chunks_new, src_name)
                        github_sync.push(f"add doc: {src_name}")
                        init_kb.clear(); st.success(f"✅ 导入 {len(chunks_new)} 个片段"); st.rerun()
                    except Exception as e:
                        st.error(f"导入失败：{e}")

    st.divider()
    st.markdown("**📚 知识库**")

    tree = {}
    for c in kb.chunks:
        cat = c.get("category", "其他")
        doc = c.get("doc_name") or c.get("source", "未知")
        tree.setdefault(cat, {}).setdefault(doc, []).append(c)

    cat_icons = {"工艺": "🔧", "常见问题": "💬", "其他": "📁"}
    cat_order = ["工艺", "常见问题", "其他"] + [c for c in tree if c not in ["工艺", "常见问题", "其他"]]

    for cat in cat_order:
        if cat not in tree: continue
        docs         = tree[cat]
        total_chunks = sum(len(v) for v in docs.values())
        with st.expander(f"{cat_icons.get(cat,'📁')} {cat}  ·  {len(docs)} 份 / {total_chunks} 段", expanded=False):
            for doc_name, chunks_in_doc in docs.items():
                with st.expander(f"📄 {doc_name}  ·  {len(chunks_in_doc)} 段", expanded=False):
                    for chunk in chunks_in_doc:
                        label = chunk.get("heading", chunk.get("path", "")).split(" > ")[-1][:28]
                        st.markdown(f"<span style='font-size:11px;color:#57606a'>· {label}</span>",
                                    unsafe_allow_html=True)
                    st.markdown("")
                    if st.button("🗑 删除此文档", key=f"del_{doc_name}", use_container_width=True):
                        with open("data/chunks.json", encoding="utf-8") as _f:
                            _all = json.load(_f)
                        _kept = [c for c in _all if (c.get("doc_name") or c.get("source")) != doc_name]
                        with open("data/chunks.json", "w", encoding="utf-8") as _f:
                            json.dump(_kept, _f, ensure_ascii=False)
                        github_sync.push(f"delete doc: {doc_name}")
                        init_kb.clear(); st.rerun()

    st.divider()
    if st.button("🗑️ 清空对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
