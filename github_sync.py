"""
GitHub 文件同步：把 data/chunks.json 存在 GitHub 仓库里实现持久化。
需要在 .streamlit/secrets.toml 中配置：
  GITHUB_TOKEN = "ghp_xxxx"
  GITHUB_REPO  = "your-username/qa-bot-demo"
  GITHUB_BRANCH = "main"   # 可选，默认 main
"""

import base64
import json
import os
from pathlib import Path

CHUNKS_PATH = "data/chunks.json"


def _cfg():
    try:
        import streamlit as st
        token  = st.secrets.get("GITHUB_TOKEN", "")
        repo   = st.secrets.get("GITHUB_REPO", "")
        branch = st.secrets.get("GITHUB_BRANCH", "main")
    except Exception:
        token  = os.getenv("GITHUB_TOKEN", "")
        repo   = os.getenv("GITHUB_REPO", "")
        branch = os.getenv("GITHUB_BRANCH", "main")
    return token, repo, branch


def is_configured() -> bool:
    token, repo, _ = _cfg()
    return bool(token and repo)


def pull() -> bool:
    """从 GitHub 拉取 chunks.json，覆盖本地文件。返回是否成功。"""
    if not is_configured():
        return False
    import urllib.request, urllib.error
    token, repo, branch = _cfg()
    url = f"https://api.github.com/repos/{repo}/contents/{CHUNKS_PATH}?ref={branch}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        content = base64.b64decode(data["content"].replace("\n", ""))
        Path(CHUNKS_PATH).write_bytes(content)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False  # 文件还不存在，用本地默认值
        print(f"[github_sync] pull failed: {e}")
        return False
    except Exception as e:
        print(f"[github_sync] pull error: {e}")
        return False


def push(commit_message: str = "update knowledge base") -> bool:
    """把本地 chunks.json 推到 GitHub。返回是否成功。"""
    if not is_configured():
        return False
    import urllib.request, urllib.error
    token, repo, branch = _cfg()
    api_url = f"https://api.github.com/repos/{repo}/contents/{CHUNKS_PATH}"

    # 先拿当前 sha（更新已有文件需要）
    sha = None
    req_get = urllib.request.Request(
        f"{api_url}?ref={branch}",
        headers={"Authorization": f"token {token}",
                 "Accept": "application/vnd.github.v3+json"}
    )
    try:
        with urllib.request.urlopen(req_get, timeout=10) as resp:
            sha = json.loads(resp.read()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[github_sync] get sha failed: {e}")
            return False

    content_b64 = base64.b64encode(Path(CHUNKS_PATH).read_bytes()).decode()
    payload = {
        "message": commit_message,
        "content": content_b64,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    req_put = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"token {token}",
                 "Accept": "application/vnd.github.v3+json",
                 "Content-Type": "application/json"},
        method="PUT"
    )
    try:
        with urllib.request.urlopen(req_put, timeout=15) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f"[github_sync] push failed: {e}")
        return False
