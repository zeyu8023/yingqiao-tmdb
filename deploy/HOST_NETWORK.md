# NAS 本机能访问 Clash，但容器超时

用户的对比结果：NAS 上 curl 通过 Clash 得到 TMDB HTTP 401，容器内同一地址的 TCP 连接超时。这说明当前容器的网络路径与 NAS 本机不同；还不能仅凭结果判断是 Docker 转发规则、OpenWrt 防火墙还是虚拟网络隔离。

提供独立 Host 网络配置，以复用 Linux NAS 本机网络。它不修改 NAS 网关、全局 Docker 网络或 OpenWrt 规则。应用仍以非 root 用户运行，现有配置与程序卷名称保持不变。

## 切换

1. 将完整包覆盖到原项目目录，保留 `.env`、原项目名与数据卷；如果已有 v1.0.5，只需要把 `compose.host.yaml` 放在原 `compose.yaml` 旁边。
2. 用户当前目录为 `/volume1/docker/tmdb-accelerator`，在 SSH 中执行：

```sh
cd /volume1/docker/tmdb-accelerator
docker compose -f compose.yaml down
docker compose -f compose.host.yaml up -d
```

不要加 `-v`。显式使用 `-f`，避免同目录的 `docker-compose.yaml` 被误选。Host 配置是独立文件，不要把它与 bridge 配置合并。

3. 验证：

```sh
docker compose -f compose.host.yaml exec tmdb-relay python -c "import socket; s=socket.create_connection(('192.168.1.2',7890),5); print('代理端口连接成功'); s.close()"
```

4. 打开 `http://NAS的IP:18012` 再点击无密钥网络验证。如果原 `.env` 的 PORT 不是 18012，以实际值为准。

Host 网络没有 Docker 端口映射，本配置已将应用监听端口与健康检查一并调整为 `.env` 的 PORT（默认 18012）。旧服务需先停止以释放同一端口。若健康检查成功但其他设备打不开页面，检查 UGOS 主机防火墙是否允许该端口。

以后对此项目启动、查看日志、停止，都使用 `docker compose -f compose.host.yaml ...`。UGOS 图形界面也必须更新为这份完整 Host 配置；若仍保存 bridge 配置，界面操作可能把服务改回 bridge。

## 回退

```sh
docker compose -f compose.host.yaml down
docker compose -f compose.yaml up -d
```

两种模式使用同一项目目录和数据卷。不要同时启动两个模式；不要创建不同名称的新项目，否则会使用不同的配置卷。

参考：https://docs.docker.com/engine/network/drivers/host/

当前开发环境无 Docker 引擎，此配置已进行 YAML、监听端口和卷一致性检查，但未代替用户在 NAS 上执行。
