#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bank_editor 的单测 —— 全离线，不弹任何窗口、不碰真题库。

    python quiz\\bank_editor\\test_bank_core.py

覆盖：读/写(带备份)、搜索过滤、导出选中、删除、改答案、
合并三种冲突策略、拆分四种方式、整理(同题多 key 合并)、体检，
最后加一条界面冒烟（窗口 withdraw 掉，只验证表格装配）。
"""

import json
import os
import re
import shutil
import sys
import tempfile

try:                       # GBK 控制台里 print ✅ 会 UnicodeEncodeError，先兜住
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bank_core as core  # noqa: E402
import bank_ai  # noqa: E402

RESULTS = []


def check(name, ok, extra=""):
    RESULTS.append((name, bool(ok)))
    print("%s %s%s" % ("✅" if ok else "❌", name, ("  " + extra) if extra else ""))


def sample():
    """构造一份贴近真实结构的题库：同一道多选有 q/p 两把钥匙，另有一条图片题。"""
    return {
        "__meta__": {"app": "yzj-csgmooc-helper", "count": 4, "exportedAt": 1791000000000},
        "qaaa111": {"a": "ABD", "q": "交换机接口模式有哪些？", "t": 1, "at": 1790000000000, "src": "learn", "subj": "计算机网络", "cls": "25级05班", "stu": "2024130038", "who": "测试同学"},
        "pbbb222": {"a": "ABD", "q": "交换机接口模式有哪些？", "t": 1, "at": 1790500000000, "src": "official"},
        "qccc333": {"a": "A", "q": "什么是VLAN？", "t": 0, "at": 1790600000000, "src": "learn", "subj": "光伏电池制造技术", "cls": "25级05班", "stu": "2024130038", "who": "测试同学"},
        "pddd444": {"a": "B", "at": 1790700000000, "src": "official"},
    }


TMP = tempfile.mkdtemp(prefix="banktest_")


def test_read_write_backup():
    bank = sample()
    p = os.path.join(TMP, "b.json")
    core.save_bank(p, bank, backup=False)
    loaded = core.load_bank(p)
    check("写入后再读回来条数一致", len(core.entries(loaded)) == 4, str(len(core.entries(loaded))))
    check("__meta__ 重算：count/latestAt/exportedAt 都在",
          loaded["__meta__"]["count"] == 4 and loaded["__meta__"]["latestAt"] == 1790700000000
          and loaded["__meta__"]["exportedAt"] > 0, json.dumps(loaded["__meta__"])[:90])
    bak = core.save_bank(p, loaded, backup=True)
    check("保存会先备份成 .bak", bool(bak) and os.path.exists(bak), os.path.basename(bak or ""))


def test_entries_and_filter():
    rows = core.entries(sample())
    check("entries 按写入时间倒序、过滤掉 __meta__", len(rows) == 4 and rows[0]["key"] == "pddd444",
          rows[0]["key"])
    check("搜索：题干关键词", len(core.filter_entries(rows, "vlan")) == 1, "")
    check("搜索：多关键词要全中", len(core.filter_entries(rows, "交换机 接口")) == 2, "")
    check("筛选：按题型", len(core.filter_entries(rows, t_filter=1)) == 2, "")
    check("筛选：按来源", len(core.filter_entries(rows, src_filter="official")) == 2, "")
    check("来源标注：entries 带出科目/班级/学号",
          [r for r in rows if r["key"] == "qaaa111"][0]["subj"] == "计算机网络"
          and [r for r in rows if r["key"] == "qaaa111"][0]["stu"] == "2024130038", "")
    check("来源标注：没标的老条目是空串，不报错",
          [r for r in rows if r["key"] == "pddd444"][0]["subj"] == "", "")
    check("搜索：科目关键词也能命中（光伏）", len(core.filter_entries(rows, "光伏")) == 1, "")
    check("搜索：学号也能命中", len(core.filter_entries(rows, "2024130038")) == 2, "")
    check("题型名映射", core.type_name(1) == "多选" and core.type_name(None) == "未标注", "")


def test_subset_delete_set():
    bank = sample()
    sub = core.subset(bank, ["qaaa111", "qccc333"])
    check("导出选中：只留选中的，且带自己的 __meta__",
          len(core.entries(sub)) == 2 and sub["__meta__"]["count"] == 2, str(sorted(k for k in sub if k != "__meta__")))
    after, n = core.delete_keys(bank, ["pddd444", "不存在的key"])
    check("删除：只删存在的，返回真实条数",
          n == 1 and len(core.entries(after)) == 3, "删了 %d" % n)
    changed = core.set_answer(bank, "qccc333", "ABCD")
    check("改答案：答案变了、来源打了 +手动 标记",
          changed["qccc333"]["a"] == "ABCD" and "手动" in changed["qccc333"]["src"], changed["qccc333"]["src"])


def test_merge():
    a = sample()
    b = {"qaaa111": {"a": "ACD", "q": "交换机接口模式有哪些？", "t": 1, "at": 1790900000000, "src": "import"},
         "qeee555": {"a": "B", "q": "新题", "t": 0, "at": 1790900001000, "src": "import"}}
    m_new, s_new = core.merge([a, b], policy="new")
    check("合并(新覆盖)：同 key 用后来的，新题也进来",
          m_new["qaaa111"]["a"] == "ACD" and "qeee555" in m_new
          and s_new["added"] == 5 and s_new["updated"] == 1, json.dumps(s_new))
    m_old, s_old = core.merge([a, b], policy="old")
    check("合并(保留已有)：同 key 不动", m_old["qaaa111"]["a"] == "ABD" and s_old["skipped"] == 1, json.dumps(s_old))
    m_skip, s_skip = core.merge([a, b], policy="skip")
    check("合并(冲突跳过)：结果同 old", m_skip["qaaa111"]["a"] == "ABD" and s_skip["skipped"] == 1, json.dumps(s_skip))


def test_split():
    bank = sample()
    by_type = core.split(bank, mode="type")
    check("拆分：按题型", set(by_type.keys()) == {"多选", "单选", "未标注"},
          str({k: len(core.entries(v)) for k, v in by_type.items()}))
    by_size = core.split(bank, mode="size", size=3)
    check("拆分：每 3 条一片 → 2 片", len(by_size) == 2
          and sum(len(core.entries(v)) for v in by_size.values()) == 4, str(list(by_size)))
    by_kw = core.split(bank, mode="keyword", keyword="VLAN")
    check("拆分：按关键词（含/不含）",
          "含_VLAN" in by_kw and len(core.entries(by_kw["含_VLAN"])) == 1, str(list(by_kw)))
    by_src = core.split(bank, mode="src")
    check("拆分：按来源", set(by_src.keys()) == {"learn", "official"}, str(list(by_src)))


def test_consolidate_and_health():
    bank = sample()
    h = core.health(bank)
    check("体检：统计同题多 key", h["dup_questions"] == 1 and h["dup_keys"] == 1 and h["total"] == 4,
          json.dumps(h, ensure_ascii=False)[:110])
    out, stats = core.consolidate(bank)
    check("整理：同题两组 key 合并成一条（4 → 3）",
          len(core.entries(out)) == 3 and stats["merged"] == 1, json.dumps(stats))
    check("整理：同题里优先 official，且保留 q… 钥匙",
          "qaaa111" in out and "pbbb222" not in out and out["qaaa111"]["src"] == "official",
          str(sorted(k for k in out if k != "__meta__")))
    check("整理：没题干的图片题不参与合并，原样保留", "pddd444" in out, "")
    check("整理后体检：不再有同题多 key", core.health(out)["dup_keys"] == 0, "")


def test_split_groups_and_paper():
    bank = sample()
    bank["qfff777"] = {"a": "A", "q": "硅片制绒温度是多少？", "t": 0, "at": 1790800000000, "src": "learn"}
    bank["iabc123@1"] = {"a": "A", "at": 1790750000000, "src": "official"}
    bank["iabc123@2"] = {"a": "B", "at": 1790750001000, "src": "official"}
    bank["idef456@1"] = {"a": "C", "at": 1790750002000, "src": "official"}
    groups = core.split_groups(bank, [("电气", ["电动机", "交换"]), ("光伏", ["制绒", "硅片"])])
    check("多组关键词拆分：两组 + 未归类",
          set(groups.keys()) == {"电气", "光伏", "未归类"}
          and len(core.entries(groups["光伏"])) == 1
          and len(core.entries(groups["电气"])) == 2, str({k: len(core.entries(v)) for k, v in groups.items()}))
    papers = core.split_by_paper(bank)
    check("按整卷图片拆分：同图的题进同一组、无指纹的进未归类",
          "iabc123_2题" in papers and "idef456_1题" in papers
          and len(core.entries(papers["iabc123_2题"])) == 2
          and any(k == "未归类" for k in papers), str(list(papers)))


def test_gui_smoke():
    """界面冒烟：起一个 withdraw 掉的窗口，验证表格/筛选/选中装配正常（不弹任何对话框）。"""
    try:
        import tkinter
        sys.path.insert(0, HERE)
        import bank_editor
    except Exception as e:
        check("界面冒烟：跳过（没有 tkinter）", True, str(e)[:40])
        return
    p = os.path.join(TMP, "gui.json")
    core.save_bank(p, sample(), backup=False)
    try:
        app = bank_editor.BankEditor(p)
        app.withdraw()                     # 不弹到桌面上
        app.update()
        n_rows = len(app.tree.get_children())
        app.q_var.set("vlan")
        app.refresh()
        n_filtered = len(app.tree.get_children())
        app.clear_filter()
        first = app.tree.get_children()[0]
        app.tree.selection_set(first)
        app.update()
        sel = app.selected_keys()
        # v2.2 来源标注：表格要有多出来的两列，筛选下拉要能列出科目
        cols = tuple(app.tree["columns"])
        check("界面：表格多了「科目」「来源账号」两列", "subj" in cols and "from" in cols, str(cols))
        check("界面：科目筛选下拉列出了题库里的科目",
              "光伏电池制造技术" in list(app.cb_subj["values"]),
              str(list(app.cb_subj["values"])[:4]))
        app.subj_var.set("光伏电池制造技术")
        app.refresh()
        check("界面：按科目筛选只剩 1 条", len(app.tree.get_children()) == 1,
              str(len(app.tree.get_children())))
        app.clear_filter()
        check("界面：清除筛选后科目也复位", app.subj_var.get() == "全部", app.subj_var.get())
        # 斑马纹 × 有没有答案：每行只挂这四个标签之一
        tags = [app.tree.item(k, "tags")[0] for k in app.tree.get_children()]
        tags_ok = all(t in ("odd", "alt", "odd-noans", "alt-noans") for t in tags)
        # 点表头要出排序箭头（▼ 降序），点 # 回到原始顺序
        app.sort_by("question")
        app.update()
        arrow_ok = "▼" in app.tree.heading("question", "text")
        app.sort_by("no")
        app.update()
        arrow_clear = app.tree.heading("question", "text").strip() == "题干"
        # 改答案 → 亮 ●（dirty）；保存 → 灭掉
        app.tree.selection_set(first)
        app.txt_answer.delete(0, "end")
        app.txt_answer.insert(0, "ABCD")
        app.save_answer()
        dirty_on = app.dirty and app.dirty_var.get().startswith("●")
        app.save_file()
        dirty_off = not app.dirty
        # 导出当前筛选结果（把弹窗的选路径换成固定路径，其余逻辑照跑）
        out = os.path.join(TMP, "gui-export.json")
        bank_editor.filedialog.asksaveasfilename = lambda **kw: out
        app.export_all()
        export_ok = os.path.exists(out) and len(core.entries(core.load_bank(out))) == 4
        check("界面：行标签是「斑马纹×有无答案」四选一", tags_ok, str(tags))
        check("界面：点表头出排序箭头，点 # 回到原始顺序", arrow_ok and arrow_clear, "")
        check("界面：改答案亮 ●、保存后灭", dirty_on and dirty_off,
              "dirty_on=%s dirty_off=%s" % (dirty_on, dirty_off))
        check("界面：导出当前筛选结果写出 4 条", export_ok, "")
        # 把一条的答案清空 → 那行要变成 -noans（压暗）
        k0 = app.tree.get_children()[0]
        app.bank = core.set_answer(app.bank, k0, "")
        app.rows_all = core.entries(app.bank)
        app.refresh()
        check("界面：没答案的行压暗（-noans）",
              app.tree.item(k0, "tags")[0].endswith("-noans"), str(app.tree.item(k0, "tags")))
        app.destroy()
        check("界面冒烟：表格装了 4 行、搜索能过滤、选中能拿到 key",
              n_rows == 4 and n_filtered == 1 and sel == [first], "rows=%d filtered=%d sel=%s" % (n_rows, n_filtered, sel))
    except Exception as e:
        check("界面冒烟：构造/刷新没报错", False, "%s: %s" % (type(e).__name__, e))


def test_gui_empty_state():
    """没打开文件时的空状态：窗口能起来、空状态文字能显示、概览不炸。"""
    try:
        import bank_editor
    except Exception as e:
        check("界面空状态：环境没有 tkinter", True, str(e)[:40])
        return
    try:
        app = bank_editor.BankEditor(None)
        app.withdraw()
        app.refresh()
        app.show_empty(True)
        app.update()
        txt = app.txt_detail.get("1.0", "end")
        app.show_empty(False)
        app.update()
        app.destroy()
        check("界面空状态：没打开题库时文案与概览都正常", "还没有打开题库" in txt,
              txt.strip().splitlines()[0] if txt.strip() else "")
    except Exception as e:
        check("界面空状态：没打开题库时文案与概览都正常", False, "%s: %s" % (type(e).__name__, e))


def test_csv_roundtrip():
    bank = sample()
    cp = os.path.join(TMP, "b.csv")
    core.to_csv(bank, cp)
    head = open(cp, encoding="utf-8-sig").readline().strip()
    check("导出 CSV：表头齐全", head.startswith("key,题型,题干,答案"), head)
    txt = open(cp, encoding="utf-8-sig").read()
    check("导出 CSV：内容里有题干和答案", "交换机接口模式" in txt and "ABD" in txt, "")
    check("导出 CSV：多了科目/班级/学号三列",
          all(c in head for c in ("科目", "班级", "学号")) and "光伏电池制造技术" in txt, head)
    # 模拟用户在 Excel 里改了一条答案
    rows = txt.splitlines()
    rows = [r.replace("交换机接口模式有哪些？,ABD", "交换机接口模式有哪些？,ABCD") if "qaaa111" in r else r
            for r in rows]
    open(cp, "w", encoding="utf-8-sig").write("\n".join(rows) + "\n")
    back, stats = core.from_csv(bank, cp)
    check("CSV 改完写回：答案更新、来源打 +csv 标记",
          back["qaaa111"]["a"] == "ABCD" and "csv" in back["qaaa111"]["src"] and stats["changed"] == 1,
          json.dumps(stats))


def test_bank_ai_logic():
    """AI 整理（极简 Agent）：提示词只带勾选任务；回复严格校验；应用只改该改的。"""
    rows = [
        {"key": "q1", "q": "下列词语读音正确的是（）", "t": 0, "a": "A", "subj": "", "at": 3},
        {"key": "q2", "q": "请把你的作业拍照上传", "t": 4, "a": "", "subj": "", "at": 2},
        {"key": "q3", "q": "制绒工序常用什么溶液？", "t": 0, "a": "B", "subj": "", "at": 1},
    ]
    p = bank_ai.build_prompt(rows, ["subj", "junk"])
    check("AI 整理：提示词只带勾选的任务",
          "任务1 补科目" in p and "任务2 标无效题" in p and "任务3" not in p, "")
    check("AI 整理：提示词带 key 与题干", "key=q1" in p and "读音正确" in p, "")

    reply = ('```json\n{"items":['
             '{"key":"q1","subj":"语文","junk":false,"confidence":0.9,"why":"字音"},'
             '{"key":"q2","subj":"未知","junk":true,"junk_reason":"作业","confidence":0.95,"why":"作业"},'
             '{"key":"nope","subj":"数学","junk":false,"confidence":1,"why":"x"}'
             ']}\n```')
    props, errs = bank_ai.parse_reply(reply, rows)
    kinds = {(x["kind"], x["key"]) for x in props}
    check("AI 整理：能剥掉 ```json 围栏并解析", len(props) == 2, str(sorted(kinds)))
    check("AI 整理：不存在的 key 会被丢掉并记错",
          ("subj", "nope") not in kinds and any("不存在" in e for e in errs), str(errs))
    check("AI 整理：科目名清洗（未知 → 丢弃）", all(x["new"] != "未知" for x in props), str(props))
    check("AI 整理：无效题被标出来", ("junk", "q2") in kinds, str(sorted(kinds)))

    dup_rows = [{"key": "a", "q": "交换机接口模式有哪些？", "t": 1, "a": "ABD", "subj": "", "at": 2},
                {"key": "b", "q": "交换机接口模式有哪些", "t": 1, "a": "ABD", "subj": "", "at": 1}]
    props2, _ = bank_ai.parse_reply('{"items":[{"key":"a","dupe_of":"b","confidence":0.8},'
                                    '{"key":"b","dupe_of":"a","confidence":0.8}]}', dup_rows)
    drops = {x["key"] for x in props2 if x["kind"] == "dupe"}
    check("AI 整理：近似重复只删信息少的那条（不会两条都删）", drops == {"b"}, str(props2))

    batches = []

    def fake(system, user, max_tokens):
        keys = re.findall(r"key=(\S+)", user)
        batches.append(len(keys))
        items = ",".join('{"key":"%s","subj":"数学","junk":false,"confidence":0.9,"why":"x"}' % k for k in keys)
        return '{"items":[%s]}' % items

    many = [{"key": "k%d" % i, "q": "数学题 %d" % i, "t": 0, "a": "A", "subj": "", "at": i} for i in range(23)]
    props3, errs3 = bank_ai.analyze(many, {}, ["subj"], 10, chat_fn=fake)
    check("AI 整理：23 条按 10 条一批 → 3 批、23 条建议",
          len(props3) == 23 and batches == [10, 10, 3] and not errs3, str(batches))

    bank = {"__meta__": {"count": 3},
            "a": {"q": "语文题", "a": "A", "src": "learn"},
            "b": {"q": "作业", "a": "", "src": "learn"},
            "c": {"q": "制绒", "a": "B", "src": "official"}}
    out, stats = bank_ai.apply_proposals(bank, [
        {"kind": "subj", "key": "a", "new": "语文"},
        {"kind": "junk", "key": "b"},
        {"kind": "dupe", "key": "c", "keep": "a"}])
    check("AI 整理：补科目写 subj 并打 +AI 标记",
          out["a"]["subj"] == "语文" and "+AI" in out["a"]["src"], str(out["a"]))
    check("AI 整理：无效/重复条目被删、meta 条数重算",
          "b" not in out and "c" not in out and out["__meta__"]["count"] == 1,
          json.dumps({k: v for k, v in out.items() if k.startswith("__")}, ensure_ascii=False))
    check("AI 整理：只挑有题干的条目（图片键跳过）",
          [r["key"] for r in bank_ai.candidates([{"key": "i1", "q": ""}, {"key": "q2", "q": "有题干"}])]
          == ["q2"], "")
    check("AI 整理：科目名清洗（未知 / 长句 / 带标点 都丢掉）",
          bank_ai.clean_subj("  高等数学 ") == "高等数学"
          and bank_ai.clean_subj("未知") == ""
          and bank_ai.clean_subj("计算机网络？") == ""
          and bank_ai.clean_subj("很长" * 15) == "", "")


def test_gui_ai_dialog():
    """AI 整理对话框能开，三个任务 + 检测模型 + 开始分析 都在。"""
    try:
        import tkinter as tk
        import bank_editor
    except Exception:
        check("界面：AI 整理对话框能开、任务项齐全", True, "环境没 tkinter")
        return
    p = os.path.join(TMP, "ai_gui.json")
    core.save_bank(p, sample(), backup=False)
    app, ok, detail = bank_editor.BankEditor(p), True, ""
    app.withdraw()
    try:
        app.do_ai_organize()
        app.update()
        dlg = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                    if t:
                        texts.append(str(t))
                except Exception:
                    pass
                walk(c)
        walk(dlg)
        need = ["补科目标签 subj", "标出无效题", "找近似重复", "检测模型", "开始分析"]
        missing = [x for x in need if not any(x in t for t in texts)]
        ok = not missing
        detail = "缺：" + ",".join(missing) if missing else ""
        dlg.destroy()
    except Exception as e:
        ok, detail = False, "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            app.destroy()
        except Exception:
            pass
    check("界面：AI 整理对话框能开，三个任务 + 检测模型 + 开始分析都在", ok, detail)


def test_year_subject_split():
    """按「年级 × 科目」分流：有班级/课程名的精确分；只有题干的靠猜；都没有的进未分类。"""
    bank = {
        "__meta__": {"app": "t", "count": 4, "exportedAt": 1},
        "q25yw01": {"a": "A", "q": "下列词语中读音正确的是（）", "t": 0, "at": 1, "src": "learn",
                    "subj": "语文", "cls": "25级05班", "stu": "2024001", "who": "张三"},
        "q25zy01": {"a": "B", "q": "制绒工序常用什么溶液？", "t": 0, "at": 2, "src": "learn",
                    "subj": "光伏电池制造技术", "cls": "25级05班", "stu": "2024001", "who": "张三"},
        "q24yw01": {"a": "C", "q": "求 y=x^2 的导数（数学题）", "t": 0, "at": 3, "src": "learn",
                    "cls": "24级02班", "stu": "2023002", "who": "李四"},
        "qold001": {"a": "D", "q": "这是一条老数据，没有任何标注", "t": 0, "at": 4, "src": "official"},
    }
    check("年级抽取：从班级名『25级05班』拿到『25级』", core.year_of({"cls": "25级05班"}) == "25级",
          core.year_of({"cls": "25级05班"}))
    check("年级抽取：班级名优先于题干里的年级", core.year_of({"cls": "25级05班", "q": "24级的题"}) == "25级", "")
    check("年级抽取：抽不到就是『年级未知』", core.year_of({"q": "没有年级"}) == "年级未知", "")

    groups, st = core.group_year_subject(bank)
    check("科目判定：课程名=语文 → 语数外（依据=标注）",
          core.subject_of({"subj": "语文"}) == ("语数外", "标注"), "")
    check("科目判定：课程名=光伏电池制造技术 → 专业课（依据=标注）",
          core.subject_of({"subj": "光伏电池制造技术"}) == ("专业课", "标注"), "")
    check("科目判定：没课程名 → 拿题干猜（依据=猜）",
          core.subject_of({"q": "求 y=x^2 的导数（数学题）"}) == ("语数外", "猜"), "")
    check("科目判定：什么都判不了 → 未分类",
          core.subject_of({"q": "这是一条老数据"}) == ("未分类", "无"), "")

    check("分流统计：猜出来的 1 条、年级未知 1 条",
          st["guessed"] == 1 and st["unknown_year"] == 1, json.dumps(st, ensure_ascii=False))

    parts = core.split_by_year_subject(bank)
    check("分流：25级按科目分成两份",
          len(core.entries(parts.get("25级-语数外", {}))) == 1
          and len(core.entries(parts.get("25级-专业课", {}))) == 1, str(sorted(parts)))
    check("分流：24级靠题干猜出语数外",
          len(core.entries(parts.get("24级-语数外", {}))) == 1, str(sorted(parts)))
    check("分流：老数据进『年级未知-未分类』",
          len(core.entries(parts.get("年级未知-未分类", {}))) == 1, str(sorted(parts)))
    check("分流：每份都带自己的 __meta__ 且条数对得上",
          all((sub.get("__meta__") or {}).get("count") == len(core.entries(sub)) for sub in parts.values()),
          str({k: len(core.entries(v)) for k, v in parts.items()}))


def test_cli_year_split():
    """命令行分流：两个库 → 按 key 合并 → 按「年级 × 科目」出文件；.js 也能解析。"""
    import bank_split_cli as cli
    d = os.path.join(TMP, "cli")
    os.makedirs(d, exist_ok=True)
    two = {
        "a.json": {"q25yw1": {"a": "A", "q": "下列词语读音正确的是（）", "at": 1, "src": "learn",
                              "subj": "语文", "cls": "25级05班"},
                   "q25zy1": {"a": "B", "q": "制绒用什么溶液？", "at": 2, "src": "learn",
                              "subj": "光伏电池制造技术", "cls": "25级05班"}},
        "b.json": {"q25yw1": {"a": "A", "q": "下列词语读音正确的是（）", "at": 3, "src": "learn",
                              "subj": "语文", "cls": "25级05班"},
                   "q24zy1": {"a": "C", "q": "电机启动方式？", "at": 4, "src": "learn",
                              "subj": "电机拖动", "cls": "24级02班"}},
    }
    for fn, bank in two.items():
        with open(os.path.join(d, fn), "w", encoding="utf-8") as fh:
            json.dump(bank, fh, ensure_ascii=False)

    out = os.path.join(d, "out")
    rc = cli.main([d, "-o", out, "--js", "--quiet"])
    files = sorted(os.listdir(out)) if os.path.isdir(out) else []
    js = [x for x in files if x.endswith(".js")]
    jo = [x for x in files if x.endswith(".json")]
    check("CLI 分流：退出码 0", rc == 0, str(rc))
    check("CLI 分流：3 组 → 3 份 json + 3 份 js", len(jo) == 3 and len(js) == 3, str(files))
    check("CLI 分流：分出『25级-语数外』和『25级-专业课』",
          any("25级-语数外" in x for x in jo) and any("25级-专业课" in x for x in jo), str(files))
    pick = [x for x in jo if "25级-语数外" in x]
    if pick:
        sub = core.load_bank(os.path.join(out, pick[0]))
        check("CLI 分流：重复 key 合并后只剩 1 条", len(core.entries(sub)) == 1,
              str(len(core.entries(sub))))
    pick = [x for x in js if "25级-语数外" in x]
    if pick:
        txt = open(os.path.join(out, pick[0]), encoding="utf-8").read()
        head = "\nconst bank = "          # 行首那个才是数据，注释里的 "// Node：const bank = " 不算
        tail = ";\nif (typeof module"
        data = json.loads(txt[txt.index(head) + len(head):txt.index(tail)])
        check("CLI 分流：.js 里包的是一份能直接解析的题库",
              data.get("__meta__", {}).get("count") == 1, str(data.get("__meta__")))


def test_gui_year_dialogs():
    """「年级分流」和「批量归并」两个对话框能开、能点预览、能关（防布局炸了没人发现）。"""
    try:
        import tkinter as tk
        from tkinter import ttk
        import bank_editor
    except Exception:                       # 环境没 tkinter 就跳过，不算失败
        check("界面：年级分流 / 批量归并 两个对话框都能开、能预览、能关", True, "环境没 tkinter")
        return
    p = os.path.join(TMP, "dlg.json")
    core.save_bank(p, sample(), backup=False)
    app, ok, detail = bank_editor.BankEditor(p), True, ""
    app.withdraw()
    try:
        def find(widget, text):
            for c in widget.winfo_children():
                try:
                    if isinstance(c, ttk.Button) and c.cget("text") == text:
                        return c
                except Exception:
                    pass
                hit = find(c, text)
                if hit:
                    return hit
            return None

        for opener, label in [(app.do_split_year, "年级分流"), (app.do_batch_year, "批量归并")]:
            opener()
            app.update()
            dlg = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
            btn = find(dlg, "预览")
            if btn is None:
                ok, detail = False, label + "里找不到「预览」按钮"
            else:
                btn.invoke()                 # 预览读的是空文件列表 / 当前库，不该抛
                app.update()
            dlg.destroy()
            app.update()
    except Exception as e:
        ok, detail = False, "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            app.destroy()
        except Exception:
            pass
    check("界面：年级分流 / 批量归并 两个对话框都能开、能预览、能关", ok, detail)


TESTS = [test_read_write_backup, test_entries_and_filter, test_subset_delete_set,
         test_merge, test_split, test_consolidate_and_health, test_split_groups_and_paper,
         test_csv_roundtrip, test_year_subject_split, test_cli_year_split,
         test_bank_ai_logic, test_gui_ai_dialog,
         test_gui_empty_state, test_gui_year_dialogs, test_gui_smoke]


if __name__ == "__main__":
    for fn in TESTS:
        fn()
    bad = [n for n, ok in RESULTS if not ok]
    print("\n== 结果：%d/%d 通过 ==" % (len(RESULTS) - len(bad), len(RESULTS)))
    if bad:
        print("失败的用例：%s" % "、".join(bad))
        print("临时目录留在这儿方便查：%s" % TMP)
    else:
        shutil.rmtree(TMP, ignore_errors=True)   # 跑完就收摊，别在 %TEMP% 里堆一地
    sys.exit(1 if bad else 0)
