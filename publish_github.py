#!/usr/bin/env python3
"""title-referee GitHub 发布脚本（基于 micro-product-mvp 验证模板）"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

GH_USER = "tomlen045"

REPOS = [
    {
        "dir": "/Users/len/.hermes/cache/scratch/title-referee",
        "name": "title-referee",
        "desc": "⚖️ Local offline WeChat article title scorer & clickbait detector — 公众号标题评分+标题党检测，纯本地零依赖，评分逐条可解释",
        "topics": ["wechat", "title-analyzer", "clickbait", "nlp", "self-hosted",
                   "content-creation", "新媒体", "公众号", "zero-dependency", "offline"],
        "msg": "feat: v1.0.0 — 本地离线标题评分引擎（14 条标题党规则 + 5 组正向信号 + 长度/结构修正），可解释报告导出，零依赖单文件",
    },
]


def get_token():
    r = subprocess.run(["security", "find-generic-password", "-s", "GH_TOKEN",
                        "-a", GH_USER, "-w"], capture_output=True, text=True)
    tok = r.stdout.strip()
    if not tok:
        print("❌ 钥匙串没有 GH_TOKEN"); sys.exit(1)
    return tok


TOKEN = get_token()
HDRS = {"Authorization": "token " + TOKEN,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json"}


def api(method, path, payload=None):
    req = urllib.request.Request("https://api.github.com" + path,
                                 data=json.dumps(payload).encode() if payload else None,
                                 headers=HDRS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def run(cmd, cwd, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    return r.returncode, (r.stdout + r.stderr).strip()


overall_ok = True
for repo in REPOS:
    name = repo["name"]
    print("=" * 8, name, "=" * 8)

    st, body = api("POST", "/user/repos", {
        "name": name, "description": repo["desc"],
        "has_wiki": False, "private": False, "auto_init": False})
    if st == 201:
        print("✅ 仓库已创建")
    elif st == 422:
        print("ℹ 仓库已存在,跳过创建")
    else:
        print("❌ 建仓失败", st, json.dumps(body)[:200]); overall_ok = False; continue

    d = repo["dir"]
    if not os.path.isdir(os.path.join(d, ".git")):
        run(["git", "init", "-b", "main"], d)
    run(["git", "config", "user.name", GH_USER], d)
    run(["git", "config", "user.email", GH_USER + "@users.noreply.github.com"], d)
    run(["git", "add", "-A"], d)
    rc, out = run(["git", "commit", "-m", repo["msg"]], d)
    print("commit:", "ok(新提交)" if rc == 0 else "无变更")

    url = "git@ssh.github.com:%s/%s.git" % (GH_USER, name)
    rc, _ = run(["git", "remote", "get-url", "github"], d)
    if rc != 0:
        run(["git", "remote", "add", "github", url], d)
    else:
        run(["git", "remote", "set-url", "github", url], d)
    ssh_env = {"GIT_SSH_COMMAND":
               "ssh -p 443 -i %s/.ssh/id_ed25519_github -o StrictHostKeyChecking=accept-new"
               % os.path.expanduser("~")}
    rc, out = run(["git", "push", "-u", "github", "main"], d, env=ssh_env)
    if rc == 0:
        print("✅ 推送成功")
    else:
        print("❌ 推送失败:", out[-300:]); overall_ok = False; continue

    api("PATCH", "/repos/%s/%s" % (GH_USER, name), {"description": repo["desc"]})
    st2, _ = api("PUT", "/repos/%s/%s/topics" % (GH_USER, name), {"names": repo["topics"]})
    print("topics:", "✅" if st2 in (200, 204) else "⚠ %d" % st2)

    try:
        with urllib.request.urlopen("https://github.com/%s/%s" % (GH_USER, name), timeout=20) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    st3, info = api("GET", "/repos/%s/%s" % (GH_USER, name))
    ok = code == 200 and info.get("default_branch") == "main" and info.get("private") is False
    print(("✅ 发布验证通过: https://github.com/%s/%s (HTTP %s, branch=%s)"
           % (GH_USER, name, code, info.get("default_branch"))) if ok else
          ("⚠ 网页 %s / API default_branch=%s private=%s"
           % (code, info.get("default_branch"), info.get("private"))))
    if not ok:
        overall_ok = False

print()
print("🎉 全部发布完成" if overall_ok else "⚠ 部分失败,见上方日志")
sys.exit(0 if overall_ok else 1)
