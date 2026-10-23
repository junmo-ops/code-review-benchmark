# DSH 的 Kylin 部署核查

核查日期：2026-10-23。范围为银河麒麟高级服务器 V10，不将桌面版、openKylin 或 Ubuntu Kylin 的安装方法套到服务器镜像上。

## 行内实际结果与最终选择

使用者随后从真实 POD 提供了命令输出：`ID=kylin`、`VERSION_ID=V10`、`x86_64`，运行身份为 UID 1001；`ks10-adv-os` 和 `ks10-adv-updates` 均能访问，分别列出 Bubblewrap 0.3.3-1 和 0.3.3-2。实际从已配置更新源下载 39772 字节的 RPM 后，签名 key ID `7a486d9f` 及内容摘要均检查为 OK，解包后 `bwrap --version` 为 0.3.3。

该版本不支持 `--clearenv`。改用兼容参数后，实际 bwrap 报 namespace 创建无权限，退出码 1；单独 `unshare --user --map-root-user` 同样被拒绝。`Seccomp=2`、`NoNewPrivs=1`、`max_user_namespaces=254394`，这些不足以单独确定是哪项策略拦截。不能照通用错误提示修改 sysctl 或提升权限。

使用者明确选择方案 2，新增显式 POD 内运行模式，不再依赖 Bubblewrap/RPM。它保留软件工具与路径限制，不提供 OS 隔离；见 [ADR 0002](adr/0002-explicit-pod-dsh-execution.md)和[操作单](../deployment/DSH部署操作单.md)。以下“尚未确认”保留为公开资料研究时的历史边界，不再把“公司源是否有包”列作当前未知项。

## 已确认

1. **银河麒麟高级服务器 V10 SP3 就是 Linux 发行版。** 麒麟官方产品页明确将它称为国产 Linux 操作系统，并说明与 openEuler 社区的技术关系。这里不是一个需要重新移植 Linux 软件的平台。[官方产品页](https://www.kylinos.cn/productPc/server/serverMain/)
2. **服务器版采用 RPM 软件包体系，可用 dnf/yum 管理安装及依赖。** 官方 V10 系统管理员手册第四章说明 dnf 使用 rpm 等库，可与 yum 同时使用。RPM 是系统软件安装包格式，不是另一项独立服务，也不是 npm 包。[官方管理员手册](https://kylinos.cn/upload/product/20221220/e0574494d7d3e287e18d10052664bdec.pdf)
3. **官方源已经有 x86_64 的 Bubblewrap 安装包，无须先让用户自行编译。** V10 SP3 基础源列出 `bubblewrap-0.3.3-1.ky10.x86_64.rpm`；V10 SP3-2403 更新源列出 `bubblewrap-0.3.3-2.ky10.x86_64.rpm`，目录显示约 39K。这只是公开仓库存在性证据，不代表这两个包可不经确认地混用于任何 V10 镜像，也不代表最新上游版本。[SP3 基础源](https://update.cs2c.com.cn/NS/V10/V10SP3/os/adv/lic/base/x86_64/Packages/)、[SP3-2403 更新源](https://update.cs2c.com.cn/NS/V10/V10SP3-2403/os/adv/lic/updates/x86_64/Packages/)
4. **Bubblewrap 提供 Linux 隔离工具，具体保护规则由调用者配置。** 上游说明它使用 Linux 命名空间，可控制可见目录、只读挂载、进程与网络隔离；它不是装好后自动具备固定规则的完整沙箱。包能安装不等于目标容器允许这些隔离操作。[Bubblewrap 上游说明](https://github.com/containers/bubblewrap#sandbox-security)

## 对本项目意味着什么

DSH 的 npm 依赖和 Bubblewrap 的系统依赖要分开处理。把 DSH 及完整 npm 依赖做成离线制品可以解决 npm 联网问题；不能据此认为 Linux 系统依赖也已经安装。本项目仍须保留 [ADR 0001](adr/0001-controlled-skill-execution-evaluation.md) 和 [Linux 基线](DSH-Linux基线.md) 约定的只读隔离，不应为方便部署而退化成裸跑。

项目的 `backend/Dockerfile` 已经通过 `yum` 安装 `git`、`procps-ng`、`libstdc++`。因此优先核查**同一基础镜像中已经配置的行内 yum 源**是否提供 `bubblewrap`，而不是先要求用户准备一套离线 RPM。若行内源有包，后续可以在镜像构建时从该源安装；若没有，才需要选取与镜像版本、架构一致并经审核的离线 RPM 及缺失依赖。这里是部署建议，不表示在线安装分支已实现或已验收。

## 最小探测

在实际基础镜像或其临时容器内执行以下只读查询，不在宿主机上替代查询。`yum` 查询可能访问其已配置的仓库并刷新元数据，但不会安装软件。

```sh
cat /etc/os-release
uname -m
rpm -q bubblewrap
yum repolist enabled
yum --showduplicates list bubblewrap
```

若镜像只有 `dnf`，后两条使用 `dnf repolist --enabled` 和 `dnf --showduplicates list bubblewrap`。保存包版本、架构、仓库 ID 及查询错误，避免把权限、网络或仓库故障误判为“麒麟不支持”。这些命令是待执行的行内探测步骤，本次未在公司镜像中执行。

## 尚未确认

- 行内实际基础镜像的 V10 子版本、已装软件、配置的公司源及该源是否提供 Bubblewrap。
- 公开列出的 RPM 对实际镜像的依赖满足情况、签名验证结果及项目所用参数的兼容性。本次没有下载、安装或运行该 RPM。
- 生产容器运行身份、宿主机内核及平台安全策略是否允许项目要求的用户、挂载、PID、网络命名空间；须在最终容器执行已有 DSH 预检，不能用“它是 Linux”或“已安装”替代。

本次仅核查官方公开资料并记录证据，未调用真实模型或 COS，未证明行内部署成功，也未提交、推送或发布。
