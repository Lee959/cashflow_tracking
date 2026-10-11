# Cashflow Tracker

家庭现金流追踪工具。目前已完成：**从 Gmail 自动抓取账单**（金额、到期日、PDF），算出**每月固定支出**，按月查看。
下一阶段接入 Plaid（TD / RBC / CIBC 的交易、余额、负债），补上不发邮件的扣款、做现金流预测和净资产追踪。

- **后端**：Python / FastAPI，提供 JSON API，并在后台定时抓取 Gmail 账单（`app/`）
- **前端**：React + TypeScript + Vite，liquid glass 风格界面（`frontend/`）
- 数据存在本机 SQLite（`data/cashflow.db`），只在 `127.0.0.1` 上运行

## 页面

**Dashboard（`/`）**：只看最重要的信息

1. **Your fixed bills**：每月固定账单总额（以及一年合计），按账单列出每月平均。
   两个月一次的水费、一年两次的地税，都折算成每月金额。算法：最近 12 个月里每个账单的总额 ÷ 覆盖的月数。
2. **本月卡片**：本月到期总额、还剩多少要付、下一笔是什么和哪天，以及跟每月平均相比高还是低。
   可以往前翻月份，但**不会显示未来月份**。
3. **Bills by month**：最近 12 个月的柱状图，悬停看金额，点柱子跳到那个月。
4. **当月账单列表**：可以手动修改金额或到期日，有 PDF 的可以直接打开。

信用卡月结单**不计入总额**：卡上的消费以后从银行交易来，避免和用卡支付的账单重复计算。

**Settings（`/settings`）**：Gmail 连接、自动抓取状态和手动抓取、账单发件人管理、最近抓取记录。
**Find bill senders（`/settings/senders`）**：扫描 Gmail，勾选真正的账单发件人。

## 首次设置

1. **Python 依赖**（已装在 `.venv`，新环境才需要）
   ```bash
   .venv/bin/pip install -r requirements.txt
   ```

2. **前端依赖并构建**（需要 Node.js，已用 Homebrew 安装）
   ```bash
   cd frontend && npm install && npm run build
   ```

3. **Google OAuth 同意屏幕**（Google Cloud Console → Google Auth Platform）
   - 确认 Gmail API 已启用。
   - Publishing status 是 **Testing** 时：要把用来收账单的 Gmail（`you@example.com`）加进 *Audience → Test users*，否则登录会被拦截（错误 403 access_denied）。
     Testing 模式下授权 **7 天就会过期**，到时在 Settings 里重新点 Connect Gmail。
   - 想免去每周重新授权：点 **Publish app** 改为 In production，不需要提交审核。
     登录时会看到 "Google hasn't verified this app"，点 *Advanced → Go to …* 继续即可（最多 100 个用户，家庭使用足够）。
   - OAuth Client 是 *Desktop app* 类型，回调地址 `http://localhost:8000/auth/google/callback` 不需要另外登记。

4. **启动**
   ```bash
   .venv/bin/python -m app
   ```
   打开 http://localhost:8000 → **Settings** → **Connect Gmail** → **Find bill senders** 勾选账单发件人 → 保存。
   之后会自动抓取，不用再手动点。

### 开发前端时

保持 `.venv/bin/python -m app` 运行，另开一个终端：
```bash
cd frontend && npm run dev
```
打开 http://localhost:5173，改代码会即时刷新，API 请求自动转发到 :8000。改完运行 `npm run build`，:8000 就会用新版本。
Google 登录完成后会跳回 :8000（回调地址固定在那里），这是正常的。

## 自动抓取

- 网页服务运行时，后台**每 6 小时**抓一次本月和下个月的账单。时间间隔可以在 `.env` 里用 `APP_SYNC_INTERVAL_HOURS` 修改。
  每次抓取都记录在 Settings 的 *Recent fetches* 里；Dashboard 顶部只在出问题时（Gmail 未连接或抓取失败）显示提示。
- 重启服务不会多抓：如果距离上次抓取不到 6 小时就跳过。
- Settings → **Fetch now** 立即抓一次。**More options** 里可以补抓最近 N 个月，或在修改解析规则后用 *Re-parse* 重新解析已存的邮件（不访问 Gmail）。
- 遇到 Gmail 限流会自动等待重试。
- ⚠ 只在服务运行时才会抓取。电脑关机或服务没开时不会抓，下次启动后会自动补上。

## 账单解析逻辑

每封来自已配置发件人的邮件算一张账单，按**到期日所在的月份**归入（也就是钱实际扣走的月份；没有到期日时按收到邮件的月份）。
抓取某个月时会同时读取上个月收到的邮件，因为账单通常提前 2–3 周寄来。

金额和到期日按以下顺序提取（邮件正文和 PDF 附件文字都会读）：
1. `config/bill_senders.toml` 里为该发件人写的 `amount_patterns` / `due_date_patterns`（最准，按需添加）
2. 通用规则：`Amount due`、`Total due`、`New balance`、`Due date`、`Withdrawal date`、`Payment date` 等关键词后面的金额或日期
3. 邮件里只有一个金额时，直接用它

能处理的格式包括：负数抵扣（如 Hydro 的 `$-5.03`）、`2026 10 09` 这种空格分隔的日期（Bell）、没有 `$` 符号的金额（水费）、法语金额 `1 234,56 $`。

- "eStatement is ready" 这类只通知、不写金额的邮件标为 **statement notice**。
- 其他找不到金额的标为 **needs review**，可以在网页上手填；手动改过的不会被重新解析覆盖。
- 同一发件人的非账单邮件（营销、通知）用 `subject_includes` / `subject_excludes` 过滤。

### Find bill senders 的扫描逻辑

1. 用 bill、statement、amount due 等关键词搜索 Gmail，跳过 Promotions 分类。
2. 排除系统通知（Google Drive 共享等）、你自己的地址、个人邮箱、看起来是人名的邮箱、Re:/Fwd: 对话。
3. 按发件人分组，判断来信频率：每月、每两个月、一年几次、一次性（比如租车就是一次性）。
4. 判断发件人类型：账单、信用卡月结单、投资或银行账户月结单、消费或旅行收据。
5. 打开最新一封邮件，看里面有没有金额。
6. 同一家公司的提醒邮件（比如 Hydro 的 "Due date alert"）标为重复，建议跳过。

## 目前覆盖情况

| 账单 | 来源 |
|---|---|
| Hydro Ottawa（电） | ✅ Gmail，每月 |
| Ottawa 水费 | ✅ Gmail，每两个月 |
| Ottawa 地税 | ✅ Gmail，一年两次 |
| Bell 网络 | ✅ Gmail，每月 |
| Enbridge 煤气、Reliance 热水器、My Ontario account、Anytime Fitness | ❌ 这个 Gmail 里没有账单邮件 |
| Rogers Business Mobile | ❌ 账单可能发到 家里另一个人的邮箱 |

补上这几项的办法：
- **Enbridge**：在 Enbridge My Account → My Bill Options 开通 **eBill 并附 PDF**，账单就会进 Gmail，程序能直接解析。
  My Account → Share My Data 还可以下载 24 个月的 Green Button XML 历史数据。
- **Reliance**：在安大略常常合并在 Enbridge 账单里，先看一下 Enbridge 账单上有没有。
- **Rogers**：转发到这个 Gmail，或以后支持连接第二个 Gmail。
- **其余几项**：等接入 Plaid 的银行交易，或者手动录入。

## 命令行

```bash
.venv/bin/python -m app.cli sync-bills                   # 抓取本月和下个月（跟自动抓取一样）
.venv/bin/python -m app.cli sync-bills --month 2026-09   # 指定月份
.venv/bin/python -m app.cli sync-bills --last 12 --force # 重新下载最近 12 个月
.venv/bin/python -m app.cli list-bills --month 2026-09
.venv/bin/python -m app.cli discover-senders --days 365  # 找出像账单的发件人
.venv/bin/python -m app.cli reparse                      # 修改 bill_senders.toml 后重新解析（不访问 Gmail）
.venv/bin/python -m app.cli plaid-check                  # 检查 Plaid 凭证
.venv/bin/python -m pytest                               # 后端测试
cd frontend && npm run lint && npm run build             # 前端检查和构建
```

## 项目结构

```
app/                      后端（Python）
  main.py                 FastAPI 入口：Google 登录、PDF 附件、托管前端构建产物
  api.py                  JSON API（/api/dashboard、/api/settings、/api/sync …）
  scheduler.py            后台自动抓取 + 抓取记录
  cli.py                  命令行
  config.py               读取 .env
  google_auth.py          Gmail OAuth（refresh token 加密存储）
  gmail_client.py         Gmail API：搜索、解码正文、下载 PDF、限流重试
  bills/
    senders.py            读写 bill_senders.toml、生成 Gmail 搜索语句
    parser.py             金额和到期日提取
    service.py            按月同步、重新解析、查询
    dashboard.py          Dashboard 数据：固定支出、当月汇总、12 个月趋势
    discover.py           Find bill senders 的扫描和分类
  plaid_client.py         Plaid（目前只有连接测试）
  models.py               数据表（SQLite）
frontend/                 前端（React + TypeScript + Vite）
  src/pages/              Dashboard、Settings、FindSenders
  src/components/         FixedCosts、MonthlyChart、BillsTable、TopBar
  src/styles.css          liquid glass 样式（浅色和深色模式）
config/bill_senders.example.toml
tests/                    后端测试（pytest）
```

## 安全

- 服务只监听 `127.0.0.1`，没有登录功能，**不要部署到公网**。
- Gmail token 用 `secrete/app.key` 加密后再存进数据库；`.env`、`secrete/`、`data/`、`config/bill_senders.toml` 都在 `.gitignore` 里。
- Gmail 只申请了只读权限（`gmail.readonly`），抓账单时只搜索已配置的发件人。

## Plaid 状态（2026-10-11 查看 dashboard）

- Production 申请审核中，还差 **Security questionnaire**（Compliance Center 里 App profile 有 3 项待填）。
- 已申请：Transactions、Recurring Transactions、Transactions Refresh、Investments、Liabilities、Enrich。
- **Statements（银行月结单 PDF）只支持美国银行**，TD / RBC / CIBC 用不了，所以账单走 Gmail。
- Sandbox 凭证可用。在 Production 批准前，用 `PLAID_ENV=sandbox`（默认值）开发。

## 下一步

- [ ] Enbridge 开通 eBill 附 PDF；Rogers 账单转发到这个 Gmail
- [ ] 手动录入没有账单邮件的固定支出（Anytime Fitness、My Ontario 等）
- [ ] Green Button XML 导入（补齐 Enbridge 和 Hydro 的历史数据）
- [ ] 开机自动启动服务（macOS launchd），这样电脑开着就能一直自动抓取
- [ ] Plaid Link 接入 → 交易同步，用来核对账单是否真的扣款，并补上不发邮件的扣款
- [ ] 每日余额快照（Plaid 不提供历史余额，越早开始越好）
- [ ] 未来 30–60 天现金流预测
