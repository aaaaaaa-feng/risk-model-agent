# 跨平台打包入口

仓库提供同一份 PyInstaller spec 和两个本机启动脚本，目标是把本地 FastAPI Web 服务打成可双击启动的目录发行版。这里的构建脚本是可复现入口，不代表当前机器已经完成 macOS 与 Windows 的真实安装验收。

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

发行版仍然只监听 `127.0.0.1`，原始数据和运行数据库留在用户本机。机构环境应自行完成签名、安装权限、升级/卸载和目标数据规模验收后再发布。
