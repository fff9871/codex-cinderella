# Codex Cinderella · 灰姑娘

赶在魔法消失前，用掉那张即将过期的 Codex 重置卡。

## 为什么写这个

本人刚重置了周额度，发现凌晨有一张充值卡要过期，于是把配置全部拉到最高，火力全开跑了一天。

结果到了凌晨，忘记使用充值卡了。

卡过期了，额度也跑掉了，本周直接从 **985 变成双非**。

所以写了这个脚本：你负责把配置拉满，灰姑娘负责看时间。

这里的「充值卡」指 Codex 的额度重置卡（rate-limit reset credit）。

## 它会做什么

- 每 60 秒查询一次卡片详情，进入过期前 1 小时的窗口后自动兑换。
- 优先兑换最早过期的卡；每轮最多处理一张，下轮重新查询。
- 兑换前保存幂等请求编号；网络超时、进程重启后复用同一编号重试。
- 读取不到卡片到期时间时记录错误，继续轮询，不猜测到期时间。
- 通过 PID 文件锁避免同一状态目录重复运行，日志自动轮转。

## 环境

macOS / Linux，Python 3.9+，无需 pip 依赖。需要已安装并登录的 `codex` CLI，且版本支持下述官方接口。

```bash
codex --version
codex login
```

使用本机 Codex 登录状态，不需要把 token 写进脚本或仓库。

脚本优先使用 `CODEX_BIN`，其次查找 `PATH`，最后自动查找 macOS `/Applications` 和 `~/Applications` 中的 ChatGPT / Codex 应用内置 CLI。后台启动时会固定解析后的路径，因此终端的 PATH 不包含 `codex` 也能使用应用内置版本。如果安装在其他位置，可手动指定：

```bash
CODEX_BIN="/absolute/path/to/codex" python3 cinderella.py start
```

## 启动方式

```bash
git clone https://github.com/fff9871/codex-cinderella.git
cd codex-cinderella

# 先执行一次只读检查
python3 cinderella.py once --dry-run

# 启动后台守护进程；退出终端后仍运行
python3 cinderella.py start

# 查看运行状态 / 观察日志 / 停止
python3 cinderella.py status
tail -f .cinderella/cinderella.log
python3 cinderella.py stop
```

前台运行、模拟后台运行和自定义轮询间隔：

```bash
python3 cinderella.py run
python3 cinderella.py start --dry-run
python3 cinderella.py start --interval 30
```

`--dry-run` 只查询并记录，不兑换。启动参数在启动时确定，修改前先 `stop`。默认到期阈值为 3600 秒，可通过 `--lead-seconds` 调整。

运行数据保存在脚本旁的 `.cinderella/`，已被 Git 忽略。可通过 `--state-dir /absolute/path` 指定其他目录；启动、停止、查看状态时使用同一目录。请保留 `state.json`，它保存兑换重试编号。

电脑需要保持开机、联网且不休眠；后台模式不提供开机自启或进程崩溃后的自动重启。轮询和网络请求需要时间，实际兑换发生在进入 1 小时窗口后的首次成功查询，而非精确到秒。卡片详情可能因服务端返回上限而不完整；脚本只处理当前返回且有明确到期时间的卡。

重置会按服务端规则改变当前额度窗口。服务端返回 `nothingToReset` 时本轮不消耗卡，下轮重新尝试；无法保证网络故障或服务限制期间一定能在到期前兑换成功。

## 单独查询额度

```bash
python3 codex_quota.py
python3 codex_quota.py --details
```

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用模拟接口，不消耗真实卡片。

## 接口

使用 [OpenAI 官方 App Server 协议](https://learn.chatgpt.com/docs/app-server)：

- `account/rateLimits/read`：查询额度、卡片和到期时间。
- `account/rateLimitResetCredit/consume`：以卡片 ID 和幂等编号兑换。

本项目不附带任何账号凭据或个人额度快照。
