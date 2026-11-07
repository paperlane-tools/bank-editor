#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题库整理 AI —— 只用标准库的极简 Agent。

它不"自由发挥"，而是被限制成三步：
    扫描题库 → 分批问模型 → **程序校验** → 人看一眼再落库。
模型只负责产出结构化 JSON 建议；能不能写、写成什么，由这里的规则说了算。

目前做三件事（都是只读题干就能判的）：
    1. 补科目标签 subj —— 老题库没有课程名，补上之后「按年级 × 科目分流」才分得动
    2. 标无效题（老师布置的开放作业/上传题）
    3. 找近似重复（同一道题的两种表述），建议删掉信息少的那条

**不做"补答案"**：题库只存题干前 100 字、不存选项，凭题干补答案很容易把错答案写进库。
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LOCAL_CONFIG = os.path.join(HERE, "ai_config.json")
ROOT_CONFIG = os.path.join(ROOT, "config.json")

# 三个协议之外的服务商，各自只是 base_url 不同
PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "base": "https://api.deepseek.com", "proto": "openai",
     "models": ["deepseek-flash", "deepseek-v4-pro"], "key_url": "https://platform.deepseek.com/api_keys"},
    {"id": "openai", "name": "OpenAI", "base": "https://api.openai.com/v1", "proto": "openai",
     "models": ["gpt-5.2-mini", "gpt-5.2"], "key_url": "https://platform.openai.com/api-keys"},
    {"id": "anthropic", "name": "Anthropic Claude", "base": "https://api.anthropic.com/v1", "proto": "anthropic",
     "models": ["claude-haiku-4-5", "claude-sonnet-4-5"], "key_url": "https://console.anthropic.com/settings/keys"},
    {"id": "gemini", "name": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta",
     "proto": "gemini", "models": ["gemini-3-flash", "gemini-3-pro"], "key_url": "https://aistudio.google.com/apikey"},
    {"id": "moonshot", "name": "月之暗面 Kimi", "base": "https://api.moonshot.cn/v1", "proto": "openai",
     "models": ["kimi-k2-turbo-preview"], "key_url": "https://platform.moonshot.cn/console/api-keys"},
    {"id": "zhipu", "name": "智谱 GLM", "base": "https://open.bigmodel.cn/api/paas/v4", "proto": "openai",
     "models": ["glm-4.6", "glm-4.6v"], "key_url": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys"},
    {"id": "dashscope", "name": "阿里百炼 · 通义千问", "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "proto": "openai", "models": ["qwen3-max"], "key_url": "https://bailian.console.aliyun.com/"},
    {"id": "siliconflow", "name": "硅基流动", "base": "https://api.siliconflow.cn/v1", "proto": "openai",
     "models": ["Qwen/Qwen3-32B-Instruct"], "key_url": "https://cloud.siliconflow.cn/account/ak"},
    {"id": "openrouter", "name": "OpenRouter", "base": "https://openrouter.ai/api/v1", "proto": "openai",
     "models": ["google/gemini-3-flash", "anthropic/claude-sonnet-4.5"], "key_url": "https://openrouter.ai/settings/keys"},
    {"id": "ollama", "name": "本机 Ollama / LM Studio", "base": "http://127.0.0.1:11434/v1", "proto": "openai",
     "models": [], "key_url": "", "no_key": True},
    {"id": "custom", "name": "自定义（OpenAI 兼容）", "base": "", "proto": "openai",
     "models": [], "key_url": ""},
]
PROTOS = ["openai", "anthropic", "gemini"]

TASKS = [
    ("subj", "补科目标签 subj", "从题干判断这是哪门课，补到条目的 subj 里（分年级分流就靠它）"),
    ("junk", "标出无效题", "老师布置的开放作业/拍照上传之类，建议从题库删掉"),
    ("dupe", "找近似重复", "同一道题的两种表述，保留信息多的那条"),
]


class AIError(Exception):
    pass


def provider_by_id(pid):
    for p in PROVIDERS:
        if p["id"] == pid:
            return p
    return PROVIDERS[0]


# ------------------------------------------------------------------ 配置
def default_config():
    p = PROVIDERS[0]
    return {"provider": p["id"], "proto": p["proto"], "base_url": p["base"],
            "model": p["models"][0] if p["models"] else "", "api_key": "", "timeout": 60}


def _read_root_key():
    """根目录 config.json 里已经有 DeepSeek 的 key，能复用就不让用户再填一遍。"""
    try:
        with open(ROOT_CONFIG, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return {}
    ai = raw.get("ai") if isinstance(raw, dict) else None
    return ai if isinstance(ai, dict) else {}


def load_config(path=LOCAL_CONFIG):
    """本地 ai_config.json 优先，其次根目录 config.json 的 ai 段。"""
    cfg = default_config()
    root = _read_root_key()
    if root:
        pid = str(root.get("provider") or cfg["provider"])
        prov = provider_by_id(pid)
        cfg.update({"provider": pid, "proto": prov["proto"],
                    "base_url": str(root.get("base_url") or prov["base"]),
                    "model": str(root.get("model") or (prov["models"][0] if prov["models"] else "")),
                    "api_key": str(root.get("api_key") or "")})
    try:
        with open(path, encoding="utf-8") as fh:
            local = json.load(fh)
        if isinstance(local, dict):
            cfg.update({k: local[k] for k in cfg if k in local})
    except Exception:
        pass
    return cfg


def save_config(cfg, path=LOCAL_CONFIG):
    data = {k: cfg.get(k) for k in ("provider", "proto", "base_url", "model", "api_key", "timeout")}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


def mask_key(key):
    s = str(key or "")
    return (s[:3] + "***" + s[-3:]) if len(s) > 8 else ("(未填)" if not s else "***")


# ------------------------------------------------------------------ HTTP
def _url(cfg, kind, model=""):
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    proto = cfg.get("proto") or "openai"
    if proto == "gemini":
        if kind == "models":
            return base + "/models"
        m = re.sub(r"^models/", "", str(model or cfg.get("model") or ""))
        return base + "/models/" + urllib.parse.quote(m) + ":generateContent"
    if kind == "models":
        return base + "/models"
    return base + ("/messages" if proto == "anthropic" else "/chat/completions")


def _headers(cfg):
    key = str(cfg.get("api_key") or "")
    proto = cfg.get("proto") or "openai"
    if proto == "anthropic":
        return {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}
    if proto == "gemini":
        return {"Content-Type": "application/json", "x-goog-api-key": key}
    return {"Content-Type": "application/json", "Authorization": "Bearer " + key}


def _body(cfg, system, user, max_tokens):
    proto = cfg.get("proto") or "openai"
    model = str(cfg.get("model") or "")
    if proto == "anthropic":
        return {"model": model, "max_tokens": max_tokens, "temperature": 0, "system": system,
                "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}]}
    if proto == "gemini":
        return {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens}}
    return {"model": model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}


def _text_of(raw, proto):
    proto = proto or "openai"
    if proto == "anthropic":
        return "".join(c.get("text", "") for c in (raw.get("content") or []) if c.get("type") == "text")
    if proto == "gemini":
        cand = (raw.get("candidates") or [{}])[0]
        return "".join(p.get("text", "") for p in ((cand.get("content") or {}).get("parts") or []))
    choice = (raw.get("choices") or [{}])[0]
    return (choice.get("message") or {}).get("content") or ""


def _request(cfg, url, data=None, timeout=None):
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=_headers(cfg),
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or cfg.get("timeout") or 60)) as res:
            return json.loads(res.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise AIError("接口返回 %s：%s" % (e.code, detail))
    except urllib.error.URLError as e:
        raise AIError("网络错误：%s" % getattr(e, "reason", e))
    except Exception as e:
        raise AIError("%s: %s" % (type(e).__name__, e))


def chat(cfg, system, user, max_tokens=4000, timeout=None):
    proto = cfg.get("proto") or "openai"
    url = _url(cfg, "chat")
    raw = _request(cfg, url, _body(cfg, system, user, max_tokens), timeout)
    return _text_of(raw, proto)


def list_models(cfg, timeout=None):
    proto = cfg.get("proto") or "openai"
    raw = _request(cfg, _url(cfg, "models"), None, timeout)
    out = []
    if proto == "gemini":
        for m in raw.get("models") or []:
            out.append(str(m.get("name") or "").replace("models/", ""))
    else:
        for m in raw.get("data") or raw.get("models") or []:
            if isinstance(m, str):
                out.append(m)
            else:
                out.append(str(m.get("id") or m.get("name") or m.get("model") or ""))
    seen, uniq = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return sorted(uniq)


# ------------------------------------------------------------------ 提示词 / 解析
SUBJ_HINT = ("语文 数学 英语 高等数学 大学英语 思想政治 毛泽东思想和中国特色社会主义理论体系概论 思修 "
             "体育 计算机基础 就业指导 心理健康 光伏电池制造技术 电机拖动 PLC应用技术 计算机网络 电子商务基础")


def build_prompt(rows, tasks):
    """rows: [{key,q,t,a,subj}]；tasks: 选中的任务 id 列表。"""
    lines = [
        "你在整理一个「易智教云课堂」的题库。下面每条只有题干和已有答案，**没有选项**。",
        "只做下面勾选的任务；题干里看不出来的，就留空，不要猜、不要编造。",
        "",
    ]
    if "subj" in tasks:
        lines.append("任务1 补科目：给每条一个科目名 subj。优先从这些里选：")
        lines.append("  " + SUBJ_HINT)
        lines.append("  都不像就给「专业课」。实在判断不了给空字符串。")
    if "junk" in tasks:
        lines.append("任务2 标无效题：如果题干是老师布置的开放作业/上传题（例如「本节课你学会了什么」"
                     "「请拍照上传」「谈谈你的感想」），junk 给 true，并写 junk_reason；否则 false。")
    if "dupe" in tasks:
        lines.append("任务3 找近似重复：本批里如果有两条其实是同一道题（只是标点/空格/表述略有不同），"
                     "把信息较少的那条 dupe_of 填成另一条的 key（只填 key，不要填别的）。")
    lines += [
        "",
        "严格输出一个 JSON 对象，不要 Markdown 代码块、不要解释：",
        '{"items":[{"key":"原样抄","subj":"","junk":false,"junk_reason":"",'
        '"dupe_of":"","confidence":0.9,"why":"不超过8个字"}]}',
        "items 必须和下面题目一一对应、顺序一致，key 原样照抄。",
        "",
        "题目：",
    ]
    for i, r in enumerate(rows, 1):
        q = str(r.get("q") or "").replace("\n", " ").strip()
        if len(q) > 300:
            q = q[:300] + "…"
        lines.append("%d. key=%s 题型=%s 答案=%s 现有科目=%s\n   题干：%s"
                     % (i, r.get("key"), r.get("t", ""), r.get("a", ""), r.get("subj", "") or "（空）", q))
    return "\n".join(lines)


def extract_json(text):
    s = str(text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(s[a:b + 1])
    except Exception:
        return None


def clean_subj(s):
    """科目名要短、干净；不像科目的直接丢掉。"""
    s = re.sub(r"\s+", "", str(s or ""))
    s = s.strip("，。；;、:：\"'「」")
    if not s or len(s) > 20:
        return ""
    if re.search(r"[。？！!?]", s):
        return ""
    if s in ("未知", "不确定", "无法判断", "其他", "未知科目"):
        return ""
    return s


def parse_reply(text, rows):
    """把模型回复变成提案；非法/越界的丢掉。返回 (proposals, errors)。"""
    data = extract_json(text)
    errors = []
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return [], ["模型没有返回约定的 JSON"]
    by_key = {r["key"]: r for r in rows}
    items = data.get("items") or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "").strip()
        row = by_key.get(key)
        if not row:
            errors.append("模型提到了不存在的 key：%s" % key[:40])
            continue
        why = str(it.get("why") or "").strip()[:12]
        try:
            conf = float(it.get("confidence"))
        except Exception:
            conf = 0.0
        if conf < 0 or conf > 1:
            conf = 0.0
        subj = clean_subj(it.get("subj"))
        if subj and subj != str(row.get("subj") or "").strip():
            out.append({"kind": "subj", "key": key, "old": row.get("subj") or "", "new": subj,
                        "why": why, "confidence": conf})
        if it.get("junk") is True:
            out.append({"kind": "junk", "key": key, "old": row.get("q", "")[:60], "new": "",
                        "why": str(it.get("junk_reason") or why)[:20], "confidence": conf})
        other = str(it.get("dupe_of") or "").strip()
        if other and other != key and other in by_key:
            keep = row if len(str(row.get("q") or "")) >= len(str(by_key[other].get("q") or "")) else by_key[other]
            drop = other if keep["key"] == key else key
            out.append({"kind": "dupe", "key": drop, "old": by_key[drop].get("q", "")[:60], "new": "保留 " + keep["key"],
                        "why": "同一题", "confidence": conf, "keep": keep["key"]})
    # 同一条目被标成重复多次时只留一条
    seen, uniq = set(), []
    for p in out:
        sig = (p["kind"], p["key"])
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(p)
    return uniq, errors


def candidates(bank_rows, max_items=200):
    """只挑有题干的条目 —— 图片键（i/d/p）没有文字，问了也白问。"""
    rows = [r for r in bank_rows if str(r.get("q") or "").strip()]
    rows.sort(key=lambda r: -int(r.get("at") or 0))
    return rows[:max(1, int(max_items or 200))]


def analyze(rows, cfg, tasks, batch_size=20, on_progress=None, chat_fn=None):
    """分批问模型；chat_fn 可注入（单测用假模型，不联网）。

    返回 (proposals, errors)。任何一批失败都只记错误，不影响别的批。
    """
    chat_fn = chat_fn or (lambda system, user, max_tokens: chat(cfg, system, user, max_tokens))
    size = max(1, min(50, int(batch_size or 20)))
    props, errors = [], []
    total = len(rows)
    system = ("你是严格的题库整理程序。只输出 JSON，不要解释。看不清就留空，"
              "宁可少做，也不要把错的东西写进题库。")
    for start in range(0, total, size):
        part = rows[start:start + size]
        if on_progress:
            on_progress(start + 1, min(total, start + size), total)
        try:
            reply = chat_fn(system, build_prompt(part, tasks), 3000)
        except Exception as e:
            errors.append("第 %d–%d 条：%s" % (start + 1, min(total, start + size), e))
            continue
        got, errs = parse_reply(reply, part)
        props.extend(got)
        errors.extend(errs)
        time.sleep(0)                     # 给 GUI 线程一点呼吸（也方便测试替换）
    return props, errors


def apply_proposals(bank, proposals, add_tag=True):
    """把提案写进题库副本；返回 (new_bank, stats)。

    subj  → 写字段（src 打 +AI 标记）
    junk / dupe → 删除该 key（调用方负责二次确认 + 撤销）
    """
    out = dict(bank)
    stats = {"subj": 0, "deleted": 0}
    for p in proposals:
        k = str(p.get("key") or "")
        if k not in out or k.startswith("__"):
            continue
        if p["kind"] == "subj":
            item = dict(out[k])
            item["subj"] = p["new"]
            if add_tag:
                src = str(item.get("src") or "")
                if "+AI" not in src:
                    item["src"] = (src + "+AI").lstrip("+")
            out[k] = item
            stats["subj"] += 1
        elif p["kind"] in ("junk", "dupe"):
            out.pop(k, None)
            stats["deleted"] += 1
    meta = dict(out.get("__meta__") or {})
    meta["count"] = sum(1 for k in out if not str(k).startswith("__"))
    meta["exportedAt"] = int(time.time() * 1000)
    out["__meta__"] = meta
    return out, stats
