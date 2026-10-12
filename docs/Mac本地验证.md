> 日常启动已统一为项目根目录的 `npm run setup`、`npm run dev`；生产页面使用 `npm run build` 和 `npm start`。以下主要保留 LCR 和案例资源准备说明。

# Mac 模拟 DevAgent Cloud 的操作流程

当前这台Mac已准备好runtime、Python依赖和五例Git仓库。模型尚未配置，未调用真实模型。仓库根目录：

```
/Users/moxiaojun/个人/Code-Review-Bench标准化案例集测评/ocr-lab/code-review-benchmark
```

## 1. 本地配置模型

在仓库根目录复制模板，打开 model.local.json 填写官方DeepSeek接口令牌和实际可用模型名。检视和裁判可以使用同一令牌。不要在聊天里粘贴令牌。

```bash
cd '/Users/moxiaojun/个人/Code-Review-Bench标准化案例集测评/ocr-lab/code-review-benchmark'
cp examples/model.local.example.json model.local.json
chmod 600 model.local.json
open -e model.local.json
```

如果已存在该文件，直接编辑，不要重新cp覆盖。model.local.json、local-runtime.json、运行日志和安装资源均被.gitignore排除。local.py每次从本地配置读取环境变量，模拟平台环境注入，故新开终端无需重新export。

## 2. 创建正式本地试验批次

```bash
.venv/bin/python backend/src/local.py --timeout-seconds 60 init --suite aacr --run-id mac-smoke-01 --dataset data/pilot1.jsonl --timeout-minutes 10
.venv/bin/python backend/src/local.py --timeout-seconds 60 check --run-id mac-smoke-01
```

先用单文件10分钟上限验证，批次会记录Mac平台、Python版本、CLI与规则哈希、这一例的清单和模型名。不能在同批次偷偷改超时或模型。将来正式性能对比应使用一致预算，Mac这轮用于接入和恢复验证。

## 3. 先跑一例

```bash
.venv/bin/python backend/src/local.py --timeout-seconds 800 review --run-id mac-smoke-01 --instance 'cline__cline@419534c'
```

真实调用DeepSeek，有模型费用。local.py实际控制800秒命令时限，并自动声明给cloud.py，与平台工具timeout分开的错误配置不会发生。一例完整后重新执行相同命令应立即跳过完整答卷。

```bash
.venv/bin/python backend/src/local.py --timeout-seconds 60 status --run-id mac-smoke-01
.venv/bin/python backend/src/local.py --timeout-seconds 60 next --run-id mac-smoke-01
```

失败先看 raw、diagnostics 和 local-command-logs；确认旧任务停止，必要时原review命令追加--retry。恢复已有成功但未转换输出用recover，不用重调模型。超过800秒时模拟平台会终止命令及其子进程；本地记录platform_timeout。

## 4. 先为这一例评分并导出

答卷完整后：

```bash
.venv/bin/python backend/src/local.py --timeout-seconds 1800 eval --run-id mac-smoke-01
.venv/bin/python backend/src/local.py --timeout-seconds 60 export --run-id mac-smoke-01
```

评分真实调用裁判三轮。三轮是裁判重复评分，不是LCR重跑三次。报告生成失败可用report入口复用已评分产物。

这一例的检视、评分、中文报告和结果导出通过后，再创建五例批次：

```bash
.venv/bin/python backend/src/local.py --timeout-seconds 60 init --suite aacr --run-id mac-pilot-05 --dataset data/pilot5.jsonl --timeout-minutes 10
.venv/bin/python backend/src/local.py --timeout-seconds 60 check --run-id mac-pilot-05
.venv/bin/python backend/src/local.py --timeout-seconds 60 next --run-id mac-pilot-05
```

逐例执行，以下2600秒适用于当前五例10分钟单文件预算：

```bash
.venv/bin/python backend/src/local.py --timeout-seconds 2600 review --run-id mac-pilot-05 --instance 'cline__cline@2195a68'
.venv/bin/python backend/src/local.py --timeout-seconds 2600 review --run-id mac-pilot-05 --instance 'cline__cline@419534c'
.venv/bin/python backend/src/local.py --timeout-seconds 2600 review --run-id mac-pilot-05 --instance 'cline__cline@7d18fb9'
.venv/bin/python backend/src/local.py --timeout-seconds 2600 review --run-id mac-pilot-05 --instance 'cline__cline@f098af0'
.venv/bin/python backend/src/local.py --timeout-seconds 2600 review --run-id mac-pilot-05 --instance 'cline__cline@cb4d5c9'
.venv/bin/python backend/src/local.py --timeout-seconds 60 status --run-id mac-pilot-05
```

五例均完整后使用eval和export，将run-id改为mac-pilot-05。新批次不会自动复用别的批次答卷，以保持模型、预算和执行来源清晰。失败案例不能当完整答卷计分；本轮eval要求全部选定案例完整。

## 5. 验证范围与迁移

复用：backend/src/runner/、backend/src/reviewers/lcr/、backend/src/suites/aacr/、JSONL数据、检视规则、答卷转换、三轮裁判、中文报告、文件布局、结果跳过和恢复。local.py模拟独立同步进程、超时清理和平台环境变量注入。

差异：Mac arm64二进制及直接启动器、当前Python3.12；目标云端Linux x86_64/Python3.11/Node启动器。Mac不模拟openEuler、云端网络和真实平台生命周期。Mac启动器按Node启动器相同方式注入打包规则，跳过npm版本检查；此差异写在local-runtime中且冻结工具哈希，不修改LCR源码。

迁移时只提交源码包和文档；在云端全新绑定仓库，使用Linux资源包、tools/prepare_assets.py、tools/setup.py、平台环境变量。云端直接执行cloud.py；平台工具timeout承担本地local.py的外层时限角色。不要上传Mac runtime或本地令牌，也不要沿用Mac批次作为Linux测评批次。

云端最大命令时间、关浏览器或关本机后继续、回收持久化仍须行内验证。裁判整批评分尚无逐案例断点恢复。已有五例范围不代表正式100+清单。

新增OpenAI检视模型、保留DeepSeek裁判的操作见同目录《OpenAI接入.md》。local.py支持--config选择独立本地模型配置。
