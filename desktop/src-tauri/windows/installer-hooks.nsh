; 仅迁移本产品 1.1.2 及更早版本使用的固定 Inno Setup 安装。
; 不枚举其他卸载项，也不直接删除旧安装目录或任何用户数据目录。
!define RMA_LEGACY_INNO_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\{4CE3329A-CF6F-49E0-86C7-BE5C38DB1474}_is1"
!define RMA_LEGACY_PRODUCT_NAME "风控建模 Agent"
!define RMA_LEGACY_PUBLISHER "Risk Model Agent"

!macro RMA_ABORT_LEGACY_MIGRATION MESSAGE
  DetailPrint "${MESSAGE}"
  IfSilent +2
  MessageBox MB_ICONSTOP|MB_OK "${MESSAGE}"
  Abort
!macroend

; 精确打开固定旧键，不通过 DisplayName 等可缺失值推测键是否存在。
; 0x80000001=HKCU，0x0101=KEY_QUERY_VALUE|KEY_WOW64_64KEY。
!macro RMA_DETECT_LEGACY_INNO_KEY OUTPUT
  System::Call 'Advapi32::RegOpenKeyExW(p 0x80000001, w "${RMA_LEGACY_INNO_UNINSTALL_KEY}", i 0, i 0x0101, *p .r0) i .r1'
  ${If} $1 = 0
    System::Call 'Advapi32::RegCloseKey(p r0) i .r1'
    ${If} $1 != 0
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "无法确认旧版安装状态。为保护现有项目，本次安装已停止。"
    ${EndIf}
    StrCpy ${OUTPUT} "1"
  ${ElseIf} $1 = 2
    StrCpy ${OUTPUT} "0"
  ${Else}
    !insertmacro RMA_ABORT_LEGACY_MIGRATION "无法确认旧版安装状态。为保护现有项目，本次安装已停止。"
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREINSTALL
  Push $0
  Push $1
  Push $R0
  Push $R1
  Push $R2
  Push $R3
  Push $R4
  Push $R5
  Push $R6
  Push $R7
  Push $R8
  Push $R9
  StrCpy $R0 ""
  StrCpy $R1 ""
  StrCpy $R6 "0"

  ; Tauri 的 x64 currentUser 安装已在 .onInit 中选中 64 位注册表视图，
  ; 与旧 Inno Setup 的 x64compatible 安装保持一致。
  !insertmacro RMA_DETECT_LEGACY_INNO_KEY $R6
  ${If} $R6 == "1"
    ClearErrors
    ReadRegStr $R2 HKCU "${RMA_LEGACY_INNO_UNINSTALL_KEY}" "DisplayName"
    ReadRegStr $R3 HKCU "${RMA_LEGACY_INNO_UNINSTALL_KEY}" "Publisher"
    ReadRegStr $R4 HKCU "${RMA_LEGACY_INNO_UNINSTALL_KEY}" "DisplayVersion"
    ReadRegStr $R5 HKCU "${RMA_LEGACY_INNO_UNINSTALL_KEY}" "InstallLocation"
    ReadRegStr $R7 HKCU "${RMA_LEGACY_INNO_UNINSTALL_KEY}" "UninstallString"
    ReadRegStr $R8 HKCU "${RMA_LEGACY_INNO_UNINSTALL_KEY}" "QuietUninstallString"
    ${If} $R2 != "${RMA_LEGACY_PRODUCT_NAME}"
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "检测到无法确认归属的旧版安装记录。请先从 Windows 应用列表手动处理后再安装。"
    ${EndIf}
    ${If} $R3 != "${RMA_LEGACY_PUBLISHER}"
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版安装发布者不匹配。为避免执行错误程序，本次安装已停止。"
    ${EndIf}
    ${If} $R4 == "1.0.0"
    ${ElseIf} $R4 == "1.0.1"
    ${ElseIf} $R4 == "1.0.2"
    ${ElseIf} $R4 == "1.1.0"
    ${ElseIf} $R4 == "1.1.1"
    ${ElseIf} $R4 == "1.1.2"
    ${Else}
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版安装版本不在可迁移范围内，请先手动卸载后再安装。"
    ${EndIf}
    ${If} $R5 == ""
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版安装缺少可信的安装目录，请先手动卸载后再安装。"
    ${EndIf}
    ; 追加 `\.` 后再规范化，统一处理注册表可能保留的尾部反斜杠。
    GetFullPathName $R5 "$R5\."
    GetFullPathName $R9 "$LOCALAPPDATA\Programs\RiskModelAgent\."
    ${If} $R5 != $R9
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版不在官方默认安装目录，请先从 Windows 应用列表手动卸载后再安装。"
    ${EndIf}
    IfFileExists "$R5\risk-model-agent.exe" +2 0
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版安装目录内没有风控建模 Agent 主程序，请先手动处理后再安装。"
    IfFileExists "$R5\unins000.exe" +2 0
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版安装目录内没有可信的官方卸载程序，请先手动卸载后再安装。"
    ; 注册表命令只用于与规范路径交叉校验，绝不直接执行。这样即使
    ; InstallLocation 被误改到另一个 Inno 软件目录，也不会卸载其他软件。
    StrCpy $R0 '$\"$R5\unins000.exe$\"'
    ${If} $R7 != $R0
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版卸载路径与安装目录不一致。为避免执行错误程序，本次安装已停止。"
    ${EndIf}
    ${If} $R8 != "$R0 /SILENT"
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版静默卸载路径与安装目录不一致。为避免执行错误程序，本次安装已停止。"
    ${EndIf}

    DetailPrint "检测到旧版风控建模 Agent，正在安全卸载后继续升级。"
    ClearErrors
    ExecWait '$R0 /VERYSILENT /SUPPRESSMSGBOXES /NORESTART' $R1
    ${If} ${Errors}
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "无法启动旧版卸载程序。为避免产生半升级状态，本次安装已停止。"
    ${EndIf}
    ${If} $R1 != 0
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版卸载程序执行失败（退出码：$R1）。请先手动卸载旧版，再重新安装。"
    ${EndIf}

    ; 退出码为 0 仍不足以证明卸载完整；固定卸载键必须已经消失。
    !insertmacro RMA_DETECT_LEGACY_INNO_KEY $R6
    ${If} $R6 == "1"
      !insertmacro RMA_ABORT_LEGACY_MIGRATION "旧版卸载未完整结束。为保护现有项目和设置，本次安装已停止。"
    ${EndIf}
    DetailPrint "旧版程序与快捷方式已交由原卸载程序移除，用户工作区保持不变。"
  ${EndIf}
  Pop $R9
  Pop $R8
  Pop $R7
  Pop $R6
  Pop $R5
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R1
  Pop $R0
  Pop $1
  Pop $0
!macroend
