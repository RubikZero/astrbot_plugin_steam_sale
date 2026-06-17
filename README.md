# astrbot_plugin_steam_sale

AstrBot 插件 — 监控 Steam 游戏折扣，自动推送通知到群组。

## 功能

- 配置关注的 Steam 游戏 App ID 列表
- 定时轮询 Steam Store API，检测折扣
- 有折扣时推送到已订阅的群/频道，同一折扣不重复推送
- 可选集成 IsThereAnyDeal API，识别历史最低价
- 指令查询当前折扣状态

## 配置

在 WebUI 插件管理面板中配置：

| 字段 | 类型 | 说明 |
|---|---|---|
| `steam_game_ids` | string | 关注的游戏 App ID，逗号分隔。如 `730,570,440` |
| `itad_api_key` | string | (选填) IsThereAnyDeal API Key，用于判断历史最低价 |
| `check_interval` | int | 轮询间隔（分钟），默认 60 |
| `region` | string | Steam 区域代码，默认 `cn` |

> ITAD API Key 可在 [isthereanydeal.com/apps/my/](https://isthereanydeal.com/apps/my/) 注册获取。

## 指令

| 指令 | 别名 | 功能 |
|---|---|---|
| `/steam_sub` | `/订阅折扣` | 在当前群/频道订阅折扣通知 |
| `/steam_unsub` | `/取消订阅` | 取消订阅 |
| `/steam_sale` | `/折扣` | 查询关注游戏的当前折扣状态 |

## 安装

在 astrbot WebUI → 插件管理 → 从 Git 仓库安装，填入本仓库地址。

## 自动部署

仓库包含 GitHub Actions 工作流，推送到 `main` 或 `master` 分支时自动 SSH 到服务器执行 `git pull`。

### 服务器要求

1. 插件目录已通过 `git clone` 初始化为 git 仓库（astrbot 从 Git 安装时自动完成）
2. SSH 密钥对已配置

### GitHub Secrets 设置

在仓库 Settings → Secrets and variables → Actions 中配置：

| Secret | 说明 |
|---|---|
| `SERVER_HOST` | 服务器 IP 或域名 |
| `SERVER_USER` | SSH 用户名 |
| `SERVER_SSH_KEY` | SSH 私钥内容 |
| `PLUGIN_DIR` | (Variable) 插件目录完整路径，如 `/opt/astrbot/data/plugins/astrbot_plugin_steam_sale` |

## 开发

```bash
git clone https://github.com/your_name/astrbot_plugin_steam_sale
```

项目结构：

```
astrbot_plugin_steam_sale/
├── main.py              # 插件主逻辑
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # WebUI 配置面板定义
├── requirements.txt     # 依赖
├── .github/workflows/   # CI/CD
└── README.md
```

## 依赖

- `httpx` — 异步 HTTP 请求
