# 跨平台打包入口

仓库提供同一份 PyInstaller spec 和两个本机启动脚本，目标是把本地 FastAPI Web 服务打成可双击启动的目录发行版。当前已在本机 macOS arm64 用 PyInstaller 6.22.2 真实构建，并启动包后验证 `/api/health` 与首页返回 200；`.github/workflows/package.yml` 还会在推送到 `main` 时分别构建 macOS arm64 与 Windows x64，并执行目标平台回环烟测、上传 artifact。CI 通过不等于两端签名、安装、升级/卸载和目标机器验收已完成。

在没有安装 PyInstaller 的环境中，可以先运行不依赖打包工具的契约检查：

```bash
python scripts/verify_packaging.py
```

它只检查入口、资源目录、spec 的 onedir 配置和可选依赖是否齐全；通过不等于已经完成目标平台的真实构建。

## macOS

```bash
python3.9 -m venv .venv
.venv/bin/pip install ".[package]"
.venv/bin/python -m PyInstaller packaging/risk_model_agent.spec --noconfirm --clean
open dist/risk-model-agent
```

也可以双击 `scripts/start_mac.command`；若没有 `dist/risk-model-agent`，它会回退到仓库 `.venv` 的源码启动。

## Windows

在 PowerShell 中安装 Python 3.9+ 后运行：

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install ".[package]"
.venv\Scripts\python -m PyInstaller packaging\risk_model_agent.spec --noconfirm --clean
.\scripts\start_windows.ps1
```

推送到 `main` 后，跨平台构建结果可在 GitHub Actions 的 `package` workflow 下载；它是打包回归门禁，不是后置的独立评测 Harness。

发行版仍然只监听 `127.0.0.1`，原始数据和运行数据库留在用户本机。机构环境应自行完成签名、安装权限、升级/卸载和目标数据规模验收后再发布。
