#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题库按「年级 × 科目」归并分流 —— 命令行版（只用标准库）。

和题库编辑器里的「批量归并」是同一套逻辑（都调 bank_core），
区别是这个能写进脚本 / 定时任务里，稳定复现。

用法：
    python bank_split_cli.py <题库文件或文件夹> [...] [选项]

常用：
    python bank_split_cli.py C:\\Users\\work\\Desktop\\yzj-bank-2026-10-08.json -o D:\\题库
    python bank_split_cli.py D:\\导出\\ -o D:\\题库 --js --min 2
    python bank_split_cli.py D:\\导出\\ --dry-run          # 只看会分成什么

选项：
    -o, --out DIR        输出目录（默认：第一个输入旁边的 题库分流-<时间戳>）
    --js                 同时输出 .js 包装文件
    --no-guess           不许拿题干猜科目（老题库基本就全进「未分类」）
    --public "语文,数学"  覆盖公共课关键词（默认用 core.DEFAULT_PUBLIC_KEYWORDS）
    --major  "电动机,PLC" 专业课关键词（可选；留空时有课程名的非公共课都算专业课）
    --policy new|old|skip 多个库撞同一个 key 时怎么办（默认 new = 后加的赢）
    --min N              少于 N 条的组不单独出文件（默认 1）
    --dry-run            只打印，不写文件
    --quiet              只出最后一行汇总
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bank_core as core  # noqa: E402


def collect(paths):
    """把「文件或文件夹」列表摊成 json 文件列表（文件夹递归，跳过 .bak / 隐藏）。"""
    out, seen = [], set()
    for p in paths:
        cand = []
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fn in files:
                    if fn.lower().endswith(".json") and ".bak" not in fn.lower():
                        cand.append(os.path.join(root, fn))
        elif os.path.isfile(p):
            cand.append(p)
        else:
            sys.stderr.write("跳过（找不到）：%s\n" % p)
        for c in cand:
            a = os.path.abspath(c)
            if a not in seen:
                seen.add(a)
                out.append(a)
    return out


def plan_text(groups, stats, min_count):
    kept = sorted(((k, len(v)) for k, v in groups.items() if len(v) >= min_count),
                  key=lambda kv: (-kv[1], kv[0]))
    dropped = [k for k, v in groups.items() if len(v) < min_count]
    lines = []
    for name, n in kept:
        lines.append("  %-18s %5d 条" % (name, n))
    if dropped:
        lines.append("  （不足 %d 条没出：%s）" % (min_count, "、".join(sorted(dropped))))
    years = "、".join("%s %d" % (k, v) for k, v in sorted(stats["by_year"].items()))
    subjs = "、".join("%s %d" % (k, v) for k, v in sorted(stats["by_subject"].items(),
                                                          key=lambda kv: -kv[1]))
    lines.append("  合计 %d 条；年级分布 %s；科目分布 %s" % (stats["total"], years, subjs))
    lines.append("  其中靠题干猜的 %d 条、年级未知 %d 条、没科目 %d 条"
                 % (stats["guessed"], stats["unknown_year"], stats["no_subject"]))
    return "\n".join(lines), kept, dropped


def js_text(name, sub, app="易智教 · 题库编辑器"):
    return (
        "// 由「%s」导出 —— %s（%d 条，%s）\n"
        "// Node：const bank = require('./这个文件')\n"
        "// 网页 <script src>：加载后挂在 window.__YZJ_BANK__\n"
        "// 并进「易智教助手」题库：GM_setValue('answerBank', Object.assign(GM_getValue('answerBank', {}) || {}, bank));\n"
        "const bank = %s;\n"
        "if (typeof module !== 'undefined' && module.exports) module.exports = bank;\n"
        "if (typeof window !== 'undefined') window.__YZJ_BANK__ = bank;\n"
        % (app, name, len(core.entries(sub)), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
          json.dumps(sub, ensure_ascii=False, indent=1))
    )


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True, description="题库按「年级 × 科目」归并分流")
    ap.add_argument("inputs", nargs="+", help="题库 json 或文件夹（可多个）")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--js", action="store_true")
    ap.add_argument("--no-guess", action="store_true")
    ap.add_argument("--public", default="")
    ap.add_argument("--major", default="")
    ap.add_argument("--policy", default="new", choices=["new", "old", "skip"])
    ap.add_argument("--min", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    files = collect(a.inputs)
    if not files:
        sys.stderr.write("没有可读的题库文件。\n")
        return 2

    banks, bad = [], []
    from collections import Counter
    where = Counter()
    for p in files:
        try:
            b = core.load_bank(p)
        except Exception as e:
            bad.append("%s（%s）" % (os.path.basename(p), e))
            continue
        where[os.path.dirname(p)] += len(core.entries(b))
        banks.append(b)

    if not banks:
        sys.stderr.write("全部读不了：\n  " + "\n  ".join(bad) + "\n")
        return 2

    merged, mstat = core.merge(banks, policy=a.policy)
    pub = core.parse_keywords(a.public) or core.DEFAULT_PUBLIC_KEYWORDS
    maj = core.parse_keywords(a.major)
    groups, stats = core.group_year_subject(merged, pub, maj, guess=not a.no_guess)
    summary, kept, dropped = plan_text(groups, stats, max(1, a.min))

    if not a.quiet:
        print("读了 %d 个文件（合并 %d 条：新增 %d / 覆盖 %d / 跳过 %d）"
              % (mstat["files"], len(core.entries(merged)), mstat["added"],
                 mstat["updated"], mstat["skipped"]))
        if len(where) > 1:
            print("来源目录：" + "；".join("%s %d 条" % (d, n) for d, n in where.most_common()))
        if bad:
            print("读不了：" + "、".join(bad))
        print("会分成 %d 份：" % len(kept))
        print(summary)

    if a.dry_run:
        print("（--dry-run：没写任何文件）")
        return 0

    outdir = a.out or os.path.join(
        os.path.dirname(files[0]) or os.getcwd(),
        "题库分流-%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M"))
    os.makedirs(outdir, exist_ok=True)
    parts = {}
    for name, _n in kept:
        rows = groups[name]
        sub = {r["key"]: merged[r["key"]] for r in rows}
        sub["__meta__"] = core.make_meta(sub, merged.get("__meta__"))
        parts[name] = sub

    base = "yzj-bank"
    made = []
    for name, sub in parts.items():
        safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip() or "未命名"
        jp = os.path.join(outdir, "%s__%s.json" % (base, safe))
        core.save_bank(jp, sub, backup=False)
        made.append((os.path.basename(jp), len(core.entries(sub))))
        if a.js:
            with open(os.path.join(outdir, "%s__%s.js" % (base, safe)), "w", encoding="utf-8") as fh:
                fh.write(js_text(name, sub))

    if not a.quiet:
        for fn, n in sorted(made, key=lambda x: -x[1]):
            print("  写出 %s（%d 条）" % (fn, n))
    print("完成：%d 份 → %s" % (len(made), outdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
