# 投稿自动路由重写方案（规则引擎 v2，客户端 1.4.8 + 油猴 1.4.5 批次）

> 状态：已实现，本地待维护者实测。落地代码见 `desktop/backend/app/submission_routing.py`。

## 一、现状与问题

旧路由 `submission.select_submission_channel` 是固定三槽 + 隐式优先级：

1. 只启用一个频道 → 直接用它；
2. 草稿带手动 `channelId` → 用它；
3. 识别出发布组且不在 `publicReleaseGroups` 白名单 → `releaseGroupChannelId`；
4. 否则完结（电影 / TMDB 季集齐全）→ `noReleaseGroupCompletedChannelId`；
5. 否则 → `noReleaseGroupUpdatingChannelId`；逐步兜到 role → isDefault → 首个启用频道。

痛点：链接类型（秒传 vs 分享）完全没进决策；「去除文件名发布组」勾上后秒传直投带 `skipReleaseGroup`、客户端整链跳过发布组识别，`metadata.releaseGroup` 恒空，**所有去组秒传帖一律被当"无发布组"**，白名单路由对秒传流整体失效；想加「动漫单频道 / 秒传帖分开发 / 某剧固定频道」都做不到；路由结果看不出命中原因。

## 二、目标模型：有序规则列表，首条命中即停

一条草稿 = 一组可求值的事实；配置 = 自上而下求值的规则列表 + 兜底频道。规则 = 若干条件（AND），每条件取值可多选（OR），缺键恒真。

事实字段（全部从现有草稿本地取得，零额外网络）：
- `linkType`：`share` / `fastlink`（`share.provider`）
- `mediaType`：`movie` / `tv` / `unknown`
- `completion`：`completed` / `updating` / `unknown`（沿用 `is_completed_media`）
- `releaseGroupState`：`detected` / `stripped` / `none` + `releaseGroups`（组名小写名单）+ `releaseGroupWhitelist`（∉/∈ 白名单）
- `quality`：8k/4k/1080p/720p/sd 分档（metadata.quality/resolution/resourceType 归并，不掺片名）
- `source`：WEB-DL / Remux / BluRay…（`compact_source` 变体名单）
- `submitter`：self / collaborator（`submitter.userId` vs owner）
- `tmdbId`：精确 ID 名单 / required（已匹配）

优先级钉死：手动 channelId > 单启用频道短路 > 规则列表 > `fallbackChannelId` > role 兜底 > isDefault > 首个。

## 三、兼容与迁移

- `routing_json` 存 `user_channel_configs`，无 schema 改动。`normalize_routing` 读时把旧三槽等价迁移成 v2 规则（迁移产物也过 `normalize_rule`，与用户提交同口径；v1 三键作影子字段保留给旧消费者），`session_store._normalized_routing` 在 read/write/list 三处统一走它。completion 迁移规则不加 unknown 条件，保住旧「未识别落连载」语义。
- 协作者权限不动：`submission_channel_candidates` / `draft_channel_allowed` 仍按候选频道过滤。

## 四、可解释性与 UI

- 决策写草稿 `routeDecision`（`apply_route_decision`），预览「📣 路由」与后台草稿列表显示「自动 · 命中「X」」。
- 管理端 `ChannelRoutingEditor.vue` 规则列表（上移/下移/启停/删、条件按 `GET /api/submission/routing/schema` 下发词表渲染、可选兜底频道、白名单沿用）；前端 `useChannelConfigEditor.ts` 有本地 `normalizeRoutingToV2` + `legacyShadowKeys` 回推影子键。
- 规则模拟器 `POST /api/submission/channel-owners/{uid}/simulate`：贴一段链接按真实识别链跑一遍看命中哪条、不落草稿（秒传本地 `inspect_fastlink` 不联网，分享链接实拉目录）。

## 五、油猴脚本侧（秒传去发布组联动，1.4.5 批）

- `generateSecondaryFastlink` 在洗名前用 `collectFastlinkReleaseGroups(files, configured)`（复用 `extractReleaseGroupName`）收集原始组 → `artifact.strippedReleaseGroups`。
- `buildFastlinkSubmissionMeta(text, mappings, {stripReleaseGroup, strippedReleaseGroups})` 写 `meta.releaseGroup` / `meta.releaseGroupStripped`。
- 客户端 `build_submission_draft` 读 `link["meta"]` 存 `draft.routingMeta`（只喂 `extract_routing_facts` 发布组三态，不进 metadata、不参与渲染、不污染 `SUBMISSION_META_OVERRIDE_KEYS`）。老脚本只带 skipReleaseGroup → 事实记 stripped，可单独配规则。

## 六、回归

- 后端 `test_submission_routing_rules.py`（事实/匹配 AND-OR/三态/首条命中/手动优先/单频道短路/兜底链/迁移等价/摘要列表形态/schema/模拟器路由）+ `test_submission_drafts.py::test_fastlink_direct_push_meta_release_group_feeds_routing`；全量 466 passed。
- 油猴 `fastlink-secondary.test.mjs`（meta 三态 + push 接线）、`fastlink-strip-release-group.test.mjs`（generateSecondaryFastlink 收集组）；全量 49/49。
- 版本：客户端 1.4.7（线上）→ 仓库 1.4.8；脚本线上 1.4.4、仓库 1.4.5（本批并入未发布的 1.4.5）。按铁律：本地 dmg+zip 实测通过前不 commit / push / tag。
