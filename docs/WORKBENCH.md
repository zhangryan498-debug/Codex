# 运行工作台

工作台入口文件为 `tools/dt-client-poster-mvp/launch.py`。Windows 启动器 `Start-DT-Poster.cmd` 也随代码保留。两条开发线共用本地 PSD 引擎。启动时实际做一次合成导出、PSD 回读；缺依赖时启动失败。整个流程使用本机文件。

## 先定位本机依赖

- Windows Python 3.10 或更新，项目岗位配置生成器使用 Python 3.11+ 的 `tomllib`。商品启动经现成 Python 3.12 和 Pillow 13.0.0 验证。可使用指定的本机解释器，个人配置和 .venv 不在 Git。
- Node.js 和三个已有模块 `@napi-rs/canvas 0.1.100`、`sharp 0.35.5`、`ag-psd 34.2.1`。优先解析 `tools/psd-engine/node_modules`；模块已装在别处时，设置 `DT_PSD_DEPENDENCIES` 为本机模块目录列表，Windows 用分号分隔。引擎清单在 `tools/psd-engine/package.json`。无需复制缓存或在启动时下载。
- 浏览器回归可使用本机 Playwright 1.62.1、pngjs 7.0.0 和 Chrome。旧项目中的转写模型、GPU 库、FFmpeg／OpenMontage 及 WinRT OCR 二进制均保持原位置。具体岗位运行前复核 `interface/` 的依赖和本机资料授权。本次商品启动和浏览器检查没有验证这些岗位依赖。

## 默认开发线 · 本机预览

在仓库根目录 PowerShell 运行。变量由本机填写，路径和端口须使用新建测试目录，不指向正在使用的任务。

```powershell
$dtPython = '<现有 Python 的完整路径>'
$dtNode = (Get-Command node).Source
$env:DT_PSD_DEPENDENCIES = '<本机模块目录>;<本机 ag-psd 模块目录>'
$dtProject = (Get-Location).Path
$dtProfile = Join-Path $env:LOCALAPPDATA 'DT/IndependentWorkbench/bava-tokenplan-new-test-design-workspace'
& $dtPython -B -X utf8 './tools/dt-client-poster-mvp/launch.py' --project $dtProject --engine './tools/psd-engine/engine.cjs' --node $dtNode --profile $dtProfile --port 8784 --foreground-token-plan
```

打开启动器回显的回环地址（例 `127.0.0.1:8784`）。在「模型连接」用界面提供的测试场景、配置语言用途，再到商品任务。此分支要求独立 `bava-tokenplan-new-test-design-` 配置和前台启动参数，API Key 只允许本机替身，改版客户设置使用下文独立入口。图片和视频的用途配置可以保留，真实生成适配仍待验。


## 改版工作台 · 模型连接

以 `--foreground-token-plan --customer-model-workspace` 启动新版客户配置。前一个参数是原工作台启动器的模式标记。新版页面和服务端均阻止 Token Plan 直连。预览使用上节独立配置，不能把替身设为本人账号。

1. 从「设置 → 模型连接」选择供应商和连接方式。内置连接展示已知目录地址。自定义连接先选 OpenAI 兼容或 Anthropic 兼容，再填写服务商提供的公共 HTTPS API 基础地址。
2. 按「本机输入 API Key」打开 Windows 本机窗口，核对协议和实际目录地址再输入。Key 留在该进程的本次会话内存。取消不查询，关闭连接或重启工作台后需要重新输入。
3. 本机输入完成后，另点「获取可用模型」。工作台只发目录 GET。OpenAI 或兼容端使用 Bearer 鉴权，Anthropic 兼容端用 `x-api-key` 和协议版本头。百炼北京业务空间使用公开的空间原生目录。目录不支持、权限失败及空列表各自显示，均不发推理探测。目录请求本身不生成推理用量。第三方接口费用以该服务约定为准。
4. 分别为语言、生图和视频选账户和型号。可跨供应商。目录响应、官方参考、手填声明、实际执行状态分别显示。OpenAI 列表不提供模态或工具调用能力时可手动标记用途，调用仍待验证。普通百炼的空间目录不能代替推理授权。改协议、地址或重新输入凭据时，由对应账户重新获取目录，不沿用旧响应。
5. 从商品任务返回原草稿。现有商品图片、已有文案和已有视频仍走相应的本机处理。生图和视频只在相应新素材步骤才需要。语言、图片和视频真实推理当前均未接入新版目录流程。测试实例可用回环替身核对三条不同路线。

自定义地址禁止用户信息、查询参数、重定向、私网解析和已知 Token Plan 专用地址。选择协议和地址后在本机窗口查看实际目标。Token Plan 的官方参考与传统兼容地址需区分。兼容对话并不保证提供目录查询。厂商支持的交互工具是单独接法，当前未在 D&T 启用。

- [OpenAI 模型目录](https://developers.openai.com/api/reference/resources/models/methods/list)
- [Anthropic 模型目录](https://platform.claude.com/docs/en/api/models/list)
- [百炼北京目录](https://help.aliyun.com/zh/model-studio/list-models)及[推理授权](https://help.aliyun.com/zh/model-studio/list-model-permissions)
- [百炼 Token Plan 适用规则](https://help.aliyun.com/zh/model-studio/more-tools)与[千问 AI 平台适用规则](https://platform.qianwenai.com/docs/developer-guides/clients-and-developer-tools/other-tools)

`--account-catalog-fixture` 仅在独立 `bava-tokenplan-new-test-customer-` profile 可用，地址必须是固定 IPv4 回环。客户生产 profile 拒绝这一参数。
## 稳定开发线 · 8775 历史版本

稳定分支 `stable/customer-entry-8775` 保留旧工作台代码与本机验收证据。它包含厂商适用范围尚未确认的自有进程直连。Token Plan 用户导向以新版的限制为准。旧任务与资料继续保存在其独立本机目录。新版 8788 适用本页的模型目录流程。

## 本机资料位置

- 个人凭据只走原授权绑定的系统途径，账户设置和模型用量单独核对。工作台不要复用原 profile 当作测试目录。
- 获准原视频、原图、旧任务及原始回执从原资料位置查询。历史拆解锚点若需在归档版使用，可由资料授权人将同一历史原件放入其指定 profile 的 `private-fixtures/cn-bava-human-anchor.json`，原件摘要门禁仍保留。归档版的菲律宾竞品库存接件只复现合成路径，获准观测快照及绑定仍从原实例读取。
- 真店、发布、充值和把客户资料传给供应商均需该次授权。测试只能指向另起的测试端口与合成 profile。

## 复验

`node tools/psd-engine/test-synthetic.cjs` 做一次本机合成 PSD 可编辑文字层与回读检查。预览线的 `tests/workbench/verify-workbench.cjs` 接受显式新测试回环地址，在临时目录保留合成报告，避免把报告混进 Git。它会在指定工作台创建合成任务和模型配置，只对空白测试 profile 使用。岗位配置可用 `python -B scripts/build_codex_agents.py` 在本项目重建。


### 普通百炼任务调用

在新版 `模型连接` 选择阿里云百炼与北京普通工作空间。本人在 Windows 窗口输入该工作空间的 Key，再在网页主动获取目录并选用途。目录标注能力和当前连接状态。账号能否实际调用仍须本人按账户权限验证。Key 只保留在本机凭据进程的当前会话，重启或过期后要重新连接。

从首页进入 `商品任务`，保存带商品图的任务，在 `为这个任务使用模型` 选相应用途。每次启动前会显示账户、模型、出站文字和费用提示。

- 语言 `qwen-plus`。发送商品名称和已保存的文字事实，以及一个候选文案工具定义。只有模型明确调用该工具并返回符合版面规则的标题与副标题，才留同任务的待审 JSON。既有确认文案沿用。候选文案仍需人工审校，现场正式卖点 Skill、商城岗位 Agent 与这条普通 API 路径独立验收。
- 生图 `wan2.6-t2i`。仅发送用户填写的文本描述，下载 1280 × 1280 PNG 到本机同任务。已有商品图做本机排版可跳过生成。
- 视频 `wan2.7-t2v` 或 `wan2.7-t2v-2026-06-12`。仅发送用户填写的文本描述，使用 720P、16:9、5 秒。工作台保存供应商的任务号，重开可用原账户查询并下载 MP4。提交结果不明且未收到可信任务号时，先到供应商侧核对。工作台不会自动补发提交。视频生成是商品任务的单独产出，当前不含图生视频或自动剪辑。

原图、现有 PSD、草稿均留在本机。已下载的结果按任务、版本、模型和账户保存。查询视频状态只用原任务号与原账户。其它供应商及模型按各自目录和实际适配显示。两个 Token Plan 直连入口继续停用。

[百炼 Chat 接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope) · [文生图](https://help.aliyun.com/zh/model-studio/text-to-image-v2-api-reference) · [文生视频](https://help.aliyun.com/zh/model-studio/text-to-video-api-reference)


### 在正常商品任务制作主图、海报和详情页

从 `模型连接` 选择本人已开通的北京普通工作空间，并给语言用途配置支持工具调用的 `qwen-plus`。保存包含商详证据的中国站商品任务，文案尚未确认时可以选择「开始制作商品三件套」。留空语言模型时仍可保存资料，已有确认文案留在原排版流程。

确认窗列出当前账户、模型、发送内容和费用提示。确认才会开始，整链最多发起 7 次语言模型请求。原照片、PSD 和商详文件留在设备上，商城岗位原文和手册在本机读取。首次模型请求带商品名、两条以内商品事实和四项商详文字，工具往返带任务版本、允许的工具定义、核验标识与必要结果。商城岗位审核只发送一次性审核标识、同版校验结果、产物规格和原生文字层数。工作台在每轮核对原账户、模型和凭据进程。

卖点执行器沿用仓库 A/B 轮转和正式 Skill 规则。锁定文案后需本人审校海报排版，再在本机生成主图、海报和详情页。独立商城岗位审核和收件进程核对同商品同版，主会话确认收到商城结果后开放取件。开窗、取消、刷新和冷取仅访问本机。失败后停止，用户选择的连接、计费方式和模型不会自动替换。

客户和回环验证共用相同的任务入口、确认和处理代码。回环仅可在独立测试资料目录配置。真实客户入口使用原资料目录和普通供应商配置。两个个人 Token Plan 直连继续停用。
