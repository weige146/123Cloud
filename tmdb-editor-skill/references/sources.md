# 国内来源取数

八个来源的接口与字段。图床几乎都有 Referer 防盗链，下载带对应 Referer + 桌面 UA。**大部分可直接用 `scripts/fetch_source.py`**（下表标 ✓ 的子命令），本文供手工核对与失效排查。

通用下载模板：

```sh
curl -sL -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) …Chrome/120.0 Safari/537.36" -e "<Referer>" -o out.jpg "<图片URL>"
# 或
prep_image.py download "<图片URL>" -o out.jpg      # 自动 Referer 表 + 豆瓣/bkimg 原图升级
```

| 来源 | 取数方式 | fetch_source.py |
|---|---|---|
| B站番剧 | 公开 API | ✓ `episodes`/`bilibili-search` |
| 豆瓣 | suggest + rexxar + 桌面页补全 | ✓ `douban-suggest`/`douban-detail`/`douban-imdb` |
| 百度百科 | 搜索页 + 词条 HTML | ✓ `baike-search`/`baike-detail`/`baike-episodes` |
| 爱奇艺 | pcw-api + 移动端页挖专辑 ID | ✓ `episodes` |
| 腾讯视频 | fcgi-bin JSONP | ✓ `episodes` |
| 芒果TV | pcweb.api | ✓ `episodes` |
| 优酷 | openapi（老旧，可能停用） | ✓ `episodes` |
| 红果短剧 | SSR 页 `_ROUTER_DATA` | ✓ `episodes`/`hongguo-search` |

## 哔哩哔哩（B站番剧/影视）

短链 `b23.tv/epXXXX` 先展开。搜索 `GET api.bilibili.com/x/web-interface/search/type?search_type=media_bangumi&keyword=…`（**需要 buvid cookie**：缺失返回风控页，先访问一次主站引导 cookie——脚本与 CLI 已自动处理）；详情 `GET api.bilibili.com/pgc/view/web/season?season_id=…`（`ep_id=` / `media_id` 变体见 CLI）。无需登录。关键字段：

- `result.title` 番名、`result.evaluate` 简介、`result.cover` 竖版封面、`result.actors`（「角色：演员」换行分隔）。
- `result.episodes[]`：`title`(集号)、`long_title`(集名)、`cover`(横版帧 ~960×600)、`pub_time`(Unix 秒，首播时间戳，**优先用它**)、`duration`(毫秒)。
- `badge==="预告"` 的条目剔除、集号按顺序重排连续（CLI 已做）。

图床 Referer `https://www.bilibili.com/`，域名 `i0/i1.hdslb.com`。

## 豆瓣

- 搜索建议 `GET movie.douban.com/j/subject_suggest?q=…`（返回 id/title/sub_title/year/type）。
- 详情主路 `GET m.douban.com/rexxar/api/v2/movie/{id}`（**Referer 必须是 m.douban.com 否则 400**；无需登录；剧集 ID 会 301 到 tv，跟随即可）。`intro/aka/pubdate/durations/directors/writers/actors/genres/countries/languages/episodes_count/rating/pic.large`。
- rexxar 缺字段是常态（编剧恒空、语言/又名常缺）→ 桌面页 `movie.douban.com/subject/{id}/` HTML 补空位（登录态字段最全；匿名是 JS 壳，解析不出标题就跳过）。CLI 已实现「只补空不覆盖」合并。
- 风控特征：HTTP 200 但最终 URL 302 到 `sec.douban.com`/`/accounts/login`。CLI 会明确报错；先在浏览器开一次豆瓣再试。
- IMDb 反查 `www.douban.com/search?cat=1002&q=tt1234567`，从结果提取 `movie.douban.com/subject/{id}`。
- 海报：`img*.doubanio.com`，Referer `https://movie.douban.com/`；`/view/photo/…/public/` 变体只有 ~480px，CLI 自动升 `/raw/` 原图。豆瓣「又名」字段是**别名核对的权威来源**。

## 百度百科

- 搜索 `baike.baidu.com/search?word=…`；词条直连 `baike.baidu.com/item/{名称}/{id}`。页面多代结构并存（新版 React + 旧版 SSR），解析全做多选择器兜底，命中不了明确报错。被风控（安全验证页）时先在浏览器开一次百科再试。
- 词条详情字段：导演/编剧/主演/类型/制片地区/语言/集数/首播/每集长度/出品方/播出平台 + 海报（`bkimg.cdn.bcebos.com`，Referer `https://baike.baidu.com/`；缩放参数去掉 query 即原图）。
- **分集剧情表格**是国内剧集名/简介的重要来源：标题含「分集剧情」向后找表格；表头特征（集数/剧情/分集/集名）兜底；每行剥 `第N集` 取集号，剩余单元格里最长的当简介、次长的当集名、能解析成日期的当播出日。部分剧的分集剧情在独立词条（搜「剧名 分集剧情」）。

## 爱奇艺

专辑页 HTML 挖 albumId：`/a_` 页 `data-album-id="…"`；`/lib/m_` 页 `movlibalbumaid="…"`；单集页（`/v_…`）桌面版是 JS 空壳 → 抓 **移动端 SSR 页**（`m.iqiyi.com`，手机 UA）里的选集数据。然后：

- 专辑信息 `GET pcw-api.iqiyi.com/album/album/baseinfo/{aid}`（name/description/imageUrl）
- 分集 `GET pcw-api.iqiyi.com/albums/album/avlistinfo?aid=…&page=N&size=100`（翻页直到不足 100）——`epsodelist[]`：`order`(集号)、`subtitle`(集名)、`period`(日期)、`duration`("mm:ss"/"hh:mm:ss")、`imageUrl` + `imageSize` 数组（裸 URL 只有 120×160，按面积最大档拼 `_W_H` 后缀升图，CLI 已做）。

图床 `pic*.iqiyipic.com`，Referer `https://www.iqiyi.com/`。

## 腾讯视频

封面页链接取 cid（`v.qq.com/x/cover/{cid}…`）：

- 索引 `GET data.video.qq.com/fcgi-bin/data?otype=json&tid=431&idlist={cid}&appid=10001005&appkey=0d1a9ddd94de871b`（**JSONP**，剥 `QZOutputJson=` 前缀）→ `video_ids` 数组
- 详情 `GET union.video.qq.com/fcgi-bin/data?otype=json&tid=682&appid=20001238&appkey=6c03bbe9658448a4&idlist={逗号分隔,30 个一批}` → 每项 `fields`：`episode`(集号)、`second_title`(集名，剥「剧名_01」尾巴)、「正片」才收（`category_map` 含「正片」）、`video_checkup_time`(日期)、`duration`(秒)、`pic160x90`（`/160`→`/1280` 升图）。

图床 `puui.qpic.cn`/`vpic.video.qq.com`，Referer `https://v.qq.com/`。

## 芒果TV

路径第一个数字段是 collection_id（`w.mgtv.com/b/419629/17004788` → 419629）。`GET pcweb.api.mgtv.com/episode/list?_support=10000000&version=5.5.35&collection_id=…&page=N&size=50`，`data.list[]`：只收 `isIntact==="1"` 正片、`t1`(集号)、`t2`(集名)、`ts`(日期)、`time`("mm:ss")、`img`（剥 `_xx` 后缀再带 `?x-oss-process=image/resize,w_1280` 升到 ≥1280 原图）。图床 `*.hitv.com`，Referer `https://www.mgtv.com/`。

## 优酷

`?s=showId` 优先；否则 `id_XXX.html`/`vid=` 取视频 ID 反查 show。开放接口（客户端 ID 是公开的华为壳包参数，接口老旧可能停用）：

- `GET openapi.youku.com/v2/videos/show.json?video_id=…&ext=show&client_id=0dec1b5a3cb570c1&package=com.huawei.hwvplayer.youku`
- 分集 `GET openapi.youku.com/v2/shows/videos.json?show_id=…&show_videotype=正片&page=N&count=30&…`——`seq`(集号)、`rc_title`(集名)、`published`(日期)、`duration`(秒)、`bigthumbnail`。

图床 `*.ykimg.com`，Referer `https://v.youku.com/`。

## 红果短剧

番茄系 SSR 页，`hongguoduanju.com/detail?series_id=…` 页面内嵌 `window._ROUTER_DATA`（括号配对截 JSON，跳过字符串内花括号——CLI 已实现）：`loaderData.detail_page.seriesDetail`：`series_name/series_intro/series_cover/episode_cnt/vid_list`（红果集名日期全空，集号即顺序）。搜索 `hongguoduanju.com/search/{关键词}` 同法解析 `searchList`。短剧在 TMDB 缺条目多，是录入重点来源。

## 高清横图/海报兜底（来源图不够清晰时）

- 百度图片 JSON（需浏览器上下文或完整 cookie）：`image.baidu.com/search/acjson?tn=resultjson_com&word=剧名&pn=0&rn=30&ie=utf-8`，结果 `data[].{width,height,middleURL,thumbURL}`，横版筛 `w>h 且 w≥1000`，海报筛 `h>w`。
- Bing 图片：结果 `a.iusc` 的 `m` 属性 JSON 含 `murl`(原图)/`mw`/`mh`；`&qft=+filterui:imagesize-large` 过滤大图。
- 选图交给用户确认：把候选缩略拼 contact sheet（Pillow）让人挑，避免水印/logo/无关图（TMDB 剧照要求干净画面）。

## 数据落地

取数产物统一 JSON（`{platform,title,overview,cover,episodes:[{episodeNumber,name,airDate,runtime,overview,stillUrl}]}`），录入前先人工/对比检查；长任务落任务目录（分集 TSV + 图片 + 断点文件），便于核对与断点续传。
