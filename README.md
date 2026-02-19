# Grok 批量注册工具

批量注册 Grok 账号并自动开启 NSFW 功能。

## 功能

- 自动创建临时邮箱
- 自动获取验证码
- 自动完成注册流程
- 自动开启 NSFW/Unhinged 模式
- 注册完成后自动清理临时邮箱
- 支持多线程并发注册

## 文件说明

| 文件 | 说明 |
|------|------|
| `grok.py` | 主程序，批量注册入口 |
| `TurnstileSolver.bat` | Turnstile Solver 启动脚本 |
| `api_solver.py` | Turnstile 验证码解决器 |
| `browser_configs.py` | 浏览器指纹配置 |
| `db_results.py` | 验证结果存储 |
| `g/email_service.py` | 临时邮箱服务（freemail API） |
| `g/turnstile_service.py` | Turnstile 验证服务 |
| `g/user_agreement_service.py` | 用户协议同意服务 |
| `g/nsfw_service.py` | NSFW 设置服务 |
| `.env.example` | 环境变量模板 |
| `requirements.txt` | Python 依赖列表 |

## 依赖

- [freemail](https://github.com/user/freemail) - 临时邮箱服务（需自行部署）
- Turnstile Solver - 内置验证码解决方案

## 安装

### 方式一：使用 uv（推荐）

```bash
# 创建虚拟环境并安装依赖
uv venv
uv pip install -r requirements.txt
```

### 方式二：使用 pip

```bash
pip install -r requirements.txt
```

## 配置

复制 `.env.example` 为 `.env` 并填写配置：

```bash
cp .env.example .env
```

配置项说明：

| 配置项 | 说明 |
|--------|------|
| WORKER_DOMAIN | freemail 服务域名 |
| FREEMAIL_TOKEN | freemail JWT Token |
| YESCAPTCHA_KEY | YesCaptcha API Key（可选，不填使用本地 Solver） |
| PROXY_URL | 代理地址（可选，支持 http/https/socks4/socks5） |

## 使用

### 1. 启动 Turnstile Solver

双击运行 `TurnstileSolver.bat` 或执行：

```bash
# 使用 uv（推荐）
uv run python api_solver.py --browser_type camoufox --thread 5 --debug

# 或使用传统方式
python api_solver.py --browser_type camoufox --thread 5 --debug
```

等待 Solver 启动完成（监听 `http://127.0.0.1:5072`）

### 2. 运行注册程序

新开一个终端，运行：

```bash
# 使用 uv（推荐）
uv run grok.py

# 或使用传统方式
python grok.py
```

按提示输入：
- 并发数（默认 8）
- 注册数量（默认 100）

注册成功的 SSO Token 保存在 `keys/grok_时间戳_数量.txt`

## 输出示例

```
============================================================
Grok 注册机
============================================================
[*] 正在初始化...
[+] Action ID: 7f67aa61adfb0655899002808e1d443935b057c25b
[*] 启动 8 个线程，目标 10 个
[*] 输出: keys/grok_20260204_190000_10.txt
[*] 开始注册: abc123@example.com
[+] 1/10 abc123@example.com | 5.2s/个
[+] 2/10 def456@example.com | 4.8s/个
...
[*] 开始二次验证 NSFW...
[*] 二次验证完成: 10/10
```

## 代理配置（可选）

如需使用代理，在 `.env` 中配置 `PROXY_URL`：

```bash
# HTTP 代理
PROXY_URL=http://127.0.0.1:7890

# SOCKS5 代理
PROXY_URL=socks5://127.0.0.1:10808

# 带认证的代理
PROXY_URL=http://user:pass@127.0.0.1:7890
```

启动时会显示代理状态：
- `[*] 使用代理: socks5://127.0.0.1:10808`
- `[*] 未配置代理，使用直连`

## 注意事项

- 需要自行部署 freemail 临时邮箱服务
- 运行前必须先启动 Turnstile Solver
- 仅供学习研究使用
