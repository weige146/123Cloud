---
name: tmdb-editor
description: "在 TMDB（themoviedb.org）上录入/补全/中文化影视条目。当用户给出国内平台（B站/豆瓣/百度百科/爱奇艺/腾讯视频/优酷/芒果TV/红果短剧）的链接或剧名，要求『在 TMDB 建条/补集数/录分集/传海报剧照/补演职员/加别名/中文化翻译/建剧集组/修正分季』时使用。封装了：各来源取数、TMDB 定位与别名核对（防重复建条）、官方 v3 只读 API、登录态网页内部 /remote/ 编辑接口（经真实 Chrome 同源执行）、图片规范处理与上传。不用于纯只读查询——那类需求直接用 TMDB v3 API。"
---

# TMDB 编辑助手

把国内来源的影视资料录入/补全到 TMDB，或将 TMDB 数据修正、中文化。核心纪律：**先读后写、写前出计划请用户确认、宁慢勿错**。

## 环境（先做一次）

- 运行 Python：`~/.zcode/skills/tmdb-editor/.venv/bin/python`（依赖已装好；本仓库源在 `tmdb-editor-skill/`）。
- **网络**：需要能直连 themoviedb.org 的网络路径（实测 Loon TUN 正常）。**ZCode 里注意：Bash 沙箱的出口会拦 themoviedb.org（豆瓣/B站不受影响）**——执行 `browser.py` 必须在沙箱外跑（ZCode 中即绕过沙箱执行）；若用户网络确需代理，把地址写进 `~/.zcode/skills/tmdb-editor/proxy.txt` 或加 `--proxy`。
- **写通道（常驻浏览器，推荐流程）**：`browser.py open` 启动一个不关闭的 Chrome（profile 持久），之后 `run/login/check` 自动附加到它——登录态像日常浏览器一样长期有效（反复开关浏览器 + 同账号反复登录会触发 TMDB 短效会话）。首次 `login` 人工登录一次；若 run 结果出现 `flag:"auth"`，重新 `login` 即可。

```bash
PY=~/.zcode/skills/tmdb-editor/.venv/bin/python
S=~/.zcode/skills/tmdb-editor/scripts
$PY $S/browser.py open     # 启动常驻浏览器（已开则直接复用）
$PY $S/browser.py login    # 首次人工登录（无人值守轮询，--wait-seconds 900）
$PY $S/browser.py run 计划.json   # 执行写操作计划（自动附加常驻浏览器）
```

## 全流程

1. **取数** —— 从用户给的链接/名称拿分集清单、日期、简介、演职员、图片。各来源接口见 `references/sources.md`，可直接跑 `scripts/fetch_source.py`（episodes / douban-detail / baike-episodes / bilibili-search / hongguo-search / image …）。
2. **定位条目** —— 官方 v3 搜索确认条目是否已存在，**别名核对防重复建条**（见 `references/rules.md`，拿不准先问用户，误建无法自助删除）。只读用法见 `references/tmdb-read-api.md`。
3. **差异对比出计划** —— 用只读 API 拉条目/季/集现值，与来源数据逐项比对，产出「将新增/将修改/将跳过」清单（分集表、图片清单、翻译清单……）。**把计划给用户确认后才进第 4 步。**
4. **执行写入** —— 把计划编成 `browser.py run` 的计划 JSON（ops 见该脚本头注释；接口契约与可用片段见 `references/tmdb-web-api.md`）。写请求间隔 300–800ms，长任务落断点文件（已成功条目记进度，中断续跑不重复）。
5. **回读核验** —— 重新只读拉取，核对集数/日期/标题/图片数量；输出结果报告（成功/失败/跳过 + 失败原因）。

## 关键纪律（违者会翻车，详见 references/rules.md）

- **更新已有集**必须带 TMDB 内部 `id/bson_id` 元数据走 primary_facts；**新增**走 remote/episodes 的 `data=JSON` 表单。
- **查不到逐集真实播出日就留空**（接口接受空日期）；绝不按「每周一集」推算国产剧日期；平台上线日 ≠ 首播日。
- 图片：海报 2:3 ≥500×750、剧照/背景 16:9 ≥1280×720、标志 PNG、**禁放大**；用 `prep_image.py transform` 处理（自动去黑边/裁剪/校验）；下载用 `prep_image.py download`（自带图床 Referer 表防盗链）。
- 响应非 JSON = 掉登录或 WAF 拦截（browser.py 会打 `auth/waf_or_login` 标记），停下来重新登录，不要蛮干重试。
- 别名核对四步法；同人同名演员必须核对 `known_for` 作品。

## 参考文件

- `references/tmdb-web-api.md` —— 写接口全集（季/集/剧集组/演职员/图片 + 已实测坑 + 待实测端点探测法）。
- `references/tmdb-read-api.md` —— 官方 v3 只读：搜索、详情、季、别名、翻译、剧集组、图片。
- `references/sources.md` —— 八个国内来源的取数接口、字段、图床 Referer、搜图兜底。
- `references/rules.md` —— TMDB 编辑规则：别名核对、日期规则、图片规范、翻译规范、编辑礼仪。

## 脚本速查

```bash
PY=~/.zcode/skills/tmdb-editor/.venv/bin/python
S=~/.zcode/skills/tmdb-editor/scripts
$PY $S/fetch_source.py episodes "https://www.bilibili.com/bangumi/play/ss12345" --tsv eps.tsv
$PY $S/fetch_source.py episodes-filter eps.json --words "PV,预告,花絮"   # 剔除并重编号
$PY $S/fetch_source.py schedule --start-date 2026-01-05 --count 24 --weekdays 一,四 --per-slot 2
$PY $S/fetch_source.py douban-suggest 凡人修仙传
$PY $S/fetch_source.py douban-detail 3643508
$PY $S/fetch_source.py baike-episodes "https://baike.baidu.com/item/…"
$PY $S/prep_image.py download "https://i0.hdslb.com/bfs/…jpg" -o raw.jpg
$PY $S/prep_image.py transform raw.jpg poster -o poster.jpg
$PY $S/prep_image.py contact-sheet a.jpg b.jpg c.jpg -o sheet.jpg       # 候选图拼图供用户挑选
$PY $S/browser.py run plan.json --log plan.log.jsonl
```
