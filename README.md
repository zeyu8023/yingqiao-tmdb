# 映桥 · TMDB Relay

独立运行在 NAS 上的 TMDB 加速站，提供可视化管理页面、Clash HTTP 出站代理、图片缓存与流影接入地址。Python 标准库实现，无第三方运行时依赖，不修改流影或已有账号服务。

## 单容器升级
覆盖原项目文件并保留 .env 和项目名。Host 网络用户运行：

```sh
docker compose -f compose.host.yaml up -d --force-recreate --remove-orphans
```

普通网络用户将文件名换成 compose.yaml。新版在主容器内复制程序，再切换到 UID/GID 10001 启动。旧版的 init-data 由 --remove-orphans 清理。现有数据卷继续复用。

## 你当前这套网络

```text
流影 → 映桥的公网 HTTPS 域名 → NAS 映桥容器 → OpenWrt / Clash → TMDB
```

在映桥中填入 Clash 的 `http://192.168.1.2:7890`。手机只连接映桥，不需要改默认网关或配置家里的 HTTP 代理。

## NAS 部署

需要 Docker 与 Docker Compose（例如飞牛 Docker 项目、群晖 Container Manager 项目）。

1. 将整个项目文件夹上传到 NAS，如 `/volume1/docker/tmdb-accelerator`。
2. 复制 `.env.example` 为 `.env`，填写 `ADMIN_PASSWORD`，至少 12 位。端口默认 18012。
3. 在该文件夹执行：

```sh
docker compose up -d
docker compose ps
```

4. 浏览器打开 `http://NAS的IP:18012`，使用上一步设置的密码登录。
5. 进入「连接设置」，填写：
   - HTTP 网络代理：`http://192.168.1.2:7890`。留空则直连。
   - 对外访问根地址：局域网可以留空；公网填映桥专用域名，如 `https://tmdb.example.com`。
   - 缓存：默认 256 MB，可设置 32–4096 MB。
6. 点击「验证网络（无需密钥）」。分别检测 NAS 到代理端口、TMDB API、TMDB 图片服务；三个检测独立进行，API 返回 401 会标为网络正常，不需要填写 TMDB Key。失败时可复制诊断结果。实际业务获取资料仍使用流影里自己的 TMDB 凭据。
7. 保存配置，到「接入流影」复制两条地址。

UGOS 用户若在构建阶段出现 `auth.docker.io` 超时，请先看 [UGOS 部署排障](deploy/UGOS.md)。这是 Docker 拉基础镜像的网络，与映桥页面里的运行时代理配置是两个不同环节。

Compose 使用命名卷 `relay-data` 持久化设置、加速访问密钥和图片。容器重建不会丢失配置。不要执行 `docker compose down -v`，该命令会删除数据卷。

镜像使用非 root 用户，根文件系统只读，写入仅发生在 `/data` 与临时目录。x86_64 与 ARM64 NAS 可分别从同一个 Dockerfile 构建；本项目未在实体 NAS 或 ARM64 上实机验收。

## 公网 HTTPS

使用 NAS 自带反代或现有反代面板：

| 项目 | 填写 |
|---|---|
| 外部域名 | 新建映桥专用域名，不要复用 OpenWrt 管理页面的路径 |
| 对外协议 | HTTPS，启用有效证书 |
| 目标协议 | HTTP |
| 目标地址 | NAS 局域网 IP |
| 目标端口 | 18012，或你修改后的映射端口 |
| Host | 保留外部 Host，含非标准端口 |
| X-Forwarded-Proto | `https` |
| 读取超时 | 至少 60 秒 |

本项目不提供证书签发、域名 DNS 或公网穿透功能；这些沿用你的 NAS / 现有反代服务。应用本身监听 HTTP，公网 TLS 在外层反代终止。Nginx 片段见 `deploy/nginx.conf.example`。

如果外层开启了额外登录认证，请只对管理页面应用认证，不要让 `/r/` 的加速请求跳转到网页登录页。管理页面自身已有密码登录；加速路径使用独立随机密钥。

## 流影怎么填

进入「设置 → 服务连接 → TMDB」：

| 流影配置项 | 填写 |
|---|---|
| TMDB API 反代根地址 | 管理页生成的 `https://域名/r/访问密钥/3` |
| TMDB 图片反代根地址 | 管理页生成的 `https://域名/r/访问密钥/t/p` |
| TMDB 网络代理 | 留空；Clash 连接已由 NAS 映桥处理 |
| TMDB Key / Token | 保留你自己的凭据 |

两条地址已经兼容现有流影，无需修改 APK。管理页显示接入根地址，但不自动更改客户端设置。不要把 `/r/` 链接分享给其他人；在管理页面重置密钥后旧地址立即失效。

## 管理与缓存

- 概览展示本次启动以来的请求数、图片命中、传输量、缓存用量和最近 40 条连接。
- 连接日志只包含类型、状态与耗时，不记录完整 URL、TMDB Key、访问密钥或响应正文；重启后统计清零。
- 仅缓存成功的图片响应，7 天过期，以最近访问时间淘汰，最多 2000 张；不缓存资料接口或用户资料。
- 代理只支持 HTTP / mixed 端口，HTTPS 目标通过 CONNECT 隧道访问，仍验证 TMDB TLS 证书。暂不支持带账号密码的代理、SOCKS 或 HTTPS 代理监听。
- API 仅支持 GET；不代理 TMDB 的写操作，不提供任意 URL 代理。
- 管理设置与加速密钥保存在 `/data/config.json`，文件权限 600；请保护 NAS 数据卷。管理员密码由 Compose 环境注入，也支持使用 `ADMIN_PASSWORD_FILE` 挂载文件。

## 排障

```sh
docker compose logs --tail=50
docker compose exec tmdb-relay python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health').read().decode())"
```

- 管理页打不开：检查端口映射、NAS 防火墙、容器是否健康。
- Clash 连接失败：确认 Clash 允许局域网连接，监听的不是仅 `127.0.0.1`，OpenWrt 放行 NAS 到 7890。
- API 401：无密钥测试属于预期；携带密钥仍 401 则检查 TMDB 凭据。
- 公网登录或复制地址异常：检查反代保留 Host，并在设置里填写真实对外地址。
- 海报缓存不增长：确认测试/客户端请求成功，响应是图片；管理页诊断故意绕过缓存。

## 本地运行与验证

Python 3.12+：

```sh
python -m unittest discover -s tests -v
# 设置环境变量 ADMIN_PASSWORD（至少12位）、DATA_DIR（本机数据目录）、PORT（可选）后：
python app.py
```

本项目使用 TMDB API，但未经 TMDB 认可或认证。仍需遵循 TMDB 的使用条款与各自凭据限额。

参考：[TMDB 图片地址规则](https://developer.themoviedb.org/docs/image-basics)、[Python HTTPS 与隧道连接](https://docs.python.org/3/library/http.client.html)、[Docker Compose 环境配置](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)。


## v1.0.3：默认免构建运行
默认 compose.yaml 直接拉取官方 Python 镜像，不经过 Dockerfile / BuildKit。初始化服务读取项目源文件并复制到 Docker 程序卷，主服务从该卷运行。必须保留完整项目目录，包括 bootstrap.py、app.py 和 web/。
一次性的 init-data 服务准备程序卷与数据卷属主，成功退出属于正常现象；主服务继续使用 UID/GID 10001 非 root 用户。NAS 源文件只读挂载给初始化服务，不修改共享文件的权限或属主。
升级时覆盖程序文件，保留 .env 与原项目名，执行 `docker compose up -d --force-recreate` 重新加载 Python 代码，不要删数据卷。
v1.0.4 已改为程序卷部署，以兼容 UGOS 共享文件 ACL。升级该版本时先执行 docker compose down（不要加 -v），然后 docker compose up -d。若仍失败，请提供 init-data 服务日志。
只有需要自行构建独立镜像时才使用 `docker compose -f compose.build.yaml up -d --build`。
当前环境没有 Docker CLI，免构建 Compose 尚需 NAS 实机验证。


## v1.0.4：UGOS 文件权限兼容
初始化服务以 root 读取只读的 NAS 源文件，并以固定权限复制到独立的 relay-program 卷；它仅获得 CHOWN、DAC_OVERRIDE、FOWNER 三项能力，成功后退出。主服务没有这些能力，仍以非 root 用户运行，从只读程序卷读取代码。relay-data 保持原卷名。
请在原目录完整覆盖新版文件，保留 .env 与项目名称；不要只替换 Compose。UGOS 项目界面必须更新成新版 Compose。


## v1.0.5：无需密钥的网络验证
- 连接设置里填写 HTTP 网络代理，即可点击“验证网络（无需密钥）”，无需先保存、不依赖公网地址或缓存容量输入。
- 分别验证代理端口、TMDB API 与实际海报下载；API 401 代表已收到未鉴权响应，是网络成功，不代表业务鉴权成功。
- 分类显示 DNS、超时、连接被拒绝、代理 CONNECT 拒绝／要求认证、TLS 证书及协议错误。结果可复制，不包含管理员密码、TMDB Key 或加速访问密钥。
- 浏览器端每项独立请求、独立显示结果。空响应或非 JSON 响应会显示 HTTP 状态和反代排查提示，不再抛原始 JSON 解析错误。
- 完整覆盖文件并保留 .env、原项目名称和数据卷，然后 `docker compose down`（不要加 -v）再 `docker compose up -d`，确保 init-data 重新复制程序。仅重启 tmdb-relay 不会更新程序卷里的旧文件。
- 登录后左下角版本应显示 1.0.5；手机也可访问 /health 检查返回的 version。


## 容器无法连接 OpenWrt，但 NAS 本机可以
使用独立的 `compose.host.yaml`，切换步骤见 [Host 网络部署](deploy/HOST_NETWORK.md)。不要和默认 Compose 合并；监听端口、健康检查、现有配置卷都已一起处理。
