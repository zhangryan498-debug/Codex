# 工作台回归

`verify-workbench.cjs` 供预览线使用。先用新的 `bava-tokenplan-new-test-design-` profile 和可用端口启动工作台，再显式传入该回环地址，例如 `node tests/workbench/verify-workbench.cjs http://127.0.0.1:8784`。该脚本会创建合成商品任务和测试账户，应使用空白测试 profile。浏览器只允许本机工作台来源。

`DT_QA_NODE_MODULES` 可以指向已有 Playwright、pngjs 目录，`DT_QA_CHROME` 指向本机 Chrome。没有这些依赖时先查看本机运行指引。合成回执和文件放在系统临时目录，脚本打印实际位置。真人验收、网络和模型供应商不在该回归范围。
