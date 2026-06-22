# astrbot_plugin_steam_sale

AstrBot 插件 — 监控 Steam 游戏折扣，自动推送通知到群组。

## 功能

- 每群独立管理游戏列表，通过指令自由添加/移除
- 定时轮询 Steam Store API，检测折扣
- 折扣时自动通知添加了该游戏的群，同一折扣不重复推送
- 可配置代理（如 Cloudflare Worker）加速国内访问
- 可选集成 IsThereAnyDeal API，识别历史最低价
- 支持搜索 Steam 游戏并一键添加到列表

## 配置

在 WebUI 插件管理面板中配置：

| 字段 | 类型 | 说明 |
|---|---|---|
| `proxy_url` | string | (选填) Steam API 代理地址，如 Cloudflare Worker URL |
| `itad_api_key` | string | (选填) IsThereAnyDeal API Key，用于判断历史最低价 |
| `request_timeout` | int | HTTP 请求超时时间（秒），默认 120 |
| `check_interval` | int | 轮询间隔（分钟），默认 60 |
| `region` | string | Steam 区域代码，默认 `cn` |

> ITAD API Key 可在 [isthereanydeal.com/apps/my/](https://isthereanydeal.com/apps/my/) 注册获取。

## 指令

| 指令 | 别名 | 权限 | 功能 |
|---|---|---|---|
| `/steam_add <AppID>` | `/添加游戏` | ADMIN | 向本群游戏列表添加游戏 |
| `/steam_remove <AppID>` | `/移除游戏` | ADMIN | 从本群游戏列表移除游戏 |
| `/steam_list` | `/游戏列表` | 所有人 | 查看本群关注的游戏 |
| `/steam_search <关键词>` | `/搜索游戏` | 所有人 | 搜索 Steam 游戏并显示 App ID |
| `/steam_sale` | `/折扣` | 所有人 | 查询本群游戏的折扣状态 |

> `ADMIN` 权限需要发送者在群内是群主或管理员。

## 使用流程

1. `/搜索游戏 荒野大镖客` — 搜索游戏，获得 App ID
2. `/添加游戏 1174180` — 添加到本群列表
3. 插件轮询到折扣后，自动通知本群
4. `/折扣` — 随时查看当前折扣状态

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
| `PLUGIN_DIR` | (Secret) 插件目录完整路径，如 `/home/user/astrbot/data/plugins/astrbot_plugin_steam_sale` |

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
