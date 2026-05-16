# Binance Alpha Futures Telegram Monitor

这个脚本每 5 分钟扫描一次 Binance Alpha 币种，并通过 Telegram 推送命中结果。

默认筛选条件：

1. Binance Alpha 币种
2. 已上线 Binance USD-M 永续合约
3. 链上 Top10 持仓比例大于 80%
4. 最近 5 根已收盘的 1 分钟合约 K 线，每根 quote volume 都大于 300,000 USDT

## 配置

```bash
cp .env.example .env
```

编辑 `.env`，填入：

```bash
TELEGRAM_BOT_TOKEN=你的机器人token
TELEGRAM_CHAT_ID=你的chat_id
```

其他阈值也都可以在 `.env` 里调整。

## 试运行

```bash
python3 alpha_futures_telegram_monitor.py --once --dry-run
```

## 正式运行

```bash
python3 alpha_futures_telegram_monitor.py
```

脚本会持续运行，并按 `SCAN_INTERVAL_SECONDS=300` 每 5 分钟扫描一次。

## 后台运行示例

```bash
nohup python3 alpha_futures_telegram_monitor.py >> monitor.log 2>&1 &
```

停止时可以用：

```bash
ps aux | grep alpha_futures_telegram_monitor.py
kill <PID>
```

## 网站同步

脚本每轮扫描都会同步网站数据，前端会展示最新结果、扫描走势和历史扫描明细：

```text
site/data/latest.json
site/data/history.json
```

本地查看仪表盘：

```bash
cd site
python3 -m http.server 8080
```

然后打开：

```text
http://127.0.0.1:8080
```

如果你的个人网站有后端接口，可以在 `.env` 里配置：

```bash
WEB_SYNC_WEBHOOK_URL=https://your-domain.example/api/binance-alpha-sync
WEB_SYNC_WEBHOOK_TOKEN=optional_bearer_token
```

未配置 webhook 时，脚本只写本地 JSON 文件。你可以用 rsync、GitHub Pages、Nginx 静态目录或自己的后端读取这些 JSON 文件。

### 自动提交网站数据到 GitHub

如果这个仓库已经接入 Vercel，可以让本地脚本每轮扫描后自动提交 `site/data/*.json`，Vercel 会跟随 GitHub 提交自动部署：

```bash
GITHUB_SYNC_TOKEN=你的GitHubToken
GITHUB_SYNC_REPOSITORY=zzwzzw-futurer/binance-alpha-oi
GITHUB_SYNC_BRANCH=main
GITHUB_SYNC_PATH_PREFIX=site/data
```

`GITHUB_SYNC_TOKEN` 建议使用 GitHub fine-grained personal access token，只授权当前仓库，并开启：

- Repository permissions: `Contents` -> `Read and write`
- Metadata: `Read-only`

脚本会把 `latest.json` 和 `history.json` 放在同一个 commit 里提交。`--dry-run` 模式下只写本地 JSON，不会提交到 GitHub。

## 部署

Vercel 部署配置在 `vercel.json`，输出目录指向 `site`。

发布到公开仓库前确认：

- `.env` 不会提交
- `monitor.log` 不会提交
- `.alpha_futures_monitor_state.json` 不会提交
- `site/data/*.json` 可以提交，用于展示公开网站上的最新扫描数据

## Telegram 获取方式

- `TELEGRAM_BOT_TOKEN`：在 Telegram 找 `@BotFather` 创建 bot 后获得。
- `TELEGRAM_CHAT_ID`：先给 bot 发一条消息，然后访问：

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
```

从返回 JSON 里的 `message.chat.id` 取值。

## 说明

- 脚本只使用公开市场数据接口，不会发起交易。
- Alpha 数据按已安装 Binance skills 文档中的 Web3 Alpha Rank 接口获取。
- Top10 持仓比例优先使用 Alpha Rank 返回的 `holdersTop10Percent`，缺失时会再查询 Token Dynamic 数据。
- 合约成交额使用 Binance USD-M Futures 1m K 线的 quote asset volume 字段。
- 为避免同一币种反复刷屏，默认同一链上合约地址和合约交易对 1 小时内只推送一次，可通过 `ALERT_COOLDOWN_SECONDS` 修改。
- 网站同步展示的是每轮扫描的全部命中结果，TG 推送仍受 `ALERT_COOLDOWN_SECONDS` 影响。
