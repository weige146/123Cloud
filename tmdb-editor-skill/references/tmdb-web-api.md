# TMDB 网页内部编辑接口（写侧全集）

TMDB 公开 v3/v4 API 只能读与账户操作，**不能贡献资料**。资料录入靠网页编辑器的一套未公开接口，路径含 `/remote/`。本文合并了实测沉淀（tmdb-helper 油猴脚本 + cn-media-to-tmdb 项目），是写操作的唯一依据。

## 通用要求

- 必须已登录（同源 Cookie 自动携带）。
- **在已登录的 TMDB 页面里用 `fetch`（同源）调用**，不要用外部 curl——站点前有 AWS WAF。skill 用 `scripts/browser.py`（Playwright 真实 Chrome）执行。
- 写请求是表单编码不是 JSON：`Content-Type: application/x-www-form-urlencoded; charset=UTF-8` + `X-Requested-With: XMLHttpRequest` + `credentials:'same-origin'`；Kendo 约定把 JSON 塞进名为 `data` 的表单字段：`data=<URL编码的JSON>`。
- 每个写请求间隔 300–800ms。
- 接口无稳定性承诺。**失效排查法**：打开对应官方编辑页 → DevTools Network 看真实请求的路径/字段 → 对照页脚 `Build xxxxxxx` 版本；上传配置等页面内嵌数据以实际 HTML 为准。

路径变量：`{tv_id}` 节目数字ID；`{season_no}` 季号；`{episode_id}` 单集内部数字ID；`{media_id}` 图片编辑器 BSON ID（不同于公开数字ID）；`{group_id}` 剧集组 ID。

## 已实测端点

### 季

- `GET /tv/{tv_id}/remote/seasons?translate=false` —— 季列表 + `bson_id`、`episode_count`
- `POST /tv/{tv_id}/remote/seasons` —— 新建季（body: `data=<JSON>`）
- `POST /tv/{tv_id}/season/{season_no}/remote/primary_facts?series_id={tv_id}&translate=false` —— 更新季资料

### 单集

- `GET /tv/{tv_id}/season/{season_no}/remote/episodes?translate=false` —— 剧集列表（含每集 `id`、`bson_id`、`air_date`）
- `POST /tv/{tv_id}/season/{season_no}/remote/episodes?translate=false` —— **新增**单集
- `POST /tv/episode/{episode_id}/remote/primary_facts?series_id={tv_id}&episode_number={n}&translate=false` —— **更新**已有单集
- `POST /tv/{tv_id}/season/{season_no}/remote/next_episode_air_date?translate=false` —— 推算下一集默认日期（body: `episode_number=<n>`）
- `DELETE /tv/{tv_id}/season/{season_no}/episode/{episode_no}?episode_id={episode_id}&translate=false` —— 删除单集

**新增 payload**（官方编辑器同款字段）：

```json
{"episode_number": 2, "name": "第2集", "overview": "", "air_date": "", "runtime": 13, "locked_fields": [], "season_number": 1, "new": true}
```

**更新 payload**（tmdb-helper 实测契约——缺元数据会被拒）：

```json
{"episode_number": 1, "name": "…", "overview": "…", "air_date": "2009-07-15", "runtime": 45,
 "locked_fields": [], "id": 7604118, "season_number": 1, "show_id": 330017, "bson_id": "…",
 "production_code": "", "vote_average": 0, "vote_count": 0}
```

未填字段回退线上现值（先 GET remote/episodes 拿 `id/bson_id` 再更新）。**易错点**：更新第 1 集时 URL 必须带 `episode_number=1`，否则报「Episode number 必须大于 0」；`air_date` 传空串 `""` 合法且规范（见 rules.md 日期规则）。

### 剧集组（episode groups，tmdb-helper 实测）

- `GET /tv/{tv_id}/remote/episode_groups?translate=false` —— 组列表
- `POST /tv/{tv_id}/remote/episode_groups` / `PUT`（带 `id` 改名）—— 组增改（payload: `{"name","description","type"}`，type 1–6：首播顺序/绝对顺序/DVD 顺序/数字流媒体顺序/故事线/制作顺序）
- `GET /tv/{tv_id}/remote/episode_group/{group_id}/groups?translate=false` —— 子组列表
- 子组增改同形：`POST/PUT …/episode_group/{group_id}/groups`（payload: `{"name","order","episode_count"}`，改时带 `id`）
- `GET/POST/PUT/DELETE /tv/{tv_id}/remote/episode_group/{group_id}/{sub_group_id}/episodes` —— 子组成员单集（读出成员→改→整体回写）

### 人物 / 演职员（cn-media-to-tmdb 实测）

- `GET /search/remote/person?flatten_known_for=true` —— 搜人物，返回 `name/bson_id/credit_id/profile_path/known_for`。**同名多，必须核对 known_for 作品，不能取第一条。**
- `GET/POST/PUT/DELETE /tv/{tv_id}/remote/seasons/cast?translate=false` —— 主演
- `GET/POST/PUT/DELETE /tv/{tv_id}/remote/seasons/crew?translate=false` —— 职员

### 图片上传（全部已实测）

图片页 URL 与媒体类型/图片类型对应关系：

| 页面 | media_type | type |
|---|---|---|
| `/movie/{id}/images/posters` | `Movie` | `poster` /（背景页 `backdrop`、标志页 `logo`） |
| `/tv/{id}/images/posters` | `TvSeries` | `poster` |
| `/tv/{id}/season/{n}/images/posters` | `TvSeason` | `poster` |
| `/tv/{id}/season/{n}/episode/{n}/images/backdrops`（页面标题「剧照」） | `TvEpisode` | `still` |
| 人物头像页（待实测，见下节） | `Person`? | `profile`? |

**先从页面 HTML 提取上传配置**（Kendo 上传组件内嵌）：

```js
const html = document.documentElement.outerHTML;
const mediaId = html.match(/media_id:\s*['"]([a-f0-9]{12,40})['"]/)[1];
// 同一 400 字符窗口里还有 media_type: '…' 与 type: '…'
```

（`scripts/browser.py` 的 `upload_config` op 已封装。）

**上传 `POST /image`（multipart）**，易错点（全部实测踩过）：

- 图片字段名必须是 **`upload_files`**（用错 500/404）。
- 必带表单字段：`media_id`、`media_type`、`type`、`translate=false`。
- 不要手动设 `Content-Type`（让浏览器自动带 multipart boundary）；加 `X-Requested-With: XMLHttpRequest`。
- 成功响应 `{"success":true,"html":"<li …>…"}`；校验失败 `{"failure":{"errors":[…]}}`；401/403 = 掉登录；200 但非 JSON = WAF/登录页。

```js
const blob = await (await fetch('http://127.0.0.1:8899/poster.jpg',{mode:'cors'})).blob();
const fd = new FormData();
fd.append('upload_files', blob, 'poster.jpg');
fd.append('media_id', mediaId);
fd.append('media_type', 'TvSeries');
fd.append('type', 'poster');
fd.append('translate', 'false');
const r = await fetch('/image', {method:'POST', credentials:'same-origin', body:fd, headers:{'X-Requested-With':'XMLHttpRequest'}});
```

上传图片先过 `scripts/prep_image.py transform`（官方规范裁剪/禁放大/去黑边）。

## 延伸端点（2026-09-21 登录态实测核对）

- **条目主资料**：`/tv/{id}/remote/primary_facts`（编辑页 `primary_facts_form` 的提交地址；写请求实测通过）。主创 `/tv/{id}/remote/created_by`、关键词 `/tv/{id}/remote/keywords`、视频 `/tv/{id}/remote/videos`（三者 GET 均 200 JSON，读取已实测；写入为 kendo `data=JSON` 同款形态）。
- **翻译（zh-CN 中文化）**：编辑页带 `?translate=true&language=zh-CN` 重开，字段名变语言后缀（`zh_CN_name`、`zh_CN_overview`），保存仍提交 `/tv/{id}/remote/primary_facts`（同一表单端点）。语言选择弹窗 = `/translation-popup?media_type=TvSeries&media_id={条目bson_id}&referral=…`。单集翻译同理（集的 primary_facts + 语言后缀字段）。语言选择弹窗里的 `default_language_popup`/`fallback_language_popup` 是「默认语言/回退语言」设置。
- **别名（alternative titles）**：读取与创建同端点 `GET/POST /tv/{id}/remote/alternative_titles?translate=false`（GET 返回数组：`{id, iso_3166_1, iso_639_1, native_name, title, type, locked}`；POST 为 kendo `data=JSON` 创建）。单条锁定/解锁 `POST /tv/{id}/remote/alternative_title/lock?translate=false`。编辑页导航 `?active_nav_item=alternative_titles` 区块内有「添加别名」按钮。
- **外部编号（IMDb/豆瓣等）**：`/tv/{id}-{slug}/remote/external_ids?translate=false`——**纯写端点**（kendo grid：POST/PUT/DELETE，GET 是 404；现值由编辑页预渲染）。注意路径必须带 slug（`/tv/1396-breaking-bad/...`），单集/季系端点不带 slug 也行。条目编辑页各区块统一用 `?active_nav_item=external_ids` 等锚点切换。
- **人物头像**：图片页 URL 是 `/person/{id}/images/profiles`（复数 profiles，单数 404）。页面内嵌配置 `media_type: 'Person', type: 'profile'`，上传与 `/image` 同款 multipart（upload_files/media_id/media_type/type/translate）。已实测到配置层。
- **建条向导**：`/tv/new`、`/movie/new` 存在；查重端点已实锤 `GET /tv/duplicate_check?name=…&original_name=…` → `{"success":true}`（200 JSON）；`/tv/content_check` 对 GET 是 404（POST 型，形态需现场）。向导本体是 JS 分步表单（第一步防重名搜索），最终提交请求需首次真实建条时现场记录一次再补进本文档。
- **图片删除**：**网页版没有自助删图入口**（代码级确认：画廊组件 `tmdb-image-gallery-*.js` 是纯查看器，卡片/悬停/灯箱/主应用 JS 全链路无删除端点；官方 Talk 亦确认只有管理员能删）。官方清理渠道 = 条目页「反馈」举报：`GET /report/new?media_type=TvSeries&media_id={bson}&request_url={页面路径}` 取表单 → POST 表单 action（urlencoded，非 data=JSON）→ `{"success":true}`。字段：`authenticity_token/session_language/item/item_id/item_type/request_url/type_of_problem/extra_details/public_report`；type_of_problem 单选值：`duplicate | bad_image | design_issue | offensive_or_spam | incorrect_content`，删自己误传的图选 `bad_image` 并把图片直链与理由写进 `extra_details`，管理员人工处理（实测提交 success）。
- **通用坑**：条目级端点带 `{id}-{slug}` 形态（从编辑页 JS 抄最稳）；带查询串 `?translate=false`；写请求 `data=URL编码JSON`；响应 `failure.errors` 数组是官方校验文案（如分辨率不足），按文案修数据。

## 登录与会话（实测坑）

`browser.py login` 打开 `https://www.themoviedb.org/login` 人工登录一次（先关 Cookie 弹窗 `#onetrust-accept-btn-handler`；自动填表时注意 type 后**密码框可能被前端清空**，提交前校验 `document.getElementById('auth_password').value.length` 非 0，否则报「We couldn't validate your information」）。Google 登录在 WebView 里会被拒，用账号密码方式。

两个实测坑（browser.py 已处理，手工调接口时要知道）：

1. **登录态校验必须看状态码**：未登录访问 `/settings/api` 是**同 URL 返回 401 的登录页**（不重定向）——看 URL 判断会误报已登录。正确判据：`fetch('/settings/api')` 返回 200 才是登录态。
2. **会话 cookie 是会话级**：浏览器一关就丢（登录后第一次 run 可能成功、之后就 401）。login 成功后必须把 cookies 存盘、run 时注入（browser.py 已内置：`tmdb-cookies.json`）。

## 响应错误分类（browser.py 已封装）

| 现象 | 含义 | 处置 |
|---|---|---|
| 401/403 | 未登录/无权限（非编辑者账号） | 重新登录；确认账号有贡献权限 |
| 200 非 JSON（HTML） | WAF 拦截页或登录页 | 停；`browser.py login` 后重试 |
| `{"failure":{"errors":[…]}}` | TMDB 校验失败（如分辨率不足、字段超长） | 按 errors 修数据，不要重试原请求 |
| 无 `success` 标记 | 空响应/接口变化 | 单条重试一次，仍失败停下核对页面 |

## 可用片段

### 新增一集（页面内 fetch；browser.py fetch op 等价）

```js
const payload = {episode_number:2, name:'第2集', overview:'', air_date:'', runtime:13, locked_fields:[]};
const r = await fetch('/tv/330017/season/1/remote/episodes?language=zh-CN&translate=false', {
  method:'POST', credentials:'same-origin',
  headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
  body:new URLSearchParams({data: JSON.stringify(payload)})
});
console.log(await r.text());  // {"success":true,"id":…}
```

### 更新已有集（先读再改）

```js
const list = await (await fetch('/tv/330017/season/1/remote/episodes?translate=false',{credentials:'same-origin'})).json();
const cur = list.find(e => e.episode_number === 1);
const payload = {episode_number:1, name:'正式标题', overview:'简介', air_date:'2009-07-15', runtime:45, locked_fields:[],
                 id:cur.id, season_number:1, show_id:330017, bson_id:cur.bson_id,
                 production_code:cur.production_code||'', vote_average:cur.vote_average||0, vote_count:cur.vote_count||0};
await fetch('/tv/episode/'+cur.id+'/remote/primary_facts?series_id=330017&episode_number=1&translate=false', {
  method:'POST', credentials:'same-origin',
  headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
  body:new URLSearchParams({data: JSON.stringify(payload)})
});
```
