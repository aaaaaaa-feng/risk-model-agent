use std::{
    fs::OpenOptions,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use tauri::{AppHandle, Manager};

use super::protocol::{request_desktop_shutdown, DesktopEndpoint, BACKEND_HOST};

const GRACEFUL_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(12);

pub(super) fn resolve_backend_executable(app: &AppHandle) -> Result<PathBuf, String> {
    #[cfg(debug_assertions)]
    if let Some(explicit) = std::env::var_os("RISK_AGENT_BACKEND_EXECUTABLE") {
        let candidate = PathBuf::from(explicit);
        if candidate.is_file() {
            return candidate
                .canonicalize()
                .map_err(|error| format!("无法解析开发后端路径：{error}"));
        }
        return Err(format!("开发后端文件不存在：{}", candidate.display()));
    }

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("无法定位客户端资源目录：{error}"))?;
    let candidate = resource_dir.join("backend").join(backend_executable_name());
    if !candidate.is_file() {
        return Err(format!("安装包中缺少本地建模服务：{}", candidate.display()));
    }

    let canonical_root = resource_dir
        .canonicalize()
        .map_err(|error| format!("无法验证客户端资源目录：{error}"))?;
    let canonical_candidate = candidate
        .canonicalize()
        .map_err(|error| format!("无法验证本地建模服务：{error}"))?;
    if !canonical_candidate.starts_with(&canonical_root) {
        return Err("本地建模服务越出客户端资源目录，已拒绝启动".to_owned());
    }
    Ok(canonical_candidate)
}

pub(super) fn spawn_backend_process(
    executable: &Path,
    port: u16,
    desktop_token: &str,
    bootstrap_token: &str,
    log_path: &Path,
    install_root: &Path,
) -> Result<(Child, ProcessTreeGuard), String> {
    let log_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
        .map_err(|error| format!("无法打开后端日志文件：{error}"))?;
    let stderr_log = log_file
        .try_clone()
        .map_err(|error| format!("无法初始化后端错误日志：{error}"))?;

    let mut command = Command::new(executable);
    command
        .current_dir(
            executable
                .parent()
                .ok_or_else(|| "打包后的后端可执行文件没有有效目录".to_owned())?,
        )
        .env("RISK_AGENT_HOST", BACKEND_HOST)
        .env("RISK_AGENT_PORT", port.to_string())
        .env("RISK_AGENT_OPEN_BROWSER", "0")
        .env("RISK_AGENT_DESKTOP_TOKEN", desktop_token)
        .env("RISK_AGENT_DESKTOP_BOOTSTRAP_TOKEN", bootstrap_token)
        .env("RISK_AGENT_BACKEND_LOG_PATH", log_path)
        .env("RISK_AGENT_INSTALL_DIR", install_root)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(stderr_log));
    configure_hidden_process(&mut command);

    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动打包后的本地服务 {}: {error}", executable.display()))?;
    let process_guard = match create_process_tree_guard(&child) {
        Ok(process_guard) => process_guard,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };

    Ok((child, process_guard))
}

pub(super) fn graceful_stop_child(
    mut child: Child,
    process_guard: Option<ProcessTreeGuard>,
    endpoint: DesktopEndpoint,
) -> bool {
    if request_desktop_shutdown(endpoint.port, &endpoint.token) {
        let deadline = Instant::now() + GRACEFUL_SHUTDOWN_TIMEOUT;
        while Instant::now() < deadline {
            match child.try_wait() {
                Ok(Some(_)) => {
                    let _ = child.wait();
                    return true;
                }
                Ok(None) => thread::sleep(Duration::from_millis(100)),
                Err(_) => break,
            }
        }
    }
    terminate_child(child, process_guard);
    false
}

pub(super) fn terminate_child(mut child: Child, _process_guard: Option<ProcessTreeGuard>) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;

        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let mut taskkill = Command::new("taskkill.exe");
        taskkill
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let _ = taskkill.status();
    }

    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(windows)]
pub(super) struct ProcessTreeGuard(isize);

#[cfg(windows)]
impl Drop for ProcessTreeGuard {
    fn drop(&mut self) {
        use windows_sys::Win32::Foundation::CloseHandle;

        if self.0 != 0 {
            unsafe {
                CloseHandle(self.0 as _);
            }
        }
    }
}

#[cfg(windows)]
fn create_process_tree_guard(child: &Child) -> Result<ProcessTreeGuard, String> {
    use std::{ffi::c_void, mem::size_of, os::windows::io::AsRawHandle, ptr};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, GetLastError},
        System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
    };

    unsafe {
        let job = CreateJobObjectW(ptr::null(), ptr::null());
        if job.is_null() {
            return Err(format!(
                "无法创建 Windows 后端回收作业（系统错误 {}）",
                GetLastError()
            ));
        }

        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const c_void,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if configured == 0 {
            let error = GetLastError();
            CloseHandle(job);
            return Err(format!("无法配置 Windows 后端回收作业（系统错误 {error}）"));
        }

        let assigned = AssignProcessToJobObject(job, child.as_raw_handle() as _);
        if assigned == 0 {
            let error = GetLastError();
            CloseHandle(job);
            return Err(format!(
                "无法将本地服务纳入 Windows 回收作业（系统错误 {error}）"
            ));
        }

        Ok(ProcessTreeGuard(job as isize))
    }
}

#[cfg(not(windows))]
pub(super) struct ProcessTreeGuard;

#[cfg(not(windows))]
fn create_process_tree_guard(_child: &Child) -> Result<ProcessTreeGuard, String> {
    Ok(ProcessTreeGuard)
}

#[cfg(windows)]
fn configure_hidden_process(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn configure_hidden_process(_command: &mut Command) {}

pub(crate) fn open_directory(path: &Path) -> Result<(), String> {
    let mut command = if cfg!(windows) {
        let mut command = Command::new("explorer.exe");
        command.arg(path);
        command
    } else if cfg!(target_os = "macos") {
        let mut command = Command::new("open");
        command.arg(path);
        command
    } else {
        let mut command = Command::new("xdg-open");
        command.arg(path);
        command
    };
    configure_hidden_process(&mut command);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开日志目录：{error}"))
}

fn backend_executable_name() -> &'static str {
    if cfg!(windows) {
        "risk-model-agent.exe"
    } else {
        "risk-model-agent"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn executable_name_matches_platform() {
        assert_eq!(backend_executable_name().ends_with(".exe"), cfg!(windows));
    }
}
