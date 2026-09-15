# 3x-ui + Caddy + REALITY，共用公网 443

在 Debian 12+ / Ubuntu 22.04+（amd64 / arm64，systemd）安装 3x-ui v3.8.0、Caddy，并自动创建一个 **VLESS + TCP + REALITY + Vision** 入站和客户端。

## 连接方式

```text
节点客户端 / 浏览器 → 公网 TCP 443 → Xray / REALITY
                                      ├─ 已认证节点 → 代理流量
                                      └─ 普通 TLS → Caddy 127.0.0.1:8443
                                                       ├─ 面板 127.0.0.1:2053
                                                       └─ 订阅 127.0.0.1:2096
HTTP / 证书验证 → 公网 TCP 80 → Caddy（跳转到 HTTPS 443，自动续期）
```

面板、订阅和节点的**外部访问端口都是 443**。3x-ui 面板本身继续监听内部 2053，Caddy 监听内部 8443；不要在面板设置里把 Web 端口也改为 443。公网 443 已由 Xray 独占。

## 已安装旧版脚本：迁移

你已用本仓库旧脚本安装并能打开面板时，执行这一条，替换域名：

```bash
curl -fL --retry 3 -o install-3x-ui-caddy.sh https://raw.githubusercontent.com/kzzou/3x-ui-caddy-installer/main/install-3x-ui-caddy.sh && sudo bash install-3x-ui-caddy.sh --migrate www.kzzou.cloud
```

迁移会短暂停止服务，备份数据库和 Caddyfile，然后移动 Caddy 到本机 8443、创建 `REALITY-443-Caddy` 入站并重启服务。**原面板账号、访问路径、其他入站及客户端保持不变。** 备份路径会在终端显示，例如 `/root/3x-ui-caddy-backup.XXXXXXXX`。

- 仅接管本脚本的 v3.8.0 SQLite 安装；域名必须与原安装相同。
- Caddy 配置必须与本仓库旧版或新版生成配置一致；存在其他网站、自定义运行配置时停止，避免覆盖。
- 如果你已手动创建了其他 **443 入站**，迁移会拒绝执行。先在面板给那个入站改到其他空闲端口；脚本不会删除它。
- 数据库修改、启动或 HTTPS 检查失败时，尝试恢复原数据库、Caddyfile 和服务运行状态；恢复失败则保留备份并报告问题。
- 重复迁移复用已创建的 `REALITY-443-Caddy` 身份，保留客户端和统计，不重复创建。该入站若被改为不兼容协议或无有效 Vision 客户端，会拒绝迁移。
- 迁移不做 Ubuntu 软件包升级，也不升级已安装的 3x-ui/Caddy。

## 全新服务器：安装

```bash
curl -fL --retry 3 -o install-3x-ui-caddy.sh https://raw.githubusercontent.com/kzzou/3x-ui-caddy-installer/main/install-3x-ui-caddy.sh && sudo bash install-3x-ui-caddy.sh www.kzzou.cloud
```

如果缺少 `curl`，先执行 `sudo apt-get update && sudo apt-get install -y curl`。

安装前准备：

1. 域名 A 记录指向 VPS 公网 IPv4。使用 IPv6 时，确认服务和网络均可达后再配置 AAAA。
2. **域名直接解析到 VPS**，不要让 REALITY 客户端连接普通 CDN/HTTP 代理入口。
3. 云安全组和系统防火墙放行 TCP **80、443**，保留 SSH 端口。80 用于证书申请和续期；不需要放行 2053、2096、8443 或 UDP 443。
4. 服务器能访问 GitHub、官方软件源和 ACME 证书机构。

参数支持纯域名、`https://域名/`、`:443`、首尾空白和粘贴的 Markdown 链接。含空白或 Markdown 时用单引号包住整个参数。路径、查询参数、其他端口被拒绝。

通过系统与冲突预检后，脚本先刷新软件源索引；Ubuntu 额外运行 `apt-get upgrade -y`，保留现有配置文件。更新失败即停止。不执行发行版升级、不自动重启，需要重启时在末尾提示。

全新安装拒绝覆盖已有服务。服务运行但证书尚未就绪时返回 **2**，保留运行以便自动重试；其他安装错误停止本次配置的服务、保留诊断文件。安装失败后的残留状态须根据日志处理，不要把 `--migrate` 当成任意失败恢复命令。

## 获取面板和节点

```bash
# 面板地址、用户名和密码
sudo cat /root/3x-ui-caddy-access.txt

# 节点分享链接 vless_uri，以及 subscription_url
sudo cat /root/3x-ui-reality-node.json
```

这两个文件权限均为 `600`。私钥保留在服务器端，由 3x-ui 管理；节点 JSON 只含客户端连接所需的公钥、UUID、shortId 等信息，不要公开分享整个文件。

面板中会出现 `REALITY-443-Caddy`：

| 项目 | 配置 |
|---|---|
| 协议 / 传输 | VLESS / TCP |
| 安全 / Flow | REALITY / xtls-rprx-vision |
| 入站端口 | 443 |
| REALITY Target | 127.0.0.1:8443 |
| Server Names / SNI | 你的域名 |
| 客户端地址与端口 | 你的域名、443 |

可以直接导入生成的 `vless_uri`，也可以从面板复制客户端订阅。普通、JSON 和 Clash 订阅均走同一域名的 443。默认客户端没有流量或到期限制，可在面板按需要调整。

## 维护与检查

```bash
sudo systemctl status x-ui caddy --no-pager
sudo journalctl -u x-ui -u caddy -n 80 --no-pager
sudo ss -lntp '( sport = :443 or sport = :8443 or sport = :2053 or sport = :2096 )'

# 编辑 Caddyfile 后验证并重载
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

预期：443 属于 Xray；8443、2053、2096 都只绑定 `127.0.0.1`。从另一台设备验证面板、订阅和节点可用，并确认三个内部端口无法直连。脚本的本机检查不能代替外部网络验证。

- 不要删除或关闭 `REALITY-443-Caddy`，也不要把 Target 改成其他网站，否则域名 443 将无法访问面板和订阅。
- Xray 停止或重启时，公网面板入口也会暂时中断；Caddy 独立通过 80 续期证书。
- 当前 `xver=0`，回落到 Caddy 的连接显示为本机地址。面板/订阅的来源 IP 日志和基于来源 IP 的限制无法准确区分外部客户端；REALITY 节点本身仍直接接收客户端连接。
- Caddy 只启用 HTTP/1.1 和 HTTP/2，避免向客户端发布内部 8443 的 HTTP/3 入口。
- 未匹配路径返回 404；Caddy 添加 HSTS 和面板会话 Cookie 的 Secure 标志。
- 原有其他入站仍可能开放其他公网端口，脚本不会删除这些业务。确定不再使用后可在面板关闭。
- 升级前备份 `/etc/x-ui/`、`/etc/caddy/` 和两个凭据文件；升级后重新核对入站、监听和节点链接。

## 预览与测试

```bash
bash install-3x-ui-caddy.sh --render https://www.kzzou.cloud
bash tests/domain-input.sh
python3 tests/reality-db.py
python3 tests/migration-rollback.py
bash tests/smoke.sh
```

域名、数据库和迁移模拟测试不安装软件或启动系统服务。`smoke.sh` 在 Linux 临时目录使用真实 3x-ui、Xray、Caddy 进程验证浏览器回落、订阅和 REALITY 代理；测试证书使用本地 CA，不向系统安装信任。实际 VPS 的 apt/systemd 安装、ACME 公网签发及外部连通性仍需在目标服务器验证。

2026-09-15 已通过：17 项域名输入用例、5 项数据库行为测试、5 种迁移成功/回滚场景，以及真实 3x-ui v3.8.0 / Xray 26.9.9 / Caddy v2.11.4 的 HTTPS 回落与 REALITY 代理双路径测试。代理测试仅在测试配置中移除了阻止私网目的地址的规则，以连接同机测试目标；生产配置保留原有路由规则。

## 官方依据

- [3x-ui v3.8.0](https://github.com/MHSanaei/3x-ui/releases/tag/v3.8.0)
- [3x-ui 数据模型](https://github.com/MHSanaei/3x-ui/blob/v3.8.0/internal/database/model/model.go)
- [REALITY Target 与转发机制](https://xtls.github.io/config/transports/reality.html)
- [Caddy TLS / ACME](https://caddyserver.com/docs/caddyfile/directives/tls)
- [Caddy 全局选项](https://caddyserver.com/docs/caddyfile/options)
