#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bank_core.py —— 题库文件（易智教助手导出的 JSON）的纯逻辑，不含界面。

题库文件长这样：

    {
      "__meta__": { "app": "yzj-csgmooc-helper", "count": 12, "exportedAt": 1791... },
      "q1a2b3c": { "a": "ABD", "q": "交换机接口模式有哪些？", "t": 1, "at": 1791..., "src": "learn" },
      "p10bo642": { "a": "1C#2A", "at": 1791..., "src": "official" }
    }

  - key  ：指纹。`q…`=按题干、`i…`/`d…`=按图片哈希、`p…`=按卷子+题号
  - a    ：答案（多选 ABD、填空 a#CSG#b、连线 1C#2A#3G、简答是一段 HTML/正文）
  - q/t  ：题干（≤100 字）和题型编号（0单选 1多选 2判断 3填空 4主观 5视听 6连线 7排序）
  - at   ：写入时间（毫秒）
  - src  ：来源（learn=交卷自学习、official=官方判定、import=导入、test=测试）

这里只做「读 → 改 → 写」的纯函数，GUI 和单测都调它。
"""

import json
import os
import re
import time
import csv

TYPE_NAME = {0: "单选", 1: "多选", 2: "判断", 3: "填空", 4: "主观/问答", 5: "视听/代码", 6: "连线", 7: "排序"}
KEY_KIND = {"q": "题干", "i": "图片哈希", "d": "图片近似", "p": "卷子题号"}


def type_name(t):
    if t is None or t == "":
        return "未标注"
    try:
        return TYPE_NAME.get(int(t), "其他(%s)" % t)
    except (TypeError, ValueError):
        return str(t)


def key_kind(k):
    return KEY_KIND.get(str(k)[:1], "其他")


# ------------------------------------------------------------------ 读 / 写
def load_bank(path):
    """读题库；文件不存在/坏掉时抛异常（由调用方提示）。"""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("这不是题库文件（顶层不是对象）")
    return data


def entries(bank):
    """摊平成条目列表：[{key, a, q, t, at, src, subj, cls, stu, who}]，按写入时间倒序。

    subj/cls/stu/who 是 v2.2 的来源标注（哪门课 / 哪个班 / 哪个账号收的）；
    v1.4.7 之前入库的老条目没有这几个字段，取到的是空串。
    """
    rows = []
    for k, v in (bank or {}).items():
        if str(k).startswith("__") or not isinstance(v, dict):
            continue
        rows.append({"key": k, "a": v.get("a", ""), "q": v.get("q", ""), "t": v.get("t"),
                     "at": v.get("at") or 0, "src": v.get("src", ""),
                     "subj": v.get("subj", ""), "cls": v.get("cls", ""),
                     "stu": v.get("stu", ""), "who": v.get("who", "")})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


def make_meta(bank, source_meta=None):
    """重算 __meta__（条数 / 最后更新时间 / 导出时间）。"""
    rows = entries(bank)
    latest = max([r["at"] for r in rows] or [0])
    meta = dict(source_meta or {})
    meta.update({
        "app": meta.get("app") or "yzj-csgmooc-helper",
        "count": len(rows),
        "latestAt": latest,
        "exportedAt": int(time.time() * 1000),
    })
    return meta


def save_bank(path, bank, backup=True):
    """写回文件；默认先备份成 <名字>.<时间戳>.bak。返回备份路径（没备份返回 None）。"""
    bak = None
    if backup and os.path.exists(path):
        bak = "%s.%s.bak" % (path, time.strftime("%Y%m%d-%H%M%S"))
        with open(bak, "w", encoding="utf-8") as fh:
            json.dump(load_bank(path), fh, ensure_ascii=False, indent=1)
    out = dict(bank)
    out["__meta__"] = make_meta(bank, (bank or {}).get("__meta__"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return bak


# ------------------------------------------------------------------ 查 / 选
def search_text(entry):
    """搜索覆盖：题干 / 答案 / key / 科目 / 班级 / 学号 / 姓名（搜"光伏"或学号都能命中）。"""
    return " ".join([str(entry.get("q") or ""), str(entry.get("a") or ""), str(entry.get("key") or ""),
                     str(entry.get("subj") or ""), str(entry.get("cls") or ""),
                     str(entry.get("stu") or ""), str(entry.get("who") or "")]).lower()


def filter_entries(rows, query="", t_filter=None, src_filter=None):
    """query 关键词（题干/答案/key，空格分隔全部命中）；t_filter 题型编号或 None。"""
    kws = [w for w in str(query or "").lower().split() if w]
    out = []
    for r in rows:
        text = search_text(r)
        if kws and not all(w in text for w in kws):
            continue
        if t_filter is not None and str(r.get("t")) != str(t_filter):
            continue
        if src_filter and src_filter != "全部" and (r.get("src") or "") != src_filter:
            continue
        out.append(r)
    return out


def subset(bank, keys):
    """挑出若干 key，组成一个新题库（带自己的 __meta__）。"""
    keep = set(str(k) for k in keys)
    out = {k: v for k, v in (bank or {}).items() if str(k) in keep}
    out["__meta__"] = make_meta(out, (bank or {}).get("__meta__"))
    return out


def delete_keys(bank, keys):
    """删掉若干 key，返回 (新题库, 删了几条)。"""
    drop = set(str(k) for k in keys)
    out = {k: v for k, v in (bank or {}).items() if str(k) not in drop}
    out["__meta__"] = make_meta(out, (bank or {}).get("__meta__"))
    return out, sum(1 for k in (bank or {}) if str(k) in drop)


def set_answer(bank, key, answer):
    out = dict(bank)
    if str(key) not in out:
        raise KeyError(key)
    item = dict(out[str(key)])
    item["a"] = str(answer)
    item["src"] = (str(item.get("src") or "") + "+手动").lstrip("+")
    out[str(key)] = item
    out["__meta__"] = make_meta(out, out.get("__meta__"))
    return out


# ------------------------------------------------------------------ 合并 / 拆分 / 重组
def merge(banks, policy="new"):
    """合并多个题库。

    policy: new   = 同 key 用后一个（新）的覆盖（默认）
            old   = 同 key 保留已有的（先来的赢）
            skip  = 同 key 冲突就跳过
    返回 (合并后的题库, 统计)
    """
    out = {}
    stats = {"files": len(banks), "added": 0, "updated": 0, "skipped": 0}
    for b in banks:
        for k, v in (b or {}).items():
            if str(k).startswith("__") or not isinstance(v, dict):
                continue
            if k in out:
                if policy == "old":
                    stats["skipped"] += 1
                    continue
                if policy == "skip":
                    stats["skipped"] += 1
                    continue
                out[k] = v
                stats["updated"] += 1
            else:
                out[k] = v
                stats["added"] += 1
    out["__meta__"] = make_meta(out)
    return out, stats


def split(bank, mode="type", size=100, keyword=""):
    """拆分题库，返回 {分片名: 题库}。

    mode: type    按题型
          size    每 N 条一片
          keyword 题干里含关键词的 / 不含的，两片
          src     按来源
    """
    rows = entries(bank)
    groups = {}
    if mode == "size":
        n = max(1, int(size))
        for i in range(0, len(rows), n):
            groups["第%d片_%d-%d" % (i // n + 1, i + 1, min(i + n, len(rows)))] = rows[i:i + n]
    elif mode == "keyword":
        kw = str(keyword or "").strip()
        hit = [r for r in rows if kw and kw.lower() in search_text(r)] if kw else []
        miss = [r for r in rows if r not in hit]
        if hit:
            groups["含_%s" % kw] = hit
        groups["不含_%s" % kw] = miss
    elif mode == "src":
        for r in rows:
            groups.setdefault(r.get("src") or "未标注", []).append(r)
    else:  # type
        for r in rows:
            groups.setdefault(type_name(r.get("t")), []).append(r)
    out = {}
    for name, rs in groups.items():
        sub = {r["key"]: bank[r["key"]] for r in rs}
        sub["__meta__"] = make_meta(sub, bank.get("__meta__"))
        out[name] = sub
    return out


def split_groups(bank, groups, rest_name="未归类", only_with_question=False):
    """按**多组关键词**拆（一次分出好几个"班/科目"）。

    groups: [("电气控制班", ["电动机","起动","继电器"]), ("光伏班", ["制绒","扩散","硅片"])]
    一道题只进**第一个命中**的组；都没命中 → rest_name 那一组。
    only_with_question=True 时，没有题干的条目一律进未归类（它们判不了内容）。
    返回 {组名: 题库}
    """
    rows = entries(bank)
    buckets = {name: [] for name, _ in groups}
    buckets[rest_name] = []
    for r in rows:
        q = str(r.get("q") or "")
        hit = None
        if q or not only_with_question:
            for name, kws in groups:
                if any(str(w) and str(w).lower() in q.lower() for w in kws):
                    hit = name
                    break
        buckets[hit or rest_name].append(r)
    out = {}
    for name, rs in buckets.items():
        if not rs and name == rest_name:
            continue
        sub = {r["key"]: bank[r["key"]] for r in rs}
        sub["__meta__"] = make_meta(sub, bank.get("__meta__"))
        out[name] = sub
    return out


# ------------------------------------------------------------------ 按「年级 × 科目」分流（v2.3）
# 题库条目里从 v1.4.7 起会带：cls（班级名，如「25级05班」）、subj（课程名，如「光伏电池制造技术」）。
# 年级直接从 cls 抽；科目要靠词典（可编辑）——有 subj 时按课程名判，没有就只能拿题干猜（并标记出来）。
YEAR_RE = re.compile(r"(\d{2}|\d{4})\s*级")

DEFAULT_PUBLIC_KEYWORDS = ["语文", "数学", "英语", "高数", "高等数学", "大学英语", "思政", "思想政治",
                           "毛泽东", "思修", "职业道德", "体育", "计算机基础", "就业指导"]


def year_of(entry):
    """年级：优先班级名(cls)，其次课程名、题干。『25级05班』→『25级』；抽不到 →『年级未知』。"""
    for v in (entry.get("cls"), entry.get("subj"), entry.get("q")):
        m = YEAR_RE.search(str(v or ""))
        if m:
            return m.group(1) + "级"
    return "年级未知"


def parse_keywords(text):
    """把『语文,数学 英语』这种一行文本切成一串关键词（逗号/顿号/空格都能当分隔）。"""
    return [w.strip() for w in re.split(r"[,，、;；\s]+", str(text or "")) if w.strip()]


def subject_of(entry, public_kws=None, major_kws=None, guess=True):
    """判科目，返回 (类别, 依据)。依据：『标注』= 有条目自带课程名可依；『猜』= 只能从题干猜；『无』= 判不了。"""
    public_kws = DEFAULT_PUBLIC_KEYWORDS if public_kws is None else public_kws
    major_kws = [] if major_kws is None else major_kws
    subj = str(entry.get("subj") or "").strip()
    if subj:
        if any(k and k in subj for k in public_kws):
            return "语数外", "标注"
        if any(k and k in subj for k in major_kws):
            return "专业课", "标注"
        return "专业课", "标注"          # 有具体课程名、又不在公共课表里 → 当成专业课
    if guess:
        q = str(entry.get("q") or "")
        if q:
            if any(k and k in q for k in public_kws):
                return "语数外", "猜"
            if any(k and k in q for k in major_kws):
                return "专业课", "猜"
    return "未分类", "无"


def group_year_subject(bank, public_kws=None, major_kws=None, guess=True):
    """按『年级 × 科目』分组，给编辑器做预览用。

    返回 (groups, stats)：
      groups = {"25级-语数外": [row, ...], "年级未知-未分类": [...]}
      stats  = {"total", "by_year", "by_subject", "guessed", "unknown_year", "no_subject"}
    """
    rows = entries(bank)
    groups, by_year, by_subject = {}, {}, {}
    guessed = unknown_year = no_subject = 0
    for r in rows:
        y = year_of(r)
        s, how = subject_of(r, public_kws, major_kws, guess)
        groups.setdefault("%s-%s" % (y, s), []).append(r)
        by_year[y] = by_year.get(y, 0) + 1
        by_subject[s] = by_subject.get(s, 0) + 1
        if how == "猜":
            guessed += 1
        if how == "无":
            no_subject += 1
        if y == "年级未知":
            unknown_year += 1
    stats = {"total": len(rows), "by_year": by_year, "by_subject": by_subject,
             "guessed": guessed, "unknown_year": unknown_year, "no_subject": no_subject}
    return groups, stats


def split_by_year_subject(bank, public_kws=None, major_kws=None, guess=True):
    """按『年级 × 科目』拆成多份题库。组名形如『25级-语数外』『24级-专业课』。

    注意：老数据（v1.4.7 之前采集的）没有班级/课程名，会大量落进『年级未知』『未分类』——
    这不是 bug，是数据里确实没有这个信息。
    """
    groups, _stats = group_year_subject(bank, public_kws, major_kws, guess)
    out = {}
    for name, rs in groups.items():
        sub = {r["key"]: bank[r["key"]] for r in rs}
        sub["__meta__"] = make_meta(sub, bank.get("__meta__"))
        out[name] = sub
    return out


def split_by_paper(bank):
    """按**整卷图片**拆：`i<hash>@题号` / `d<hash>@题号` 各自按哈希聚成一组。

    同一张试卷的题会落在同一组里（i… 和 d… 是同一张图的两种指纹，会各成一组）。
    没有指纹的（`p…` 这种只有卷子哈希的）进「未归类」。
    返回 {组名: 题库}，组名形如「i5a5e7776_4题」。
    """
    rows = entries(bank)
    buckets = {}
    for r in rows:
        k = str(r["key"])
        if k[:1] in ("i", "d") and "@" in k:
            head = k.split("@")[0]
            buckets.setdefault(head, []).append(r)
        else:
            buckets.setdefault("未归类", []).append(r)
    out = {}
    for head, rs in buckets.items():
        name = head if head == "未归类" else "%s_%d题" % (head, len(rs))
        sub = {r["key"]: bank[r["key"]] for r in rs}
        sub["__meta__"] = make_meta(sub, bank.get("__meta__"))
        out[name] = sub
    return out


def normalize_question(q):
    """题干归一化：去标签、去空白、去标点，用于判断"是不是同一道题"。"""
    s = str(q or "")
    s = re.sub(r"<[^>]*>", " ", s)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[，。、！？；：“”‘’（）《》,.!?;:\"'()\[\]{}]", "", s)
    return s.lower()


def consolidate(bank, prefer_src=("official", "learn")):
    """整理（重组）：把指向同一道题的多个 key 合并成一条。

    只在**有题干**的条目之间合并（图片指纹那种没题干，不能瞎并）。
    同一题里优先级：prefer_src 里靠前的来源 > 写入时间更新的。
    保留下来的 key 用其中最短的 q… key（最通用：换班级/换账号都能命中）。
    返回 (新题库, 统计)
    """
    rows = entries(bank)
    groups = {}
    for r in rows:
        nq = normalize_question(r.get("q"))
        if len(nq) < 6:                       # 题干太短（或没有）→ 不参与合并
            groups.setdefault("__alone__" + r["key"], [r])
            continue
        groups.setdefault(nq, []).append(r)

    def rank(r):
        src = str(r.get("src") or "")
        pr = 99
        for i, s in enumerate(prefer_src):
            if src.startswith(s):
                pr = i
                break
        return (pr, -int(r.get("at") or 0))

    out = {}
    stats = {"groups": 0, "merged": 0, "kept": 0}
    for name, rs in groups.items():
        best = sorted(rs, key=rank)[0]
        q_keys = [r["key"] for r in rs if str(r["key"]).startswith("q")]
        keep_key = min(q_keys, key=len) if q_keys else best["key"]
        item = dict(bank[best["key"]])
        out[keep_key] = item
        stats["kept"] += 1
        if len(rs) > 1:
            stats["groups"] += 1
            stats["merged"] += len(rs) - 1
    out["__meta__"] = make_meta(out, bank.get("__meta__"))
    return out, stats


def health(bank):
    """体检：条数 / 题型分布 / 科目分布 / 缺题干 / 缺答案 / 同一题多 key。"""
    rows = entries(bank)
    by_type = {}
    by_subj = {}
    for r in rows:
        by_type[type_name(r.get("t"))] = by_type.get(type_name(r.get("t")), 0) + 1
        s = str(r.get("subj") or "").strip() or "（没标科目）"
        by_subj[s] = by_subj.get(s, 0) + 1
    dup = {}
    for r in rows:
        nq = normalize_question(r.get("q"))
        if len(nq) >= 6:
            dup.setdefault(nq, []).append(r["key"])
    return {
        "total": len(rows),
        "by_type": by_type,
        "by_subject": by_subj,
        "no_tag": sum(1 for r in rows if not (r.get("subj") or r.get("stu"))),
        "no_question": sum(1 for r in rows if not r.get("q")),
        "no_answer": sum(1 for r in rows if not str(r.get("a") or "").strip()),
        "dup_questions": sum(1 for v in dup.values() if len(v) > 1),
        "dup_keys": sum(len(v) - 1 for v in dup.values() if len(v) > 1),
        "latest": max([r["at"] for r in rows] or [0]),
    }


# ------------------------------------------------------------------ Excel 门路
# v2.2 起 CSV 多三列：这题是从哪门课、哪个班、哪个学号收的
CSV_HEAD = ["key", "题型", "题干", "答案", "写入时间", "来源", "科目", "班级", "学号"]


def to_csv(bank, path):
    """导出成 Excel 能直接打开的 CSV（带 BOM，中文不乱码）。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEAD)
        for r in entries(bank):
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["at"] / 1000)) if r["at"] else ""
            w.writerow([r["key"], type_name(r["t"]), r["q"], r["a"], when, r["src"],
                        r["subj"], r["cls"], r["stu"]])
    return path


def from_csv(bank, path):
    """把 CSV 里改过的「答案」写回题库（按 key 匹配，其它列只读）。

    返回 (新题库, 统计)：changed=答案变了的条数，missing=CSV 里有但题库里没有的 key。
    """
    out = dict(bank)
    changed = 0
    missing = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            k = (row.get("key") or "").strip()
            if not k:
                continue
            if k not in out:
                missing.append(k)
                continue
            new = (row.get("答案") or "").strip()
            if new and new != str(out[k].get("a") or ""):
                item = dict(out[k])
                item["a"] = new
                item["src"] = (str(item.get("src") or "") + "+csv").lstrip("+")
                out[k] = item
                changed += 1
    out["__meta__"] = make_meta(out, out.get("__meta__"))
    return out, {"changed": changed, "missing": missing}


