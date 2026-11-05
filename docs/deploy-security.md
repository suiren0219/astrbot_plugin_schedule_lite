# 部署安全指南（端口放行与 IP 保护）

课表 Lite 家族的插件会把一些服务暴露在服务器端口上，这份文档解释**要放行哪些端口**、
**为什么裸 IP 链接有风险**、以及**推荐的域名反代部署方式**。适用于：

- 主插件课表 Lite（AstrBot WebUI 默认端口 `6185`，控制台页面挂在它下面）
- 附属插件[课表 Lite 网页伴侣](https://github.com/suiren0219/astrbot_plugin_schedule_lite_web)
  （独立 HTTP 服务，默认端口 `8766`）

> 课表 Lite 不会登录校园网或教务系统，也没有任何写接口；
> 本文档只讨论「端口暴露」这一件事。

---

## 1. 端口放行（不做外部就访问不了）

插件监听端口后，还需要在**两层**都放行，缺一不可：

### 1.1 云服务商安全组（入方向规则）

| 云厂商 | 入口 |
| --- | --- |
| 阿里云 | ECS 控制台 → 实例 → 安全组 → 配置规则 → 入方向 → 添加：协议 TCP，端口 `8766/8766`，授权对象 `0.0.0.0/0` |
| 腾讯云 | 轻量/CVM 控制台 → 防火墙 → 添加规则：应用类型自定义，协议 TCP，端口 `8766` |
| 华为云 | 安全组 → 入方向规则 → 端口 `8766` |

### 1.2 服务器系统防火墙

```bash
# Ubuntu (ufw)
sudo ufw allow 8766/tcp

# CentOS / RHEL (firewalld)
sudo firewall-cmd --permanent --add-port=8766/tcp && sudo firewall-cmd --reload

# Debian (nftables/iptables 通常默认放行，确认没有 DROP 规则即可)
```

改了 `web_port` 配置后，**安全组和防火墙要同步放行新端口**。

## 2. ⚠️ 裸 IP 链接的风险

网页伴侣未配置 `external_base_url` 时，`/课表网页` 发出去的是：

```
http://123.45.67.89:8766/help
```

这条消息意味着：

- **群里所有人**（包括任何截图转发出去的人）都能看到你的服务器 IP
- IP 可以被批量扫描：攻击者拿到 IP 后会探测常见端口（SSH 22、面板 6185 等），
  尝试弱口令爆破、漏洞利用
- 你的服务器还可能被盯上其他业务（AstrBot WebUI、数据库等）

**缓解建议（按推荐程度排序）：**

1. **域名 + HTTPS 反代（推荐）**：隐藏源站 IP，见第 3 节
2. **CDN 套壳**：Cloudflare 等代理域名，真实 IP 对外不可见
3. 只在熟悉的小群使用裸 IP 链接，并尽快迁移到域名
4. 无论哪种方案：AstrBot WebUI（6185）**不要**对公网放行，管理面板只应通过
   SSH 隧道或反代加认证访问

## 3. 推荐部署：域名反代（最稳组合）

### 3.1 网页伴侣侧

`web_host` 改成 `127.0.0.1`——服务只监听本机回环，**公网完全摸不到这个端口**，
安全组连 8766 都不用放行，只放行 443：

```json
{
  "web_host": "127.0.0.1",
  "web_port": 8766,
  "external_base_url": "https://kb.example.com/schedule"
}
```

### 3.2 Nginx 反代示例

```nginx
server {
    listen 443 ssl;
    server_name kb.example.com;
    # ssl_certificate / ssl_certificate_key 略（Let's Certbot 一条命令搞定）

    location /schedule/ {
        proxy_pass http://127.0.0.1:8766/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
# 证书（Certbot）
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d kb.example.com
```

### 3.3 Caddy 反代示例（自动 HTTPS，更省事）

```caddy
kb.example.com {
    handle_path /schedule/* {
        reverse_proxy 127.0.0.1:8766
    }
}
```

### 3.4 检查清单

- [ ] `external_base_url` 已设为 `https://域名/路径`（群里发的链接变成域名）
- [ ] 浏览器打开 `https://域名/路径/help` 能看到手册
- [ ] `web_host=127.0.0.1` 时，从外网直连 `IP:8766` 应该**连不上**（预期行为）
- [ ] 安全组只放行了 443（和 SSH），6185/8766 未对公网开放
- [ ] `/课表网页` 发出的链接是域名，且没有 ⚠️ IP 提醒

## 4. 端口一览

| 端口 | 用途 | 是否对公网放行 |
| --- | --- | --- |
| 6185 | AstrBot WebUI（含课表控制台） | **不建议**；走 SSH 隧道或反代加认证 |
| 8766 | 网页伴侣（帮助手册网页） | 直连模式放行；反代模式不放行（127.0.0.1） |
| 443 | 反代 HTTPS 入口 | 放行 |

## 5. 常见问题

**Q：放了行还是打不开？**
先在服务器本机 `curl http://127.0.0.1:8766/` 确认服务活着；再从外网
`telnet 服务器IP 8766` 排查是安全组还是防火墙的问题。

**Q：不想买域名？**
可以用免费的二级域名服务，或接受裸 IP 的风险继续用——本站只有帮助文档，
没有课表数据和写接口，风险集中在「服务器其他端口被顺藤摸瓜」。

**Q：群里发的链接还是带 IP？**
检查网页伴侣的 `external_base_url` 是否留空；填好后重启插件即可。
