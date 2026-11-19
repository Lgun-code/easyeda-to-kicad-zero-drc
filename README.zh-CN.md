# easyeda-to-kicad-zero-drc

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![KiCad](https://img.shields.io/badge/KiCad-10-blue.svg)](https://www.kicad.org/)
[![Python](https://img.shields.io/badge/Python-3.x-green.svg)](https://www.python.org/)

[English](README.md) | **简体中文**

一个 Agent Skill（同时也是可独立运行的脚本流水线）：把**嘉立创EDA专业版（EasyEDA Pro）**工程转换为原生 **KiCad** 工程，并把 DRC 报告清到**零错误、零警告、零未连接**。

真实板子裸导入 KiCad 时通常有*数百条* DRC 报错——其中绝大多数根本不是设计问题，而是导入器造成的系统性损伤：源设计规则被丢弃、UUID 在封装实例之间重复（导致报错归属错乱）、禁布区在重灌铺铜前不生效、丝印图形按构造就压在焊盘上。本项目先用脚本确定性修复这些损伤，再为剩余项提供一份经实测验证的修复手册（playbook），供 Agent（或你本人）逐项清完。

## 功能

- **设计规则移植**——从立创源工程读出真实规则（支持 V2 `.epcb` 与 V3 `.epru`），mil 换算为 mm 后写入 KiCad 板级设置与工程文件，而不是留着 KiCad 的出厂默认值。
- **工程封装库抽取**——由板子本身生成工程内 `.pretty` 库，让封装一致性检查有真实可比的依据。
- **全文件 UUID 去重**——导入器不仅在封装实例间复用 UUID，连子图形、走线、过孔的 UUID 也重复，DRC 会因此把报错安到错误的器件上；本项目对文件中所有重复 UUID 重新生成。
- **丝印修复引擎**——按真实间距语义（线宽边缘而非中心线）对丝印图形做重建、裁剪、微移与冲突消解，处理丝印压焊盘与丝印互叠。
- **库同步（变体抽取）**——按真实几何对放置实例分组，立创里逐实例改动过的位置会被拆成库变体（`Name_v2`、`Name_v3`……），使 `lib_footprint_mismatch` 能真正清零而不是被压制。
- **手册而非玄学**——`references/playbook.md` 按 DRC 报错类型逐条给出验证过的修法，包括定点 `.kicad_dru` 豁免的写法，以及那些会让整份豁免文件静默失效的坑。

## 案例

Mustang「Board3」——144 个封装、107 个网络、4 层板，一个真实的嘉立创专业版工程：

| 阶段 | DRC 总数 |
|---|---|
| 裸导入 | 835 |
| 确定性修复（规则移植、抽库、UUID 去重、重灌） | ~250 |
| 丝印修复 + 库同步 | 59 |
| 定点豁免 + 微修 | **0** |

最终结果与原作者手工完成的 KiCad 移植版一致，同为 0。

## 前置要求

| 要求 | 说明 |
|---|---|
| KiCad 10 | 已在 10.0.8 上验证。需要 `kicad-cli` 以及 KiCad 自带的 Python（`pcbnew`）。KiCad 8/9 预期可用，但未实测。 |
| Python 3 | 文本层脚本只用标准库。 |
| `xvfb-run` | 无头 Linux 上所有 `pcbnew` 脚本必须在虚拟显示下运行（见教学第 4 步）。 |
| 嘉立创EDA专业版 | 仅用于把源工程导出为 V2 格式（教学第 1 步）。 |

## 安装

作为独立流水线使用——克隆即可：

```bash
git clone https://github.com/Lgun-code/easyeda-to-kicad-zero-drc.git
cd easyeda-to-kicad-zero-drc
```

作为 Agent Skill 使用——把仓库复制或克隆到你的 Agent 的 skills 目录（入口是 `SKILL.md`，描述文件是 `skill.yaml`），或者直接让 Agent 读 `SKILL.md` 并按它驱动脚本。

无需安装任何依赖：脚本只用 Python 标准库加 KiCad 自带 Python。

## 使用教学

### 第 1 步——在嘉立创专业版里把工程导出为 V2 格式

KiCad 的导入器只能读 **V2** 格式。V3 工程（`.epro2`）导入后是*空板*——这是最常见的失败原因，先检查它。

在嘉立创专业版里：**文件 → 另存为 → epro (V2 格式)**，把得到的 `.epro` 文件交给流水线。

> 格式自查：解压文件后看内容。V2 包含 `project.json` 和以 JSON 数组存储的 `*.epcb`；V3 包含 `project2.json` 和对象流格式的 `*.epru`。`scripts/extract_rules.py` 两种都能读（可用于读取 V3 工程的规则），但转换必须用 V2。

### 第 2 步——跑确定性流水线

```bash
python3 scripts/pipeline.py MyProject.epro --out MyProject-kicad \
    --kicad-cli /path/to/kicad-cli --kicad-python /path/to/kicad-python
```

`--kicad-cli` / `--kicad-python` 默认取 `PATH` 里的 `kicad-cli` 与 `kicad-python`；KiCad 不是系统安装时（例如解压的 AppImage）请显式传路径。

流水线依次执行：

1. **转换**——用 KiCad 的立创专业版导入器导入选定的 PCB（`convert_pcb.py`）。
2. **基线 DRC**——保存 `drc_before.json`，让之后每一步改进都可量化。
3. **规则移植**——`extract_rules.py` 读源规则；`make_project.py` 写 `.kicad_pro`；`apply_rules.py` 写板级设计设置与默认网络类。
4. **抽封装库**——`extract_library.py` 写出工程 `.pretty` 库与 `fp-lib-table`。
5. **UUID 去重**——`dedup_uuids.py` 重新生成板文件中所有重复 UUID。
6. **重灌铺铜 + DRC**——保存 `drc_after_skill.json`。

在案例板上，仅这一步就把报错从 835 降到约 250，且剩下的每一项都是有对应修法的真实类别，不再是导入噪声。

### 第 3 步——跑丝印修复

```bash
python3 scripts/fix_silk.py MyProject-kicad/<Board>.kicad_pcb \
    --drc MyProject-kicad/drc_after_skill.json
```

这是纯文本脚本（快，不需要 KiCad）。它根据 DRC 报告定位真正被标记的封装，从焊盘几何重建标准芯片轮廓，把丝印裁剪到不压外来焊盘，并通过微移、收缩、（最后手段）删除单边来消解丝印互叠。所有改动都会逐项报告。

### 第 4 步——同步封装库

```bash
xvfb-run -a <kicad-python> -u scripts/sync_library.py \
    MyProject-kicad/<Board>.kicad_pcb MyProject-kicad
```

两个必踩的坑：

- 这个脚本必须用 **KiCad 自带 Python 并在 `xvfb-run` 下**运行——用系统 `python3` 跑会报 `No module named 'wx'`。
- 第二个参数是**工程目录**，*不是* `.pretty` 目录。脚本自己会拼 `<nickname>.pretty`；传成 `.pretty` 路径会生成嵌套的 `X.pretty/X.pretty`，板子随后会指向「不存在」的封装。

立创里几何确实被逐实例改动过的放置会被拆成库变体（`Name_v2`、`Name_v3`……）并改指实例，这正是 `lib_footprint_mismatch` 能真正清零的前提。

### 第 5 步——按 playbook 处理剩余项

第 2–4 步之后剩下的是一小串明确类别：散热焊盘饥饿、源设计自带的庭院重叠、槽孔/板边间距、少量微间距。按 `references/playbook.md` 逐项处理：

- 真实的几何问题用真实微修（焊盘铺铜连接覆盖、0.05 mm 以内的微移、T 型触点分段）。
- 源设计有意为之的行为用**定点** `.kicad_dru` 豁免——一条规则、起好名字、条件只匹配目标对象。

> ⚠️ `.kicad_dru` 里只要有一条约束语法错误，**整份文件会被静默作废**——KiCad 照常退出 0 且不给任何提示。每次改完 `.dru` 必须重跑 DRC，并确认一条已知被豁免的项仍然被豁免。

### 第 6 步——验收：全量 DRC 必须恰好为零

```bash
kicad-cli pcb drc --refill-zones --save-board --format json --severity-all \
    -o drc_final.json MyProject-kicad/<Board>.kicad_pcb
```

验收标准是 `--severity-all`（错误*和*警告）+ 重灌铺铜：**0 违规、0 未连接**。少于这个数不算完成。

### 第 7 步——打包交付

交付 KiCad 工程（`.kicad_pcb`、`.kicad_pro`、如有 `.kicad_dru`、`.pretty` 库、`fp-lib-table`），并附：

- `drc_before.json` 与 `drc_final.json`——可量化的修复前后对比；
- 一份简短修复报告：列明报错数变化链、**每一处删除与每一条规则豁免**，以及任何*未*转换的内容（见下方「范围」）。

## 脚本速查

| 脚本 | 运行环境 | 用途 |
|---|---|---|
| `pipeline.py` | python3 | 编排确定性流水线（教学第 2 步的六个子步骤） |
| `extract_rules.py` | python3 | 立创源规则（V2 + V3）→ `rules.json`（mil → mm） |
| `convert_pcb.py` | kicad-python + xvfb | 调用 KiCad 的立创专业版导入器转换单块 PCB |
| `make_project.py` | python3 | 由 `rules.json` 写 `.kicad_pro` 的规则值 |
| `apply_rules.py` | kicad-python + xvfb | 写板级设计设置 + 默认网络类 |
| `extract_library.py` | kicad-python + xvfb | 抽取工程 `.pretty` 封装库 |
| `dedup_uuids.py` | python3 | 重新生成板文件中所有重复 UUID |
| `fix_silk.py` | python3 | 丝印重建 / 裁剪 / 消解引擎 |
| `sync_library.py` | kicad-python + xvfb | 把几何分歧的放置拆成库变体 |

## 故障排查

| 现象 | 原因 / 修法 |
|---|---|
| 转换后是空板（0 个封装） | 源工程是 V3（`.epro2`）。按教学第 1 步重新导出为 V2。没有 V3→V2 的转换器，KiCad 导入器只支持 V2。 |
| `No module named 'wx'` | 你用系统 Python 跑了 `pcbnew` 脚本。改用 KiCad 自带 Python 并在 `xvfb-run -a` 下运行。 |
| 库同步后报 `Footprint 'X_v2' not found in library` | 变体被写进了嵌套的 `X.pretty/X.pretty`——你传的是 `.pretty` 目录而不是工程目录（教学第 4 步）。把文件上移一层即可。 |
| `.kicad_dru` 豁免「不生效」 | 一条坏约束会静默作废整份文件。每次编辑后重跑 DRC 验证（教学第 5 步）。 |
| DRC 把报错安到错误器件上（如某器件「压到自己」） | 导入器的重复 UUID。跑 `dedup_uuids.py`——必须覆盖*所有*元素，不只是封装。 |
| `pcbnew` 脚本退出时挂死 | KiCad 无头运行的已知行为；本仓库脚本都以 `os._exit(0)` 结尾。以产物文件是否落盘判断成功。 |
| 丝印修复后一轮又有新的丝印报错 | DRC 报告按类型有条数上限（199/499）。拿*新*报告再跑一遍 `fix_silk.py`，几轮即收敛。 |

## 范围与工作原则

本工作流的验收标准是 DRC 完全清零，**不以保全功能为目标**：无解的条目可以删除或改造，但每一处删除与豁免必须写进交付报告（教学第 7 步）。原理图转换不在范围内——KiCad 的立创原理图导入器只能在 GUI 里用——这一点同样必须如实说明，不得静默省略。Agent 执行本流水线时遵循的完整约定见 `SKILL.md`。

## 参与贡献

欢迎提 Issue 和 PR——尤其是 playbook 还没覆盖的 DRC 类别、其他 KiCad 版本的实测报告。提交时请附板子的 DRC 修复前后计数与你的 KiCad 版本。详见 `CONTRIBUTING.md`。

## 许可证

MIT——见 `LICENSE`。
