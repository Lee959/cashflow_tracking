# Cashflow Tracker

家庭现金流追踪工具。目前已完成：**从 Gmail 自动抓取账单**（金额、到期日、PDF），算出**每月固定支出**，按月查看。
下一阶段接入 Plaid（TD / RBC / CIBC 的交易、余额、负债），补上不发邮件的扣款、做现金流预测和净资产追踪。

- **后端**：Python / FastAPI，提供 JSON API，并在后台定时抓取 Gmail 账单（`app/`）
- **前端**：React + TypeScript + Vite，liquid glass 风格界面（`frontend/`）
- 数据存在本机 SQLite（`~/Library/Application Support/HouseholdBills/`，不在会同步到 iCloud 的项目文件夹里），只在 `127.0.0.1` 上运行

## 页面

**Dashboard（`/`）**：按重要程度从上往下排

1. **这个月花了多少**（最大的数字）：本月已经付出去的账单金额（到期日已过的），进度条显示占本月到期总额的多少。
   右边是**还要付多少**、下一笔是什么、哪天、**用什么方式付**，以及本月总额比平常的月份多还是少
   （只和邮件账单的平均比，因为手动录入的支出不会出现在当月账单里）。
   可以往前翻月份（看过去的月份时显示那个月一共花了多少），但**不会显示未来月份**。
2. **当月账单列表**：每笔账单的到期日、金额、付款方式，可以手动修改，有 PDF 的可以直接打开。
3. **Bills by month**：最近 12 个月的柱状图，悬停看金额，点柱子跳到那个月。
4. **Your usual month**：平常每个月的固定账单（月平均），按账单列出。
   两个月一次的水费、一年两次的地税，都折算成每月金额。算法：最近 12 个月里每个账单的总额 ÷ 覆盖的月数。
   在 Settings 手动录入的固定支出（没有账单邮件的，比如 Enbridge、健身房）也算在里面，按频率折算成每月金额
   （例如一年 $120 = 每月 $10），列表里标注 *entered by hand*。

目前只统计 Gmail 里找到的账单；刷卡和银行账户里的其他支出要等接入 Plaid 后才能算进来。

**付款方式**（自动从银行扣款、自动从卡扣款、自己手动付）从邮件里自动识别：
- 账单邮件本身，比如 Hydro 的 "Automated Payment Withdrawal Date"、水费的 "registered with pre-authorized debit"
- 同一家公司的付款设置确认邮件，比如 Bell 的 "Confirmation - Change pre-authorized payments"，会读出银行或卡的名称和尾号（如 RBC ••321）

以最近的一封为准。每次自动抓取时会顺便检查有没有新的设置邮件。邮件里没写的（比如地税），可以在账单的 Edit 里设置 "Paid by"，手动设置优先于邮件。
悬停在付款方式上，可以看到它来自哪封邮件。

信用卡月结单**不计入总额**：卡上的消费以后从银行交易来，避免和用卡支付的账单重复计算。

**Settings（`/settings`）**：Gmail 连接、自动抓取状态和手动抓取、账单发件人管理、最近抓取记录，
以及 **Fixed costs without emails**：手动录入不发账单邮件的固定支出（名称、金额、频率：每月 / 每两个月 / 每季度 /
一年两次 / 每年，可选扣款日或下次到期日，类别）。可以修改、暂停（不计入总额）或删除。
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
   - Publishing status 是 **Testing** 时：要把用来收账单的那个 Gmail 地址加进 *Audience → Test users*，否则登录会被拦截（错误 403 access_denied）。
     Testing 模式下授权 **7 天就会过期**，到时在 Settings 里重新点 Connect Gmail。
   - 想免去每周重新授权：点 **Publish app** 改为 In production，不需要提交审核。
     登录时会看到 "Google hasn't verified this app"，点 *Advanced → Go to …* 继续即可（最多 100 个用户，家庭使用足够）。
   - OAuth Client 是 *Desktop app* 类型，回调地址 `http://localhost:8000/auth/google/callback` 不需要另外登记。

4. **启动**
   ```bash
   .venv/bin/python -m app
   ```
   会自动在浏览器里打开一个**一次性登录链接**（终端里也会打印出来）。第一次打开时设置 **Touch ID**，
   以后每次用指纹解锁；30 分钟不操作会自动锁上，顶栏的 🔒 可以随时锁定。
   然后 **Settings** → **Connect Gmail** → **Find bill senders** 勾选账单发件人 → 保存。之后会自动抓取。
   换了电脑或重置了 Touch ID：`.venv/bin/python -m app.cli reset-login`，再重新启动设置一次。

### 开发前端时

保持 `.venv/bin/python -m app` 运行，另开一个终端：
```bash
cd frontend && npm run dev
```
打开 http://localhost:5173，改代码会即时刷新，API 请求自动转发到 :8000。改完运行 `npm run build`，:8000 就会用新版本。
Google 登录完成后会跳回 :8000（回调地址固定在那里），这是正常的。

## 银行账户（Plaid）

Settings → **Bank accounts** → **Connect a bank**，会打开 Plaid 的窗口，在里面选银行、登录。连接后会显示每个账户
（支票、储蓄、信用卡、贷款、投资）和余额，交易记录会同步到本地数据库。

- **现在是正式环境（`PLAID_ENV=production`）**：卡片上标着 *Live*，连接的是你真实的银行。
  想用测试数据时把 `.env` 里改成 `PLAID_ENV=sandbox`（测试账号 `user_good` / `pass_good`）。
  每个连接都记录了它来自哪个环境，测试数据永远不会和真实账户混在一起显示。
- **申请的产品**（Pay As You Go，按每个连接每月计费）：Transactions（$0.30）、Liabilities（$0.20，信用卡结单余额、到期日、最低还款）、
  Investments（$0.53，只在银行支持时才加上并计费）。第一次连接会拉取 **24 个月**的交易历史，可能要几分钟。
- 每个连接有 **Sync**（刷新余额、交易和信用卡结单）和 **Remove**（在 Plaid 那边撤销授权，并删除本地的账户和交易）。
- 银行要求重新登录时（改了密码、授权过期），连接旁会出现 **Reconnect**，用 Plaid 的更新模式重新登录，保留原来的连接和历史。
- 同一个银行登录不能重复连接（会重复计费）；家里另一个人在同一家银行的账户可以另外连接。
- 信用卡下面会显示结单余额、到期日和最低还款额，逾期会标红。
- 每次自动抓取（每 6 小时）都会一起同步银行；只连了银行、没连 Gmail 也会照常同步。

**银行数据在 Dashboard 上的用法：**
- **Bank & card spending** 卡片：这个月从银行账户和信用卡花出去的钱（按类别）、收入、退款、最近的交易。
  计算时只看银行账户和信用卡（不看贷款、投资账户），**自己账户之间的转账和还信用卡的钱不算**
  （卡上的消费已经一笔笔算过了），退款单独列出，所以不会重复计算。
- **账单是否已扣款**：账单金额和到期日前后 7 天内的一笔银行支出对得上，账单旁就显示 ✓ Paid 和日期；
  交易名称里有账单公司名字的（比如 HYDRO OTTAWA）优先匹配。
- **首页大数字**：连上**真实银行**（production）后，自动改成"这个月一共花了多少"（所有账户和信用卡），账单是其中一部分。
  Sandbox 的测试数据只出现在 Bank & card spending 卡片里，标着 *Sandbox · test data*，不会混进首页大数字。

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
| Enbridge 煤气、Reliance 热水器、My Ontario account、Anytime Fitness | ✍️ 这个 Gmail 里没有账单邮件，在 Settings 手动录入 |
| Rogers Business Mobile | ✍️ 账单可能发到家里另一个人的邮箱，在 Settings 手动录入 |

现在就能补上：Settings → **Fixed costs without emails** → **Add**，填金额和频率，Dashboard 的总额马上就会包含。
以后某一项改成邮件账单（比如 Enbridge 开通了 eBill），记得把手动那条暂停或删除，否则会重复计算。

改成自动的办法：
- **Enbridge**：在 Enbridge My Account → My Bill Options 开通 **eBill 并附 PDF**，账单就会进 Gmail，程序能直接解析。
  My Account → Share My Data 还可以下载 24 个月的 Green Button XML 历史数据。
- **Reliance**：在安大略常常合并在 Enbridge 账单里，先看一下 Enbridge 账单上有没有。
- **Rogers**：转发到这个 Gmail，或以后支持连接第二个 Gmail。
- **其余几项**：先手动录入，以后用 Plaid 的银行交易核对。

## 命令行

```bash
.venv/bin/python -m app.cli sync-bills                   # 抓取本月和下个月（跟自动抓取一样）
.venv/bin/python -m app.cli sync-bills --month 2026-09   # 指定月份
.venv/bin/python -m app.cli sync-bills --last 12 --force # 重新下载最近 12 个月
.venv/bin/python -m app.cli list-bills --month 2026-09
.venv/bin/python -m app.cli discover-senders --days 365  # 找出像账单的发件人
.venv/bin/python -m app.cli reparse                      # 修改 bill_senders.toml 后重新解析（不访问 Gmail）
.venv/bin/python -m app.cli plaid-check                  # 检查 Plaid 凭证
.venv/bin/python -m app.cli plaid-sync                   # 刷新已连接银行的余额和交易
.venv/bin/python -m app.cli secure-secrets               # 把 .env 里的 Plaid 密钥移到钥匙串，收紧文件权限
.venv/bin/python -m app.cli security-check               # 一键安全检查（--quick 跳过依赖扫描）
.venv/bin/python -m app.cli scan-secrets --history       # 在整个 git 历史里找密钥和个人信息
.venv/bin/python -m app.cli install-hooks                # 安装提交前钩子
.venv/bin/python -m app.cli reset-login                  # 忘掉 Touch ID 和所有会话
.venv/bin/python -m pytest                               # 后端测试
cd frontend && npm run lint && npm run build             # 前端检查和构建
```

## 项目结构

```
app/                      后端（Python）
  main.py                 FastAPI 入口：Google 登录、PDF 附件、托管前端构建产物
  api.py                  JSON API（/api/dashboard、/api/settings、/api/sync、/api/manual-costs …）
  scheduler.py            后台自动抓取 + 抓取记录
  cli.py                  命令行
  config.py               读取 .env；数据目录
  keychain.py             macOS 钥匙串（加密密钥、Plaid 密钥）
  auth.py                 登录：一次性启动链接 + Touch ID（passkey）
  security.py             Host 检查、跨站请求拦截、登录要求、安全响应头、日志遮盖
  secret_scan.py          找密钥和个人信息（提交前钩子、security-check 用）
  security_check.py       一键安全检查
  secure.py               把 Plaid 密钥从 .env 迁到钥匙串，收紧文件权限
  google_auth.py          Gmail OAuth（refresh token 加密存储）
  gmail_client.py         Gmail API：搜索、解码正文、下载 PDF、限流重试
  bills/
    senders.py            读写 bill_senders.toml、生成 Gmail 搜索语句
    parser.py             金额和到期日提取
    service.py            按月同步、重新解析、查询
    dashboard.py          Dashboard 数据：固定支出、当月汇总、12 个月趋势
    manual.py             手动录入的固定支出（没有账单邮件的）
    discover.py           Find bill senders 的扫描和分类
  plaid_client.py         Plaid API：Link token、换取 access token、账户、交易同步、撤销连接
  banks.py                银行连接：保存（加密）、同步余额和交易、列出、删除
  spending.py             银行支出统计（排除转账和还卡），账单与扣款的匹配
  models.py               数据表（SQLite）
frontend/                 前端（React + TypeScript + Vite）
  src/pages/              Dashboard、Settings、FindSenders
  src/components/         FixedCosts、ManualCosts、MonthlyChart、BillsTable、TopBar
  src/styles.css          liquid glass 样式（浅色和深色模式）
config/bill_senders.example.toml
tests/                    后端测试（pytest）
```

## 安全

**银行部分**
- **只读**：只申请了 Transactions、Liabilities、Investments，都是查看类权限。这个应用**没有能力转账或动你的钱**
  （那需要 Auth / Transfer 之类的产品，从未申请，有测试保证这一点）。
- **银行密码从不经过这个应用**：登录在 Plaid 自己的窗口（cdn.plaid.com 的 iframe）里完成，应用只拿到一个 access token。
- **access token 加密存储**，加密密钥在 **macOS 钥匙串**里，不和数据库放在一起；任何 API 返回和日志里都不会出现 token（有测试检查）。
- **Plaid 的生产密钥在钥匙串里**，不在 `.env` 里（`python -m app.cli secure-secrets` 负责迁移）。
- 删除连接时会先在 Plaid 那边撤销授权，再删本地数据。
- 没有用 Plaid webhook，所以不需要对公网开放任何端口。

**登录**
- **一次性启动链接 + Touch ID**：`python -m app` 每次运行都生成新的密钥，只通过终端和自动打开的浏览器给你；
  打开后得到一个 HttpOnly、SameSite=Strict 的会话 cookie，再用 Touch ID（passkey，存在这台 Mac 的安全芯片里）解锁。
  没解锁时所有数据接口一律返回 401，所以**这台 Mac 上的其他程序**（包括沙盒应用、浏览器扩展）也读不到你的数据。
- 30 分钟不操作自动锁定；会话 7 天过期；数据库里只存会话令牌的哈希。

**整个应用**
- 服务只监听 `127.0.0.1`，只接受 Host 为 localhost 的请求（防 DNS 重绑定），其他网站发来的请求一律拒绝（防 CSRF），
  有内容安全策略（CSP），也不允许被其他网站嵌入（防点击劫持）。没有登录功能，**不要部署到公网**。
- 数据库和账单 PDF 在 `~/Library/Application Support/HouseholdBills/`，不会同步到 iCloud，权限只有你自己能读（700/600）；
  FileVault 已开启，关机或锁屏时整盘加密。
- `.env`、`secrete/` 只有你自己能读；它们和 `config/bill_senders.toml` 都在 `.gitignore` 里。
- Gmail 只申请了只读权限（`gmail.readonly`），抓账单时只搜索已配置的发件人。
- 没有 `/docs`、`/openapi.json` 这类接口说明页；接口返回禁止浏览器缓存；日志会自动遮盖 Plaid token 和启动链接里的密钥。
- **提交前钩子**（`install-hooks`）：提交里有密钥、Plaid token、`.env`/数据库文件，或你的个人信息就拒绝提交。
  个人信息清单在 `config/private_identifiers.txt`（不会被提交）。
- **一键检查**：`.venv/bin/python -m app.cli security-check` 检查文件权限、钥匙串、登录、FileVault、git 历史、依赖漏洞。
  建议每月跑一次。

**仍然存在的风险（如实说明）**
- 有"完全磁盘访问"权限、以你的身份运行的恶意程序，仍然可以直接读数据库文件和钥匙串项目（同一个 Python 读取时不会弹窗）。
  登录挡住的是"只能通过网络访问本机"的程序；电脑本身被完全控制时，这里的保护挡不住。
- GitHub 上已合并的 PR #1 仍然保留旧提交的引用；仓库是私有的，彻底清除需要 GitHub 客服或删库重建。
- 交易和余额明文存在本地数据库里；靠 FileVault 和文件权限保护，没有额外加密。
- `.env` 里还有 Google OAuth 客户端信息（不是银行相关，Google 也不把桌面应用的客户端密钥当机密），它仍在 iCloud 同步的文件夹里。
- Time Machine 备份会包含数据库；建议备份盘开启加密。

## Plaid 状态（2026-10-12 查看 dashboard）

- **Production 已批准**，计费方式 Pay As You Go（无月最低消费）。
- 已开通：Transactions、Recurring Transactions、Transactions Refresh、Investments、Liabilities、Enrich；
  应用只用 Transactions、Liabilities、Investments。
- **Statements（银行月结单 PDF）只支持美国银行**，TD / RBC / CIBC 用不了，所以账单走 Gmail。

## 下一步

- [ ] Enbridge 开通 eBill 附 PDF；Rogers 账单转发到这个 Gmail
- [x] 手动录入没有账单邮件的固定支出（Settings → Fixed costs without emails）
- [ ] Green Button XML 导入（补齐 Enbridge 和 Hydro 的历史数据）
- [ ] 开机自动启动服务（macOS launchd），这样电脑开着就能一直自动抓取
- [x] Plaid Link 接入（Sandbox）→ 账户、余额、交易同步
- [x] 自动抓取时一起同步银行交易
- [x] 用银行交易核对账单是否真的扣款；把刷卡和其他支出算进"这个月花了多少"（真实银行连上后生效）
- [x] Plaid production 批准，切换到正式环境
- [ ] 连接真实的 TD / RBC / CIBC
- [ ] 净资产：用 Investments 和贷款余额算总资产减总负债
- [ ] 每日余额快照（Plaid 不提供历史余额，越早开始越好）
- [ ] 未来 30–60 天现金流预测
