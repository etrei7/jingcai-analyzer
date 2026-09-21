# AGENTS.md

竞彩足球分析应用（Flask + 前端直连竞彩 API）。本文件记录**改动本项目时必须遵守的约定**。

## 推送前自检（必跑）

任何改动后、推送前，务必运行：

```bash
python tools/selfcheck.py
```

必须输出 `RESULT: ALL OK`（退出码 0）。它检查：
1. 所有 `*.py` 语法（`py_compile`）
2. 所有 `*.py` 是否含损坏字符（U+FFFD / 私用区 U+E000–U+F8FF）
3. `templates/index.html` 最后一个 `<script>` 的圆括号/花括号/反引号是否配平

**改动分析/结算逻辑时，另跑回归测试**（无需数据库/网络）：

```bash
python tools/test_logic.py
```

覆盖：数学模型归一化（泊松/Dixon-Coles/去水/凯利）、各玩法结算（1X2/AH/CS/HTFT）、半全场赔率反推、串关组合、缺字段/None 不崩。必须 `RESULT: ALL OK`。

> 这些检查源自真实事故：`api_football_client.py` 曾因"换行丢失 + 损坏字符"导致 `SyntaxError`，模块**始终无法导入**，API-Football 富化静默失效很久无人察觉。

## 部署

- 线上：PythonAnywhere（`https://LS7.pythonanywhere.com`），源码在 `/home/LS7/jingcai-analyzer`。
- 本机**无 git**（推送用 GitHub Contents API）。
- 服务器拉取**必须清代理**（默认代理已失效）：
  ```bash
  cd ~/jingcai-analyzer && git -c http.proxy= -c https.proxy= pull
  ```
  拉取后到 **Web → Reload**，浏览器 **Ctrl+F5**。

## 关键约束与坑（务必避免）

- **竞彩官方数据**：服务器端直连 `sporttery.cn` 会 **403**（白名单限制）。竞彩数据只能由**浏览器前端直连** `webapi.sporttery.cn`（需 `Referer: https://m.sporttery.cn/`）。后端 `cache` 只走 Bzzoiro。
- **禁止用 PowerShell 以字面 `\n` 写 Python/JS 文件**：会破坏换行与中文编码，产生语法错误或损坏字符。
- **前端 JS 禁止嵌套模板字符串**（反引号套反引号）：会让整段 `<script>` 解析失败、页面全白。
- 所有对 `*.py` / `templates/*.html` 的写入都应保持 **UTF-8（无 BOM）**；`backtest.py` 现存 BOM 属历史遗留，改动时建议去掉。
- 数值展示防呆：赔率/赔率类字段做 `toFixed`/运算前先判空或转数值（`(odds||0)`），避免 `undefined.toFixed()` 报错。
- `analyze_single_match` 末尾的 cleanup 会 **pop 字段**；前端用到的字段（如 `travel_distance_km`、`ai_preview`）**不要**加入该删除列表。
- 后台任务/定时任务耗时操作要并发或加缓存，避免阻塞 PythonAnywhere 单 worker。

## 数据源

- **Bzzoiro**（后端可访问）：赛事、赔率、伤病、裁判、天气、AI 预览、积分榜等。
- **API-Football**（`API_FOOTBALL_KEY`）：积分榜排名/状态/主客场/净胜球（免费版 100 次/天，已做内存缓存与限流降级）。
- **国际博彩公司赔率**（Bzzoiro `/odds/`，`market=1x2|asian_handicap|double_chance|draw_no_bet`）：用于"国际盘口对比/价值差"。注意单次返回**硬上限 50 条**。

## 回测闭环

- 表 `bt_*`（`odds_snapshots` / `bets` / `summary`），定时任务见 `scheduler.py`。
- 结算按 Bzzoiro `match_id` 精确查；流水线 `data_pipeline.run_full()` 采集+结算。
- 战绩汇总 `compute_summary` 有 60s TTL 缓存，结算后调 `clear_summary_cache()`。
- **历史预测结算**（`scheduler.daily_settlement`）：数字 `raw_event_id` 按 Bzzoiro 事件ID精确查；
  竞彩编号（如「周日001」）走 `bizzoiro_client.fetch_finished_events()` 拉近几日已完赛事件按队名
  规约匹配。解决竞彩预测永远「待验证」的问题。`history._save_history` 有 3000 条容量上限。
- **只统计竞彩官方场次**：`bt_bets.jingcai` 标记 + `_migrate_bt_bets` 自动加列。
  前端 `/api/analyze` 处理竞彩场次时经 `data_pipeline.record_jingcai_plays` 写入（用 `bz_event_id`
  赛后结算），并按 (match_id, play_type) 去重。`compute_summary(jingcai_only=True)` 只聚合竞彩记录；
  `data_pipeline.run_pipeline` 已停用 Bzzoiro 全量采集。旧非竞彩记录保留但不计入战绩（可恢复）。
- **串关级追踪**：表 `bt_parlays`，逻辑在 `parlay_tracker.py`（`record_parlays` 记录 /
  `settle_parlays` 结算 / `parlay_summary_cached` 汇总，60s TTL）。单场命中率≠串关命中率。
  结算任一腿未中即整套未中；腿的结算玩法/pick 由 `analysis.py::_infer_leg_meta` 从 option 推断
  （新增盘口串关选项时需同步该函数与 `parlay_tracker.eval_leg`）。接口 `/api/parlay-stats`。

## 死代码（已清理）

以下无引用函数**已删除**（经 AST 静态分析 + 本地 `import app` / test_client 验证无碍）：

- `bizzoiro_client.py::_format_time`、`fetch_actionable_results`、`fetch_same_odds_stats`、`_monte_carlo_same_odds`
- `analysis.py::_goal_distribution`、`_stable_random`
- `jingcai_scraper.py::_get_share_token`
- `backtest.py::persist_summary`
- `history.py::verify_prediction`

新增删除：
- `models.py::Match` / `Recommendation` / `TeamStat`（旧表模型，全站无引用；仅保留 `db`）
- `config.py::REFRESH_INTERVAL`（未使用）
- `templates/index.html::fetchData`（未调用）

保留（有意）：
- `odds_tracker.py::track`（已被 `track_many` 取代，保留作 API 兼容）
- `scheduler.py::warmup_cache`（手动预热入口）
- HTTP 接口 `/api/realtime`、`/api/team-data`、`/api/jingcai`、`/api/jingcai-token`
  （前端未调用，但属对外 API 面，保留以防外部集成依赖）

> 注意：`data_generator.generate_matches` 经 `from data_generator import generate_matches as generate_mock_matches` **别名导入**使用，**不可删**（静态分析会误报为未引用）。


