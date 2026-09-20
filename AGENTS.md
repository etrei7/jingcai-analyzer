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
