# TMDB 官方 v3 API（只读）

定位条目、拉现值做 diff、核对写入结果全走 v3 API（只读；写走 `tmdb-web-api.md`）。免费 Key：登录 TMDB → 设置 → API 申请 v3（就是浏览器里那个账号）。Key 只存本地任务文件，不进仓库。

## 基本形态

```
GET https://api.themoviedb.org/3/{path}?api_key=<KEY>&language=zh-CN
```

`language=zh-CN` 让标题/简介回中文译名；做 diff 时建议同时拉 `language=zh-CN` 与默认（原文）两份。

## 常用端点

| 用途 | 端点 |
|---|---|
| 综合搜索 | `/search/multi?query=…&page=1&include_adult=false` |
| 剧集/电影搜索 | `/search/tv?query=…&first_air_date_year=YYYY`、`/search/movie?query=…&year=YYYY` |
| IMDb 反查 | `/find/tt1234567?external_source=imdb_id` |
| 条目详情（含别名 `alternative_titles`、外部 ID `external_ids`、翻译 `translations` 拆开拉） | `/tv/{id}`、`/movie/{id}` |
| 别名 | `/tv/{id}/alternative_titles`（`/movie/…` 同） |
| 翻译现状 | `/tv/{id}/translations`（找 `iso_639_1: "zh"`、`iso_3166_1: "CN"` 是否已有 data） |
| 整季分集（集名/日期/简介/剧照） | `/tv/{id}/season/{n}` → `episodes[].{episode_number,name,air_date,overview,still_path,runtime}` |
| 剧集组列表 | `/tv/{id}/episode_groups` |
| 剧集组详情（分组结构+成员） | `/tv/episode_group/{group_id}` |
| 条目图片库 | `/tv/{id}/images`（`posters/backdrops/logos`，带 `file_path` 与尺寸） |
| 季图片库 | `/tv/{id}/season/{n}/images` |

图片直链：`https://image.tmdb.org/t/p/original{file_path}`（小图变体 `w342` 等可直接升 `original`）。

## 搜索排序经验（与 123 助手同源）

候选排序：精确同名 > 互含 > 年份相符 > 类型相符 > 热度（popularity）。单一类型搜不到时补 `/search/multi` 一轮；常规轮全空才带 `include_adult=true` 补一轮。**搜不到 ≠ 不存在**——先查别名（豆瓣「又名」/百科）再搜原名，见 rules.md 别名核对。

## diff 用法（先读后写）

录入前固定拉三份现值：

1. `/tv/{id}` —— 主条目有没有简介/日期/类型；
2. `/tv/{id}/season/{n}` —— 每集的 name/air_date/overview/still_path 现状；
3. `/tv/{id}/translations` 与 `/alternative_titles` —— 中文化与别名现状。

与来源数据（sources.md 取数产物）逐项比对后产出计划：

- `将新增`：TMDB 没有的集/别名/翻译/图片；
- `将修改`：TMDB 有但值缺失或明显错误（只补空位、纠错要给依据）；
- `将跳过`：两边一致的（绝不为"更好看"重写别人已填的内容）。

写完后用同一组端点回读核验，输出报告。
