# Windows 安装迁移边界

Tauri 1.2.0 的 NSIS 安装器通过 `installer-hooks.nsh` 迁移旧 Inno Setup 版本。

迁移遵守以下固定边界：

- 仅检查当前用户注册表中的固定卸载键：`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{4CE3329A-CF6F-49E0-86C7-BE5C38DB1474}_is1`。
- 只有产品名、发布者、版本、旧版主程序均通过固定规则，安装目录正好是 `%LOCALAPPDATA%\Programs\RiskModelAgent`，并且 `UninstallString`、`QuietUninstallString` 都与目录内的 `unins000.exe` 一致后，才执行这个固定文件；不会直接执行注册表中的命令字符串。调用时统一追加 Inno Setup 的静默、禁止弹窗和禁止重启参数。旧版若安装在自定义目录，必须先从 Windows 应用列表手动卸载。
- 卸载程序无法启动、返回非零退出码，或执行后固定卸载键仍存在时，立即终止新版本安装，不能继续形成半升级状态。
- Hook 不枚举其他软件、不猜测安装位置，也不直接删除旧目录、旧快捷方式或注册表项；这些内容只交给旧版本自己的卸载程序处理。
- Hook 绝不读取、移动或删除 `%LOCALAPPDATA%\RiskModelAgent`，也不会触碰用户首次选择的工作区。项目、模型、Notebook、报告、API 配置和工作区指针继续保留。

新 Tauri 安装器使用 `currentUser` 模式。旧 Inno 默认目录是 `%LOCALAPPDATA%\Programs\RiskModelAgent`，Tauri 以当前产品名生成的首次安装默认目录是 `%LOCALAPPDATA%\风控建模 Agent`。迁移时必须先由旧卸载程序移除旧程序及快捷方式，再写入新目录；迁移成功后只能保留一套可启动的程序。后续 Tauri 升级会继续复用已经登记的新安装位置。
