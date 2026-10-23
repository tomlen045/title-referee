#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标题裁判 (TitleReferee) — 本地离线的公众号标题评分+标题党检测工具。
零依赖单文件（纯 Python 标准库）。启动: python3 app.py
路由: / 页面 · /api/meta · /api/generate(评分) · /download/<id>.txt
"""
import json
import os
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
OUT_DIR = os.path.join(BASE, "output")
STATIC = os.path.join(BASE, "static")
for d in (DATA_DIR, OUT_DIR):
    os.makedirs(d, exist_ok=True)

# ============ 评分规则库（产品护城河：可解释、可离线、持续运营） ============

# 标题党/违规词表 (pattern, 扣分, 等级, 说明)
CLICKBAIT_RULES = [
    (r"震惊!?|惊呆|惊悚|吓死|炸裂|炸了|崩了", 15, "高危", "夸张情绪词，微信平台明令整治的标题党用语"),
    (r"不看后悔|后悔莫及|删前速看|速看马上删", 15, "高危", "制造恐慌+紧迫诱导，典型标题党句式"),
    (r"99%的人不知道|99%的人都不知道", 12, "高危", "虚构统计，平台重点整治句式"),
    (r"必看!?$|不看必亏|千万.{0,2}别", 10, "中危", "强命令式诱导，降低平台推荐权重"),
    (r"最全|最强|最强攻略|终极|史上最", 8, "中危", "绝对化用语，广告法与平台规范双重风险"),
    (r"第一$|全网第一|第一人|之父", 8, "中危", "绝对化用语（广告法禁用词族）"),
    (r"永久|100%|百分百|绝对", 8, "中危", "绝对化承诺，不可验证"),
    (r"福利!?$|白嫖|薅羊毛|免费领", 6, "低危", "利益诱导词，易被判定营销号"),
    (r"内部消息|内幕|泄密|机密", 12, "高危", "虚假内幕暗示，严肃违规风险"),
    (r"刚刚|突发!?$|紧急通知", 10, "中危", "假新闻句式，非真实突发禁用"),
    (r" awe|你敢看吗|敢看吗|胆小勿入", 10, "中危", "好奇心胁迫句式"),
    (r"…$|？？？", 6, "低危", "悬念省略/连环问号，滥用降低可信度"),
    (r"轉發|转发这|不转不是", 15, "高危", "道德绑架式转发诱导，平台明令禁止"),
    (r" kms|笑死|笑不活了|家人们谁懂", 4, "低危", "网络流行语，时效性强易过气（视账号调性）"),
]

# 正向信号词 (pattern, 加分, 说明)
GOOD_RULES = [
    (r"\d+天|\d+个|\d+条|\d+步|\d+招|\d+分钟|\d+元", 6, "具体数字提升信息密度与可信度"),
    (r"保姆级|一文读懂|实战|实测|亲测|复盘|教程|指南", 6, "干货信号词，实用币（收藏转发动机）"),
    (r"对比|评测|横评|选型|优缺点|踩坑", 6, "评测信号词，决策辅助类内容"),
    (r"为什么|如何|怎么|什么是", 4, "疑问式开头，触发好奇但不夸张"),
    (r"我|我们|亲历|我.{0,4}年", 3, "第一人称/亲历感，真实币"),
]

PLATFORM_MAX = 64   # 微信公众号标题上限

def _count_cn(s):
    return len(s)

def score_title(title, platform="wechat"):
    t = title.strip()
    n = _count_cn(t)
    issues = []   # {rule, level, deduct, msg, hit}
    goods = []
    total = 100.0

    # --- 长度维度 (30分权重折算进100分制: 直接扣/加) ---
    if n == 0:
        return {"error": "标题为空"}
    if n > PLATFORM_MAX:
        issues.append({"level": "高危", "deduct": 20, "msg": "超长 %d 字，超出微信公众号 %d 字上限（超出部分发布时被截断）" % (n, PLATFORM_MAX), "hit": t[PLATFORM_MAX:]})
        total -= 20
    elif n > 30:
        issues.append({"level": "低危", "deduct": 4, "msg": "%d 字偏长：手机订阅消息/分享卡片只显示前 ~30 字，关键信息可能被截断" % n, "hit": t[30:]})
        total -= 4
    elif n < 8:
        issues.append({"level": "中危", "deduct": 6, "msg": "仅 %d 字：信息量不足，读者无法判断内容价值" % n, "hit": ""})
        total -= 6
    else:
        goods.append({"add": 3, "msg": "长度 %d 字，落在移动端最佳展示区间（10-30 字）" % n})
        total += 3

    # --- 标题党/违规词 ---
    high_cnt = 0
    for pat, deduct, level, msg in CLICKBAIT_RULES:
        hits = re.findall(pat, t, re.IGNORECASE)
        if hits:
            high_cnt += (level == "高危")
            issues.append({"level": level, "deduct": deduct, "msg": msg, "hit": hits[0]})
            total -= deduct * len(hits)   # 重复命中累计扣分

    # 高危词叠加惩罚: 每多一个高危类别再 -5（标题党炸弹必须一票触底）
    if high_cnt >= 2:
        total -= 5 * (high_cnt - 1)
        issues.append({"level": "高危", "deduct": 5 * (high_cnt - 1),
                       "msg": "叠加 %d 类高危标题党特征，综合判定为标题党炸弹" % high_cnt, "hit": ""})

    # --- 正向信号 ---
    for pat, add, msg in GOOD_RULES:
        if re.search(pat, t):
            goods.append({"add": add, "msg": msg + "（命中：%s）" % re.search(pat, t).group(0)})
            total += add

    # --- 结构信号 ---
    if re.match(r"^【[^】]{1,8}】", t):
        goods.append({"add": 3, "msg": "【】栏目标签开头，识别度高"})
        total += 3
    if "|" in t or "｜" in t or "：" in t or ":" in t:
        goods.append({"add": 2, "msg": "分隔符切分信息结构，扫读友好"})
        total += 2
    # 前置重要词检查: 前10字截断点
    if n > 15 and re.match(r"^[\u4e00-\u9fa5\d]*的|^(关于|浅谈|论|记)", t):
        issues.append({"level": "低危", "deduct": 3, "msg": "开头是虚词/泛词（关于/浅谈…），前 10 字黄金位被浪费", "hit": t[:10]})
        total -= 3

    total = float(max(0, min(100, total)))   # 强制 float: 满分时报告与 API 均显示 100.0
    grade = ("S" if total >= 85 else "A" if total >= 72 else
             "B" if total >= 60 else "C" if total >= 45 else "D")
    verdict = {
        "S": "可直接发布：结构、合规、吸引力均衡",
        "A": "良好：微调 1-2 处可冲 S",
        "B": "合格但平庸：按下方建议改写",
        "C": "有标题党/违规风险或明显短板，建议重写",
        "D": "高危：存在平台限流/处罚风险，必须重写",
    }[grade]
    issues.sort(key=lambda x: -x["deduct"])
    return {"total": round(total, 1), "grade": grade, "verdict": verdict,
            "issues": issues, "goods": goods, "len": n,
            "clickbait": any(i["level"] == "高危" for i in issues)}


def build_report(pid, title, result):
    lines = []
    lines.append("=" * 46)
    lines.append("标题裁判 · 评分报告")
    lines.append("编号: %s   时间: %s" % (pid[:8], time.strftime("%Y-%m-%d %H:%M")))
    lines.append("=" * 46)
    lines.append("")
    lines.append("原标题: %s" % title)
    lines.append("综合得分: %s / 100   等级: %s   (%s)" % (result["total"], result["grade"], result["verdict"]))
    lines.append("标题党风险: %s   字数: %d" % ("有 ⚠️" if result["clickbait"] else "未检出", result["len"]))
    lines.append("")
    if result["issues"]:
        lines.append("── 扣分项（按影响排序）──")
        for i in result["issues"]:
            hit = (" 命中:「%s」" % i["hit"]) if i["hit"] else ""
            lines.append("  [%s -%s] %s%s" % (i["level"], i["deduct"], i["msg"], hit))
        lines.append("")
    if result["goods"]:
        lines.append("── 加分项 ──")
        for g in result["goods"]:
            lines.append("  [+%s] %s" % (g["add"], g["msg"]))
        lines.append("")
    lines.append("── 改写方向建议 ──")
    if result["clickbait"]:
        lines.append("  1. 删除/替换全部高危夸张词，用具体数字或亲历细节替代情绪渲染")
    if result["len"] > 30:
        lines.append("  · 把最关键的信息压进前 10 字（分享卡片只显示前半句）")
    if not re.search(r"\d", title):
        lines.append("  · 加一个具体数字（天数/条数/金额），信息密度立涨")
    if not result["goods"]:
        lines.append("  · 加干货信号词（实测/复盘/保姆级/对比）提升收藏动机")
    lines.append("  · 发布前自检: 标题承诺的内容, 正文兑现了吗? (题文一致是平台红线)")
    lines.append("")
    lines.append("── 免责 ──")
    lines.append("本报告为本地规则引擎评分, 供参考; 实际效果受内容质量/账号权重/发布时间影响。")
    return "\n".join(lines)


def build_deliverable(project):
    title = project["input"].get("title", "")
    result = project["result"]
    out = os.path.join(OUT_DIR, project["id"] + ".txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_report(project["id"], title, result))
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
                return self._send(200, f.read())
        if path == "/cover":
            with open(os.path.join(STATIC, "cover-lab.html"), encoding="utf-8") as f:
                return self._send(200, f.read())
        if path == "/api/meta":
            return self._send(200, json.dumps({
                "rules_clickbait": len(CLICKBAIT_RULES),
                "rules_good": len(GOOD_RULES),
                "platform_max": PLATFORM_MAX,
                "version": "1.0.0",
            }, ensure_ascii=False), "application/json; charset=utf-8")
        m = re.match(r"^/download/([0-9a-f-]{36})\.txt$", path)
        if m:
            fp = os.path.join(OUT_DIR, m.group(1) + ".txt")
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), "text/plain; charset=utf-8",
                                      {"Content-Disposition": "attachment; filename=\"title-report-%s.txt\"" % m.group(1)[:8]})
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/generate":
            return self._send(404, "not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            title = (data.get("title") or "").strip()
            if not title:
                return self._send(400, json.dumps({"ok": False, "error": "标题为空"}),
                                  "application/json; charset=utf-8")
            result = score_title(title)
            if "error" in result:
                return self._send(400, json.dumps({"ok": False, "error": result["error"]}),
                                  "application/json; charset=utf-8")
            pid = str(uuid.uuid4())
            project = {"id": pid, "created": time.strftime("%Y-%m-%d %H:%M"),
                       "input": {"title": title}, "result": result}
            with open(os.path.join(DATA_DIR, pid + ".json"), "w", encoding="utf-8") as f:
                json.dump(project, f, ensure_ascii=False, indent=1)
            build_deliverable(project)
            return self._send(200, json.dumps({"ok": True, "id": pid, "result": result,
                                               "download": "/download/%s.txt" % pid},
                                              ensure_ascii=False), "application/json; charset=utf-8")
        except Exception as e:
            return self._send(500, json.dumps({"ok": False, "error": str(e)}),
                              "application/json; charset=utf-8")


if __name__ == "__main__":
    port = int(os.environ.get("APP_PORT", "8670"))
    print("标题裁判 TitleReferee running at http://127.0.0.1:%d" % port)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
