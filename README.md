# 3x-ui + Caddy 一键安装

用于**全新 Debian 12+ / Ubuntu 22.04+ 服务器**，支持 amd64、arm64，要求 systemd。脚本固定使用已核对接口及 SHA-256 的 3x-ui **v3.8.0**，从 Caddy 官方稳定软件源安装 Caddy。

## 访问方式

```text
浏览器 / 订阅客户端
        │ HTTPS 443
        ▼
      Caddy（自动申请、续期证书）
        ├─ /panel-随机值/* → 127.0.0.1:2053（面板）
        ├─ /sub-随机值/*   → 127.0.0.1:2096（普通订阅）
        ├─ /json-随机值/*  → 127.0.0.1:2096（JSON 订阅）
        └─ /clash-随机值/* → 127.0.0.1:2096（Clash 订阅）
```

“隐藏端口”通过让内部服务只绑定回环地址实现：外部不能直接连接服务器的 2053、2096；访问 URL 使用默认 HTTPS 端口。**公网 443 和服务器 IP 仍然可见。** TCP 80 用于证书验证及 HTTP 跳转；Caddy 也可能监听 UDP 443 提供 HTTP/3，放行该 UDP 端口是可选的。

## 一键运行

在目标服务器执行，替换最后的 `panel.example.com` 为你的域名：

```bash
curl -fL --retry 3 -o install-3x-ui-caddy.sh https://raw.githubusercontent.com/kzzou/3x-ui-caddy-installer/main/install-3x-ui-caddy.sh && sudo bash install-3x-ui-caddy.sh panel.example.com
```

如提示缺少 `curl`，先执行 `sudo apt-get update && sudo apt-get install -y curl`。

部署前准备：

1. 将域名（例如 `panel.example.com`）的 A 记录指向 VPS 公网 IPv4。若有 AAAA 记录，IPv6 也必须正确可达。
2. 云安全组和服务器防火墙允许 TCP **80、443**，保留当前 SSH 端口。安装还需要能访问 GitHub、软件源及证书机构。
3. 将 `install-3x-ui-caddy.sh` 上传到服务器，然后执行：

```bash
sudo bash install-3x-ui-caddy.sh panel.example.com
```

不传域名时会交互询问。完整凭据写入 root 专用文件，终端不直接显示密码：

```bash
sudo cat /root/3x-ui-caddy-access.txt
```

打开文件中的面板地址，用随机生成的账号和密码登录。先创建入站和客户端，再从面板复制订阅链接。脚本已设置公开订阅 URL 为 `https://域名/随机路径/`，不会自动生成代理节点或客户端。

如果 DNS/证书尚未就绪，脚本返回 **2**，保留服务运行以便 Caddy 重试；根据日志修复域名和网络即可。其他安装错误返回非零，停用本次已配置的服务，并保留文件与临时工作目录。它不会自动删除数据库或回滚软件包。

## 重要行为

- 通过系统、已有安装和端口预检后，先刷新软件源索引；Ubuntu 额外执行 `apt-get upgrade -y` 升级已安装的软件包，保留已有配置文件。更新或升级失败即停止，成功后才安装依赖和服务。这是当前 Ubuntu 版本内的软件包升级，不进行发行版升级，也不自动重启；若系统要求重启，安装结束时会提示。
- 面板和订阅在首次启动前即配置为 `127.0.0.1`，内部 HTTP 由 Caddy 终止外部 TLS。
- 未匹配路径返回 404；面板路径原样转发，订阅不套面板登录认证。
- Caddy 补充 HSTS 和面板 Cookie 的 `Secure` 标志；安装后实际验证随机凭据能够登录。
- 首次安装生成随机账号、密码和四条随机路径，凭据文件权限为 `600`。
- 检测已有 3x-ui、Caddy、相关配置或端口冲突后停止。**重复执行不会升级或重置已有安装**；失败重试也应先根据日志处理残留状态。
- 不改动防火墙规则。其他应用已经开放的端口仍由原规则控制。
- Caddy 管理 API 默认位于本机 `localhost:2019`，脚本也检查该端口冲突。
- 修改面板设置时，保持面板和订阅监听地址为 `127.0.0.1`；不要给后端开启独立 HTTPS。变更路径时同步修改 Caddyfile 和公开订阅 URL。
- 代理节点流量不在本脚本范围内：新建入站监听公网时，其端口会公开，需另行设计 WS/gRPC 等转发。

## 预览配置

预览使用示例路径，不安装软件、不生成真实凭据、不写系统配置：

```bash
bash install-3x-ui-caddy.sh --render panel.example.com
```

## 维护与验证

```bash
# 服务和日志
sudo systemctl status x-ui caddy --no-pager
sudo journalctl -u x-ui -u caddy -n 80 --no-pager

# 两个后端应分别只出现 127.0.0.1:2053 和 127.0.0.1:2096
sudo ss -lntp '( sport = :2053 or sport = :2096 )'

# 编辑 /etc/caddy/Caddyfile 后验证并重载
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

从另一台设备打开实际面板 URL，并用 `nc -vz VPS公网IP 2053`、`nc -vz VPS公网IP 2096` 检查内部端口不可连接；服务器有 IPv6 时也检查公网 IPv6。安装脚本执行的 HTTPS 测试连接本机 Caddy 并校验证书，不能代替外部网络测试。

订阅测试必须使用真实客户端的订阅 ID。全新安装没有客户端，任意伪造 ID 返回 404 是正常结果。

升级前备份 `/etc/x-ui/`、`/etc/caddy/` 和凭据文件；数据库备份应停用面板后复制，或使用 SQLite 在线备份。通过上游管理工具升级后，重新核对监听地址和公开订阅 URL。此安装脚本不是升级工具。

## 官方依据

- [3x-ui v3.8.0 发行版](https://github.com/MHSanaei/3x-ui/releases/tag/v3.8.0)
- [3x-ui CLI 源码](https://github.com/MHSanaei/3x-ui/blob/v3.8.0/main.go)
- [3x-ui 面板及订阅设置源码](https://github.com/MHSanaei/3x-ui/blob/v3.8.0/internal/web/service/setting.go)
- [Caddy 官方安装说明](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)
- [Caddy HTTPS 反向代理说明](https://caddyserver.com/docs/quick-starts/reverse-proxy)

## 本地测试

`tests/smoke.sh` 在 Linux 临时目录下载并校验真实发行包，测试初始化、登录、监听地址、Caddy 路由与公开订阅 URL；不安装系统服务。测试仅启动临时 HTTP 监听，不申请证书。

```bash
bash tests/smoke.sh
```

实际 VPS 的 apt/systemd 安装和公网证书签发需在目标服务器验证。

2026-09-15 已在 WSL Ubuntu 22.04 使用真实 3x-ui v3.8.0、Caddy v2.11.4 通过以下检查：Bash 语法、发行包校验、数据库初始化、正确/错误密码登录、CSRF、代理返回 Cookie 的 Secure 标志、面板路径与跳转、三类订阅路由、公开订阅 URL、两个后端的回环监听。订阅路由使用不存在的客户端 ID 比对直连和代理响应，未验证实际节点配置内容。
