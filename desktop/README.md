# Windows Tauri 客户端

`desktop/` 是桌面客户端边界，不复制风控建模业务逻辑：

- Tauri 启动页是唯一可以访问本地 IPC 命令的页面。
- Rust 进程从安装包的 `backend/` 资源目录启动 PyInstaller onedir 服务。
- 后端只监听随机 `127.0.0.1` 端口，并强制 `RISK_AGENT_OPEN_BROWSER=0`。
- Rust 为每次启动生成两个独立 256-bit 随机凭据：启动凭据只用于 `/api/v1/desktop/ready` 与优雅停止；一次性 bootstrap 凭据只用于主 WebView 首次导航。
- bootstrap 成功后后端设置高熵 `HttpOnly` / `SameSite=Strict` Cookie 并 `303` 跳转到首页；除最小健康检查、ready、bootstrap 和 header 鉴权的 shutdown 外，首页、静态资源、业务 API、SSE 和下载都要求该 Cookie。
- 两个凭据由后端捕获后立即从子进程环境删除，不会被 Notebook 或 Worker 继承；桌面 Uvicorn 关闭 access log，避免一次性 URL 进入日志。
- 健康检查通过后，主窗口通过一次性 bootstrap URL 在应用内加载现有 FastAPI/React 页面；桌面模式的公开 health 只返回状态、版本和运行时，不返回工作区或 Provider 明细。
- `main` 窗口没有 Tauri capability，localhost 业务页不能调用 Rust 命令。
- Windows release 主程序使用 GUI subsystem，后端和辅助进程使用 `CREATE_NO_WINDOW`，不弹出终端。
- Windows 后端进程树纳入 `KILL_ON_JOB_CLOSE` Job Object，桌面主进程崩溃后也由操作系统回收。
- 客户端使用官方 single-instance 插件，重复双击只会聚焦已运行窗口。
- 监督器在主界面打开后继续监控后端；后端意外退出时隐藏主界面、恢复中文启动页并允许重试，重复双击也会触发恢复。
- 用户正常关闭客户端时，监督器先调用一次性凭据保护的本地优雅停止端点，让 FastAPI lifespan、SQLite、Worker 和 Notebook 完成收尾；超时后才由 Job Object 强制回收。
- 主界面的非敏感 UI 偏好通过 host Cookie 跨随机端口保存，localStorage 仅用于旧版迁移；密钥、业务字段和数据值不会写入该 Cookie。
- 构建时将冻结后端的版本化文件清单摘要固化进 Rust 客户端；启动前会校验清单、路径边界、完整文件集合、大小和 SHA-256，校验失败时不会启动后端。

完整性清单用于发现漏打包、文件损坏和普通文件替换。它不是代码签名：在没有 Authenticode 的版本中，不能抵抗能够同时修改客户端程序和资源目录的本机高权限攻击者。

## 本地检查

```bash
python ../scripts/create_backend_manifest.py
npm ci
npm run typecheck
npm run build
cd src-tauri
cargo fmt --check
cargo test
cargo clippy --all-targets -- -D warnings
```

普通 debug 检查在还没有冻结后端目录时可以编译，但这种客户端运行时会拒绝启动未校验的后端。正式 release 构建必须显式设置绝对清单路径，否则 `build.rs` 会终止构建：

```powershell
$env:RISK_AGENT_BACKEND_MANIFEST = (Resolve-Path ..\dist\risk-model-agent\backend-manifest.json)
npm run tauri build
```

## 开发运行

默认从 Tauri 资源目录读取后端。调试构建可临时指定已打包的后端可执行文件：

```bash
RISK_AGENT_BACKEND_EXECUTABLE=/absolute/path/to/risk-model-agent npm run tauri dev
```

该环境变量只在 Rust debug 构建中生效，release 客户端只会启动安装包内、并经路径边界校验的后端。
