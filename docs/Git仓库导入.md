# 测评仓库自动准备

新导入的纯案例包不再要求先上传仓库。案例的 repo 填写行内组织/项目名，例如 `S990768/LV01.30_Checkmate`。导入案例集后直接新建测评，平台在执行前自动准备所需提交。

Git 服务地址通过环境变量 `BENCH_GIT_BASE_URL` 配置，默认 `https://gitee-proxy.paasst.cmbchina.cn`。最终地址为 `<服务地址>/<repo>.git`，在导入案例时保存。测评服务器必须能访问该地址。认证方式见下文。

首次完整 clone，不使用浅克隆；已有本地历史包含所需提交时直接复用，缺少提交时 fetch，并校验 base、head 和共同历史。远端必须保留案例提交，包括迁移时的 bench_preserved 分支。无法下载的提交会明确报错，不会当作零问题继续评分。

本地 LCR 等工具使用平台自动准备的代码。新建公司服务端 Agent 测评直接发送案例 base/head，由 Agent 自己拉取仓库，平台不 clone。历史批次按冻结的 diff_mode 保留原有口径，已有远端请求继续使用原提交范围。适配器升级仍受历史工具身份校验约束，不能通过改写批次配置绕过；旧批次可能要求原版本执行。

仓库管理已从导航移除。旧入口和旧 bundle 案例绑定保留兼容，旧内置案例及历史案例集不会自动改为新的 Git 来源；使用新流程请导入采用行内仓库名的纯案例包。COS 保存新案例配置及 Git 来源；无持久化磁盘时，重建后按 Git 来源重新准备代码。

缓存位于数据目录 git-import-cache 和 repos。仅支持 HTTP(S)，不支持交互式登录或跨地址重定向；不递归下载子模块或 LFS 文件。单次 clone/fetch 超时一小时。页面显示仓库准备进度。

## Git 认证

本地默认复用启动平台账户的 Git 全局/系统配置和凭证助手。已有 `git ls-remote` 可用的认证无需重复配置。后台禁止交互输入，不会等待输入密码或弹出凭证助手登录窗口。

服务器通过部署平台的密钥环境变量同时注入 `BENCH_GIT_USERNAME` 和 `BENCH_GIT_PASSWORD`（密码或服务支持的访问令牌）。设置后优先使用这对凭证，不再使用凭证助手；只配置其中一个会报错。所有实例均需配置，并重启服务生效。

凭证只用于当前 Git 子进程的、限定仓库 URL 的 HTTP 认证头，不写入命令参数、仓库地址、案例包或 Git 配置文件。错误日志会隐藏配置的密码、编码凭证及 URL 内凭证。不要将真实密码写入案例集或 agent.json。Agent 接口配置与 Git 认证是独立配置。

## 同一案例集混用 Git 和 COS

迁移未完成时，可以在纯案例包 `manifest.json` 中增加 `repository_sources`。`repositories` 列表继续保留；来源对象的键必须与该列表完全一致。未提供新字段的旧纯案例包仍默认走 Git，旧 bundle 包仍保留原有来源。

案例包只需按仓库标识填写来源，不需要填写摘要、大小或版本：

```json
{
  "repository_sources": {
    "S990768__LV01.30_Checkmate": "git",
    "CherryHQ__cherry-studio": "cos"
  }
}
```

Git 按仓库名和 `BENCH_GIT_BASE_URL` 生成地址。COS 在导入时按仓库名查询平台已登记的当前版本，内部固定校验信息；缺少登记记录时明确提示缺哪个仓库，不需要用户导出 registry.json。服务启动须已恢复当前环境的 COS 仓库登记信息。

旧的显式 Git URL、COS 版本配置继续兼容；如新旧仓库名称不同，可使用 `{"source":"cos","repository":"旧组织__旧项目"}` 显式指定。平台不会猜测仓库映射，也不在 Git/COS 失败时自动切换来源。

将来源对象（即上述 `repository_sources` 的内容）单独保存为 `sources.json`，可通过已有工具制作案例包：

```powershell
.\.venv\Scripts\python.exe tools\build_case_config_upload.py --suite aacr --name "混合来源案例集" --dataset .\cases.jsonl --repository-sources .\sources.json --output .\cases-mixed.zip
```

导入新包后正常新建测评即可，本地 LCR 等工具会逐仓库准备 Git/COS 资源。准备日志显示具体来源，已有提交可复用缓存。公司服务端 Agent 自行拉取 Git，不能消费平台的 COS bundle；选择包含 COS 来源的案例集时，创建测评会列出仍需迁移的仓库。

创建测评时来源随排队配置保存，执行时写入批次目录 `repository-sources.json`，该文件随运行证据备份。重新导入相同案例清单、更新来源时，会先为已有历史批次保留旧来源；新批次使用新来源。仍有排队或运行任务引用该案例集时，禁止更换来源，须等任务结束或取消。已有批次没有来源快照时继续兼容原记录；不要手工删除来源快照。该改动不解除旧批次的工具版本兼容检查。
