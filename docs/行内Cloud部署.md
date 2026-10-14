# 行内代码检视测评工程

AACR 为主测，当前交付五例用于云端接入验证。工程执行现有 LCR 和裁判流程，保存答卷、日志和中文报告。正式100+例清单尚未纳入本工程。

## 一、提交到行内仓库

将 code-review-benchmark-source.zip 带入行内并解压。新建空行内 Git 仓库（例如 code-review-benchmark），不要自动生成 README。下面是 PowerShell 操作；路径按实际解压位置调整。

```powershell
cd D:\code\code-review-benchmark
git init
git add .
git status --short
```

检查应只有源码、案例清单、文档和资源校验清单，没有 .venv、runs、repos、wheelhouse、lcr、密钥配置。然后：

```powershell
git commit -m "Add cloud benchmark runner"
git branch -M main
$repoUrl = Read-Host '输入新建的行内仓库 Git 地址'
git remote add origin $repoUrl
git push -u origin main
```

在 DevAgent Cloud 选择该仓库和 main 分支。平台检出后，用 pwd 确認实际工作路径；后续每次工具调用均先 cd 到这个路径，不要假定上次 shell 的目录或环境变量仍在。

## 二、安装离线资源

另将 lcr-cloud-assets.tar.gz 上传平台（约44MB，不提交 Git）。在平台的仓库根目录执行，--archive 使用资源包的实际绝对路径：

```bash
python3 tools/prepare_assets.py --archive /实际上传路径/lcr-cloud-assets.tar.gz
python3 tools/setup.py
```

首次安装工具 timeout 可设360000ms；Linux x86_64、Python3.11、Node20、Git及venv/ensurepip为环境要求。此过程不联网、不调用模型。资源包的SHA256必须与源码中的assets-archive.sha256一致；已有资源不同会停止覆盖。资源变化须同时更新校验清单并新建测评批次。

本工程不需要 npm install 或 Go。tools/setup.py 验证资源、离线安装依赖、恢复案例 Git 历史，并执行 LCR version。lcr 为此前增加诊断日志的测试版，CLI检视策略没有新增修改；Linux实际可用性仍须由平台安装验证，不能用本机交叉编译代替。

## 三、配置模型与创建批次

在平台环境变量配置 LCR_LLM_BASE_URL、LCR_LLM_TOKEN、LCR_LLM_MODEL，以及裁判 JUDGE_BASE_URL、JUDGE_API_KEY、JUDGE_MODEL、JUDGE_USE_MOCK=false。不要把令牌放到提示词、命令或仓库。

在仓库根目录执行：

```bash
.venv/bin/python backend/src/cloud.py init --suite aacr --run-id cloud-pilot-01 --dataset data/pilot5.jsonl --model "$LCR_LLM_MODEL" --timeout-minutes 30
.venv/bin/python backend/src/cloud.py check --run-id cloud-pilot-01
```

init只执行一次，冻结数据范围、工具及规则哈希、模型和超时。check不调用模型，会列出各例需要的平台命令时限。默认结果保存在本仓库runs/；Git忽略并不代表平台持久化。平台有持久化目录时，所有cloud.py命令统一追加 --runs-root /实际持久化目录。

## 四、先验证一例

首先确认平台单条工具命令允许2000000ms，任务生命周期也足够。五分钟测试没有验证这一上限。在工具timeout真实设为2000000ms的同步命令中运行：

```bash
.venv/bin/python backend/src/cloud.py review --run-id cloud-pilot-01 --instance 'cline__cline@419534c' --external-timeout-seconds 2000
```

external-timeout-seconds只校验声明，不能延长平台限制。LCR单文件超时30分钟；案例外层按文件数计算，最长五例预算可达122分钟。不要用 &、nohup 或后台子进程，平台已验证会清理它们。

## 五、逐例执行、检查、恢复

```bash
.venv/bin/python backend/src/cloud.py next --run-id cloud-pilot-01
.venv/bin/python backend/src/cloud.py status --run-id cloud-pilot-01
```

next返回下一尚未尝试案例；逐例调用review，按check设置工具实际timeout和声明时限。已完整答卷自动跳过，失败不自动无限重试。若状态为recoverable_check_required，先recover：

```bash
.venv/bin/python backend/src/cloud.py recover --run-id cloud-pilot-01 --instance '实际案例ID'
```

恢复只检查已有输出，不调用模型。失败案例先看raw和diagnostics，确认旧执行已终止后，在review命令追加--retry。这里是案例重跑，不是自动恢复CLI session内部步骤。同批次文件锁禁止同时启动多个命令。

## 六、评分及结果保存

五例答卷全部完整后：

```bash
.venv/bin/python backend/src/cloud.py eval --run-id cloud-pilot-01
.venv/bin/python backend/src/cloud.py export --run-id cloud-pilot-01
```

评分会调用真实裁判三轮，生成中文报告；为评分另设足够工具时限。目前整批评分不支持逐案例断点恢复，100+例正式评分前应验证耗时是否适配平台。export生成runs/cloud-pilot-01-results.tar.gz，用平台提供的下载或持久化方式保存；结果不提交Git。

## 七、扩大范围前

支持多仓库和任意数量标准JSONL清单；新增案例要带齐base/head与共同祖先历史，仓库位于repos/owner__repo，或init指定--repos-root。以新run-id冻结正式案例集。平台Agent仅调度脚本，标准答案reference_comments不能传给LCR检视。

确认关浏览器/本机后的任务继续、最长命令和任务时限、环境回收后的文件保留、日志结果下载方式，再安排无人值守批量运行。绑定仓库解决工程交付，结果持久化需要平台提供。

## 本地工程维护

此目录是当前唯一测评源码入口，Mac运行环境已准备。更新源码后用 `.venv/bin/python tools/build_delivery.py` 重建父目录的源码ZIP和Linux资源包；构建只读取当前工程，不依赖旧OCR/Windows/云端打包目录。

本地依赖环境通过 `.venv` 软链接指向父目录 `ocr-lab/.venv`，请勿删除该环境。`cli-logging-source/`保留日志测试CLI构建来源；`history/ocr/`集中保存早期OCR测评证据，两者都被Git和交付包排除。
