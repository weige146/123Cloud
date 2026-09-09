# 油猴脚本

123Cloud 的配套浏览器脚本（Tampermonkey / 油猴），每个脚本一个同名文件夹，内含脚本本体、README 与测试。

- **[123 助手](./123-helper/)**（[Greasy Fork 主页](https://greasyfork.org/zh-CN/scripts/592236-123-%E5%8A%A9%E6%89%8B)）：增强 123 云盘网页端——全盘搜索、批量重命名、TMDB 媒体整理、文件清理、秒传工具箱（含二级秒传短链接）、批量分享与一键投稿、影库搜索转存。适用 123pan.com / .cn、123684 / 123865 / 123912 / 123635 等域名 + 公开分享页。
- **[TMDB 助手](./tmdb-helper/)**（[Greasy Fork 主页](https://greasyfork.org/zh-CN/scripts/594907-tmdb-%E5%8A%A9%E6%89%8B)）：themoviedb.org 页面助手——多数据源搜索（豆瓣 / 百度百科 / IMDb）、资料一键复制、来源海报一键直传、百科分集剧情抓取、悬浮球右键复制 `{tmdbid=…}` 标记；季编辑页批量单集、图片直传、剧集组管理（只查资料，不回写页面）。适用 themoviedb.org。

- **安装**：浏览器装 [Tampermonkey](https://www.tampermonkey.net/) 后，把对应 `.user.js` 拖入浏览器即可；详细步骤、配置与使用见各自 README。
- **测试**：`node 油猴脚本/123-helper/tests/run-all.mjs`、`node 油猴脚本/tmdb-helper/tests/run-all.mjs`（纯 Node 运行；tmdb-helper 的测试依赖其 tests 目录下的 jsdom，缺失时先在该目录 `npm install`）。
- **版本号**：以各脚本头部 `@version` 为准；发布 Greasy Fork 时只在上一个已发布版本基础上 +1，不跳号、不回退（见根目录 [AGENTS.md](../AGENTS.md)）。

123 助手与 [123Cloud 桌面客户端](https://github.com/weige146/123Cloud)联动：网页端创建分享自动推送为客户端投稿草稿 / 搬运任务，客户端影库可随时搜索转存，详见 [123 助手 README](./123-helper/README.md)。
