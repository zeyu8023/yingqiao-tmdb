# v1.0.4：Permission denied /app/app.py

这是主服务 UID 10001 与 NAS 上传文件的权限／ACL 不匹配，并非 Python 镜像拉取问题。
新版通过一次性的 init-data 服务，以 root 读取只读的 NAS 源文件，复制到 Docker 管理的 relay-program 卷。主服务随后以非 root 用户从程序卷运行，不改 NAS 共享目录权限。

1. 下载新版部署包，完整覆盖原目录，包括新增 bootstrap.py，保留原 .env 和项目名称。
2. 在 UGOS 项目界面更新 Compose；若使用 SSH，在原目录执行：

```sh
docker compose down
docker compose up -d
```

不要添加 -v，否则会删除原来的配置数据卷。init-data 应先打印“映桥初始化完成”，然后成功退出；tmdb-relay 随后启动，访问端口保持 18012。

如果仍失败，请提供以下两项日志：

```sh
docker compose logs --tail=50 init-data tmdb-relay
```

当前环境无 Docker CLI，已验证程序文件复制与配置保留逻辑，但未在 UGOS 上实际运行这份 Compose。

---

# v1.0.3：普通镜像能拉取、但 BuildKit 构建失败

上一轮仅配置 Docker 镜像代理不足以保证 BuildKit 的元数据／鉴权请求也使用它。日志仍然直接连接 Docker Hub 公网 IPv6，说明失败点依然在构建链路；不能仅凭日志确认具体配置。

新包的默认 compose.yaml 已取消 build，直接使用官方 Python 镜像挂载程序运行，绕过 BuildKit。覆盖原项目目录并保留 .env 和原项目名后执行：

```sh
docker pull python:3.12-alpine
docker compose up -d --force-recreate
```

UGOS 图形界面需重新导入／更新新版 compose.yaml，不能继续执行界面里保存的旧 build 配置。如果还出现 Building、Dockerfile 或 load metadata，实际用的仍是旧构建配置。

init-data 容器只负责初始化数据卷属主，成功退出正常；常驻服务是 tmdb-relay。端口仍为 18012。

如果连 docker pull python:3.12-alpine 也失败，才继续排查下方镜像拉取代理。其他项目可能使用其他仓库或缓存，需要单独确认这个官方镜像。

---

# UGOS：基础镜像拉取失败

如果日志显示：

```text
failed to fetch anonymous token
https://auth.docker.io/token
dial tcp ...:443: i/o timeout
```

说明 Docker 在获取 `python:3.12-alpine` 的基础镜像元数据时无法连接 Docker Hub。程序尚未开始运行，映桥管理页中的 Clash 地址不能影响这个阶段；修改容器环境里的 `HTTP_PROXY` 或 Dockerfile 构建参数 `HTTP_PROXY` 也不能单独解决 Docker 守护进程拉取 `FROM` 镜像的问题。

## 使用你已有的 OpenWrt / Clash

1. 在 UGOS 网页端或 PC 客户端打开「Docker」。
2. 进入「镜像」，点右上角设置。找到「代理 / 映像代理」页签，具体名称随 Docker 应用版本略有区别。
3. 启用代理，填写 `http://192.168.1.2:7890`。如果界面把 HTTP、HTTPS 分为两项，两项都填这个 **http://** 地址。
4. 确认应用。UGOS 会重启 Docker 引擎，运行中的容器可能短暂受影响，等待完成后再操作。
5. 在 Docker 的镜像界面先尝试拉取 `python:3.12-alpine`，或在项目目录执行：

```sh
docker pull python:3.12-alpine
docker compose up -d --build
```

填的是「代理」而非「镜像加速器 / Registry Mirror」：Clash 的 7890 是 HTTP 代理端口，不是 Docker 镜像仓库。确认 Clash 允许来自 NAS 的局域网连接。

官方入口说明：
- https://support.ugnas.com/detail/article/en-US/290
- https://support.ugnas.com/knowledgecenter/detail/article/en-US/297
- Docker 引擎代理机制：https://docs.docker.com/engine/daemon/proxy/

## 为什么之前还有 yingqiao-tmdb 的 pull access denied

旧版 Compose 同时配置了 `build` 和 `image: yingqiao-tmdb:local`。这个名字是本地构建标签，没有发布到 Docker Hub，Compose 可能先尝试拉取该名称，再退回本地构建。它不代表需要 Docker Hub 登录。

新部署包只声明本地构建，移除了这个 `image` 字段，避免对不存在的远程项目镜像发起拉取。仍然需要拉取 Python 官方基础镜像，所以 Docker 的网络代理仍需配置。

覆盖更新时保留已有 `.env`，保持原来的项目文件夹名 / Compose 项目名，以继续使用原有数据卷。默认外部端口仍为 **18012**。

## 其他可选路径

- 已有可信的镜像仓库：在 `.env` 设置 `PYTHON_IMAGE=你的可信仓库/library/python:3.12-alpine`，或仓库实际提供的同款 Alpine 镜像地址；不要直接填 HTTP 代理 URL。该镜像必须是 Alpine 版本，因为 Dockerfile 使用 Alpine 的用户管理命令。
- 离线导入基础镜像：在另一台能联网、与 NAS 使用相同 CPU 架构的 Docker 主机上拉取基础镜像，用 `docker save` 导出，再在 NAS 使用 `docker load` 或 UGOS 的导入本地镜像功能导入。某些 BuildKit 配置仍可能查询远端元数据；若仍超时，优先修复 Docker 引擎代理。

当前开发环境没有 Docker CLI，没有替你执行 UGOS 的引擎设置或实际镜像构建。
