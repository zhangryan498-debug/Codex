# 接口配置｜环境变量清单（**只列名，不含值**）

> ⚠️ **本包不含任何凭据。** 采样前需自备 `TIKHUB_TOKEN`。

| 变量名 | 用途 | 从哪拿 | 值是否在本包内 |
|---|---|---|---|
| `TIKHUB_TOKEN` | TikHub OpenAPI 鉴权（全部采集端点） | TikHub 控制台 → API Token | ❌ **不在包内，需自备** |

放置位置：项目根 `data-pipeline/.env`（已被 `.gitignore` 排除，不会误提交）。

```bash
# 在项目根建 .env（注意：别带进任何 git 操作）
echo "TIKHUB_TOKEN=<你的 token>" > data-pipeline/.env
```

## 凭据纪律

1. **值绝不入库**（文档、注释、示例、报告、提交历史都一样）。
2. **每次提交前做泄漏自检**：token 全值与**前 8 位前缀**都不得命中待提交文件。
3. 排查用命令（只看命中文件名，不打印值）：

   ```bash
   python3 - <<'PY'
   import os, re, subprocess
   vals = []
   for envf in ("data-pipeline/.env", ".env"):
       if not os.path.exists(envf): continue
       for ln in open(envf, encoding="utf-8"):
           m = re.match(r"\s*(?:export\s+)?[A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD)[A-Z0-9_]*\s*=\s*[\"']?([^\"'\s]+)", ln)
           if m and len(m.group(1)) >= 8: vals.append(m.group(1))
   files = subprocess.run(["git","ls-files"],capture_output=True,text=True).stdout.split()
   files += subprocess.run(["git","ls-files","--others","--exclude-standard"],capture_output=True,text=True).stdout.split()
   hits = []
   for f in set(files):
       if not os.path.isfile(f): continue
       t = open(f, encoding="utf-8", errors="ignore").read()
       for v in vals:
           if v in t or v[:8] in t: hits.append(f)
   print("命中：", hits or "无")
   PY
   ```

4. 采集脚本自带**连续失败熔断**（同端点连错 8 次即停），避免参数契约错时空烧额度。

## Codex 补充（2026-11-08）

Just One 的新调用使用本机 JUSTONE_API_TOKEN（环境变量或受保护本地 .env）；包内不保存值。FastMoss MCP 的 OAuth 由 Codex 管理，与 CLI 独立，不能复制令牌到项目。TIKHUB_OPENAPI_SPEC 指定计费扫描的 OpenAPI 文件。DT_JD_SOURCE 指定补回的历史 JD 生成器目录。所有历史计费和政策结论需重新验证。

## FastMoss OpenAPI（2026-11-15）

REST API 使用 FastMoss 开发者中心 API Key 管理页的 `client_secret`。运行 `scripts/fastmoss_openapi.ps1 -Configure` 后，密钥由当前 Windows 用户的 DPAPI 加密并存于 `%LOCALAPPDATA%\DandT\fastmoss-openapi.dpapi`。换 Windows 用户或机器时重新配置。调用通过 HTTPS Bearer 鉴权，配置和计费独立于 MCP／CLI。参数及命令见[迁移运行说明](../docs/Codex迁移运行说明.md#fastmoss-openapi)。

## 本地语音（2026-11-09）

Large-v3-turbo 使用项目 `.venv`、固定公开模型缓存和 NVIDIA 官方运行库，无需 API 密钥。执行 `scripts/transcribe_local_video.py`，由进程内DLL加载助手补齐CUDA依赖；不修改系统PATH/驱动。安装、离线调用、来源与验收边界见 `docs/本地语音GPU运行说明.md`。首次下载联网，后续转写素材留本机。
