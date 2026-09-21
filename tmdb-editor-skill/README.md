# TMDB 编辑助手 Skill（tmdb-editor）

把 tmdb-helper 油猴脚本（已从仓库移除，功能由本 skill 接替）沉淀的 TMDB 编辑知识与接口，改造成 **ZCode skill**（AI 代理可执行），并合并外部「国内影视资料录入 TMDB」方案的经验。仓库内 `tmdb-editor-skill/` 是 skill 源（含测试），`./install.sh` 安装到用户级 `~/.zcode/skills/tmdb-editor/`。

- **定位**：从国内来源（B站/豆瓣/百度百科/爱奇艺/腾讯视频/优酷/芒果TV/红果短剧）取数，在 TMDB 上**核对 → 建条/补全 → 录单集 → 传图片 → 中文化/别名/演职员 → 剧集组/分季修正**。独立工具，不与 123Cloud 客户端/油猴脚本联动。
- **纪律**：先读后写、写前出计划请人确认、宁慢勿错（TMDB 无自助删条，误建成本高）。
- **写通道**：Playwright 驱动真实 Chrome（登录态 + 同源 fetch，绕 AWS WAF），首个人工登录一次后 profile 持久化。

## 目录

```
tmdb-editor-skill/
├── SKILL.md                  # skill 主流程（安装后是 AI 代理的入口）
├── install.sh                # 安装到 ~/.zcode/skills/tmdb-editor/
├── requirements.txt          # 运行依赖（httpx/pillow/bs4/playwright）
├── references/
│   ├── tmdb-web-api.md       # TMDB 网页内部 /remote/ 编辑接口全集 + 实测坑 + 待实测端点探测法
│   ├── tmdb-read-api.md      # 官方 v3 只读 API（搜索/diff/核验）
│   ├── sources.md            # 八个国内来源的取数接口、字段、图床 Referer、搜图兜底
│   └── rules.md              # 别名核对、日期规则、图片规范、翻译规范、编辑礼仪
├── scripts/
│   ├── fetch_source.py       # 取数 CLI：八平台分集/详情 + 排期引擎 + 过滤词剔除 + 图片下载
│   ├── prep_image.py         # 图片规范化：官方规范裁剪/禁放大/去黑边 + Referer 下载 + contact sheet
│   ├── browser.py            # 写通道：Playwright 真实 Chrome，upload_config/fetch/upload/eval ops
│   └── serve.py              # 带 CORS 的本地图床中转（控制台手工执行时用）
└── tests/                    # pytest 纯逻辑回归（不联网、不需要 TMDB 账号）
```

## 安装

```bash
cd tmdb-editor-skill
./install.sh        # 装到 ~/.zcode/skills/tmdb-editor/，并建好 skill 自带 venv
```

首次使用（写操作前）：

```bash
PY=~/.zcode/skills/tmdb-editor/.venv/bin/python
$PY ~/.zcode/skills/tmdb-editor/scripts/browser.py check    # 环境自检
$PY ~/.zcode/skills/tmdb-editor/scripts/browser.py login    # 打开浏览器人工登录 TMDB 一次
```

装好后重启 ZCode 会话，涉及「TMDB 录入/补全/传海报/中文化/影库回填 tmdb 标记」的任务会自动触发该 skill。

## 使用速查（skill 安装路径）

```bash
PY=~/.zcode/skills/tmdb-editor/.venv/bin/python
S=~/.zcode/skills/tmdb-editor/scripts
$PY $S/fetch_source.py episodes "https://www.bilibili.com/bangumi/play/ss28747" --tsv eps.tsv
$PY $S/fetch_source.py episodes-filter eps.json --words "PV,预告,花絮"   # 剔除并重编号
$PY $S/fetch_source.py schedule --start-date 2026-01-05 --count 24 --weekdays 一,四 --per-slot 2
$PY $S/fetch_source.py douban-detail 3643508
$PY $S/fetch_source.py baike-episodes "https://baike.baidu.com/item/武林外传"
$PY $S/prep_image.py download "<图床直链>" -o raw.jpg
$PY $S/prep_image.py transform raw.jpg poster -o poster.jpg   # backdrop/still/logo/profile 同理
$PY $S/prep_image.py contact-sheet a.jpg b.jpg c.jpg -o sheet.jpg
$PY $S/browser.py run plan.json --log plan.log.jsonl          # 计划 JSON 形态见 browser.py 头注释
```

## 与 tmdb-helper 油猴脚本的关系

- **互补不替代**：油猴脚本跑在浏览器里，适合人手工核对与轻量直传；skill 跑在 AI 代理侧，适合批量取数、diff 计划、长任务断点执行。两边共用同一批接口契约（`references/tmdb-web-api.md` 即两边的实测合集）。
- 油猴脚本继续按 Greasy Fork 独立发版，skill 不影响其版本与发布。

## 命令使用说明

所有命令统一用 skill 自带 Python 跑：`PY=~/.zcode/skills/tmdb-editor/.venv/bin/python`、`S=~/.zcode/skills/tmdb-editor/scripts`。所有子命令与参数都有中文帮助：`$PY $S/fetch_source.py --help`、`$PY $S/fetch_source.py episodes --help` 可随时查看。

### fetch_source.py（国内来源取数）

| 命令 | 作用 | 示例 |
|---|---|---|
| `episodes 链接` | 自动识别平台抓整季分集（B站/爱奇艺/腾讯/芒果/优酷/红果），输出统一 JSON（集号/集名/日期/时长/简介/剧照直链） | `episodes "https://www.bilibili.com/bangumi/play/ss28747" --out eps.json --tsv eps.tsv` |
| `bilibili-search 关键词` | B站番剧搜索（自动引导 buvid cookie），拿 season_id | `bilibili-search 凡人修仙传 --limit 5` |
| `hongguo-search 关键词` | 红果短剧站内搜索，拿 series_id | `hongguo-search 无名` |
| `douban-suggest 关键词` | 豆瓣搜索建议（id/片名/年份/类型） | `douban-suggest 凌云壮志包青天` |
| `douban-detail 豆瓣ID` | 豆瓣条目详情：原名/译名/首播/简介/导演/编剧/主演/类型/国家/语言/集数/又名/海报（rexxar 主路 + 桌面页补空） | `douban-detail 3643508` |
| `douban-imdb tt编号` | IMDb 编号反查豆瓣条目 ID | `douban-imdb tt1234567` |
| `baike-search 关键词` | 百度百科搜索 | `baike-search 凌云壮志包青天` |
| `baike-detail 词条链接` | 百科词条信息栏：导演/主演/类型/集数/首播/出品方/播出平台 + 海报 | `baike-detail "https://baike.baidu.com/item/…"` |
| `baike-episodes 词条链接` | 百科分集剧情表格 → 分集列表（集号/集名/简介/日期） | `baike-episodes "https://baike.baidu.com/item/…/分集剧情" --tsv eps.tsv` |
| `schedule` | 生成排期分集（周更/固定间隔/单日多集共用日期） | `schedule --start-date 2026-01-05 --count 24 --weekdays 一,四 --per-slot 2 --tsv eps.tsv` |
| `episodes-filter 文件` | 按过滤词剔除分集并重编号（PV/预告/花絮），保留原有缺集 | `episodes-filter eps.json --words "PV,预告,花絮" --out eps-clean.json` |
| `image 图片链接 -o 文件` | 按图床 Referer 表下载图片（防盗链），豆瓣/bkimg 自动升级原图 | `image "https://img1.doubanio.com/…" -o raw.jpg` |

### prep_image.py（图片规范化）

| 命令 | 作用 | 示例 |
|---|---|---|
| `download 链接 -o 文件` | 同 fetch_source 的 image（Referer 表 + 原图升级） | `download "https://i0.hdslb.com/…" -o raw.jpg` |
| `transform 输入 类型` | 按官方规范处理：比例不对裁剪（禁拉伸）、超上限缩小、低于下限报错（禁放大）、16:9 自动去黑边 | `transform raw.jpg poster -o poster.jpg` |
| `probe 输入` | 只看尺寸/格式/自动判定的图片类型，不产出文件 | `probe raw.jpg` |
| `contact-sheet 多张图` | 候选图拼 contact sheet 供人挑选（带序号） | `contact-sheet a.jpg b.jpg c.jpg -o sheet.jpg --cols 3` |

类型与规范：`poster` 海报 2:3 ≥500×750；`backdrop` 背景图 / `still` 剧照 16:9 ≥1280×720；`logo` 标志 PNG ≥200×50；`profile` 人物头像 2:3 ≥300×450。`--crop-half left|right` 用于「正面+背面」横拼封面的取半裁。

### browser.py（TMDB 写通道）

| 命令 | 作用 |
|---|---|
| `check` | 环境自检：playwright/Chrome/CDP/代理。首次使用先跑这个 |
| `open` / `close` | **启动/关闭常驻浏览器**（独立 Chrome + 持久 profile + CDP 9223）。推荐开一次后一直用：登录态像日常浏览器一样长期有效；`run/login/check` 会自动附加到它 |
| `login` | 打开登录页人工登录 TMDB（持久 profile + cookie 存档）。可 `--wait-seconds 900` 无人值守轮询等待登录 |
| `run 计划.json` | 执行写操作计划：ops 顺序执行，写请求自动限速（默认 500ms），结果输出 JSON + 可选 `--log` JSONL |

`run` 的计划 JSON 七种 op：

```json
{"intervalMs": 500, "keepGoing": false, "ops": [
  {"op": "upload_config", "pageUrl": "/tv/123/images/posters"},
  {"op": "navigate", "url": "/tv/123"},
  {"op": "hover", "selector": "li[data-image-id='…']"},
  {"op": "click", "selector": "…", "wait": 2.0},
  {"op": "screenshot", "path": "/tmp/看页面.png", "fullPage": false},
  {"op": "fetch", "method": "GET", "url": "/tv/123/season/1/remote/episodes?translate=false"},
  {"op": "fetch", "method": "POST", "url": "/tv/123/season/1/remote/episodes",
   "query": {"translate": "false"},
   "data": {"episode_number": 1, "name": "第1集", "air_date": "", "locked_fields": []}},
  {"op": "upload", "url": "/image", "file": "poster.jpg",
   "fields": {"media_id": "<upload_config 拿到的>", "media_type": "TvSeries", "type": "poster", "translate": "false"}},
  {"op": "eval", "js": "async () => { … 页面内任意 fetch/DOM 操作，stay:true 可脱离 TMDB 域抓别的站 }"}
]}
```

结果判读：`ok:true` 成功；`flag:"auth"` 掉登录（重新 login）；`flag:"waf_or_login"` 被拦（换网络/代理重试）；`flag:"tmdb_validation"` TMDB 校验失败（按 `failure.errors` 文案修数据，不要原样重试）。**在 ZCode 里执行必须绕过 Bash 沙箱**（沙箱出口拦 themoviedb.org）。

### serve.py（本地图床中转）

`serve.py [目录] [端口]` —— 带 CORS 的静态服务器（默认当前目录、8899 端口、只监听 127.0.0.1），给浏览器控制台手工执行上传时的 fetch 取图用；用 browser.py 时不需要它。

### 常见场景

**给缺海报的条目补海报（三步）**：

```bash
$PY $S/fetch_source.py douban-detail 豆瓣ID              # 拿海报直链
$PY $S/prep_image.py download "<海报直链>" -o raw.jpg && $PY $S/prep_image.py transform raw.jpg poster
$PY $S/browser.py run upload-plan.json                   # upload_config + upload 两个 op
```

**批量录入整季分集（五步）**：`episodes` 取数 → `episodes-filter` 清理 → 与官方季现值 diff（先读后写）→ 出计划给人确认 → `run` 批量提交（`--log` 记断点，失败续跑）。

**中文化/补简介**：`douban-detail` 拿中文简介 → 只读拉条目现值 diff → 只补空位、不动已有内容 → `run` 提交（翻译走 `edit?translate=true` 同端点、字段名带语言后缀，见 references/tmdb-web-api.md）。

## 测试与验收

```bash
cd tmdb-editor-skill && .venv/bin/python -m pytest tests/ -q
```

- 纯逻辑回归（URL 解析/分集映射/排期引擎/过滤词/豆瓣百科解析/图片规范/响应分类/计划契约），不联网。
- 联网冒烟（免登录公开接口）已验证：豆瓣 suggest、B站搜索与整季分集（含 buvid cookie 引导）、图床 Referer 下载。
- **写通道（/remote/、/image）需 TMDB 登录态**：`browser.py login` 后先做只读验证（GET remote/episodes），写入动作（改集/传图）在测试条目上小步验证通过再批量。

## 注意事项

- **ZCode 内执行 browser.py 必须绕过 Bash 沙箱**：实测沙箱出口会拦 themoviedb.org（豆瓣/B站不受影响），沙箱外直连正常（Loon TUN 路径可用）。若网络确需代理，写 `~/.zcode/skills/tmdb-editor/proxy.txt` 或加 `--proxy`。
- TMDB 编辑接口是未公开接口，改版会失效——失效时按 `references/tmdb-web-api.md` 的「探测法」从官方页面重新核对，不要凭猜测重试。
- 遵守 `references/rules.md`：别名核对宁慢勿错、查不到的播出日期留空、图片禁放大、批量写限速 300–800ms、编辑量节制。
- Chrome profile（登录态）存在 `~/.zcode/skills/tmdb-editor/chrome-profile`，别提交、别外传。
- 仓库根 `AGENTS.md` 的发版铁律（本地打包验收后才提交）对仓库内本目录同样适用；skill 本体安装到用户级目录，不随客户端出包。
