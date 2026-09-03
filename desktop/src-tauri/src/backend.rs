mod logging;
mod process;
mod protocol;
mod window;

use std::{
    env, fs,
    path::{Path, PathBuf},
    process::{Child, ExitStatus},
    sync::{Arc, Mutex, MutexGuard},
    thread,
    time::{Duration, Instant},
};

use logging::{append_desktop_log, create_backend_log};
use process::{
    graceful_stop_child, resolve_backend_executable, spawn_backend_process, terminate_child,
    ProcessTreeGuard,
};
use protocol::{
    desktop_ready_is_valid, generate_desktop_token, reserve_loopback_port, DesktopEndpoint,
    BACKEND_HOST,
};
use serde::Serialize;
use tauri::AppHandle;
use window::{friendly_startup_error, show_main_window, show_recovery_window};

use crate::integrity::verify_backend_bundle;

pub(crate) use process::open_directory;

const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);
const HEALTH_POLL_INTERVAL: Duration = Duration::from_millis(250);
const RUNTIME_POLL_INTERVAL: Duration = Duration::from_millis(500);

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum BackendPhase {
    Starting,
    Ready,
    Failed,
    Stopped,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub(crate) struct BackendStatus {
    pub(crate) phase: BackendPhase,
    pub(crate) message: String,
    pub(crate) detail: Option<String>,
    pub(crate) log_path: Option<String>,
    pub(crate) backend_url: Option<String>,
}

impl BackendStatus {
    fn starting(log_path: Option<String>) -> Self {
        Self {
            phase: BackendPhase::Starting,
            message: "正在启动本地建模服务…".to_owned(),
            detail: None,
            log_path,
            backend_url: None,
        }
    }

    fn failed(
        message: impl Into<String>,
        detail: impl Into<String>,
        log_path: Option<String>,
    ) -> Self {
        Self {
            phase: BackendPhase::Failed,
            message: message.into(),
            detail: Some(detail.into()),
            log_path,
            backend_url: None,
        }
    }

    fn stopped(log_path: Option<String>) -> Self {
        Self {
            phase: BackendPhase::Stopped,
            message: "本地建模服务已停止".to_owned(),
            detail: None,
            log_path,
            backend_url: None,
        }
    }
}

struct BackendRuntime {
    generation: u64,
    child: Option<Child>,
    process_guard: Option<ProcessTreeGuard>,
    endpoint: Option<DesktopEndpoint>,
    status: BackendStatus,
}

struct ReadyBackend {
    backend_url: String,
    bootstrap_token: String,
}

#[derive(Clone)]
pub(crate) struct BackendSupervisor {
    inner: Arc<Mutex<BackendRuntime>>,
    log_dir: Arc<PathBuf>,
}

impl BackendSupervisor {
    pub(crate) fn new(log_dir: PathBuf) -> Self {
        // 日志目录不可写也不能让 GUI 客户端在启动页出现前直接退出。
        // 后台启动任务会再次创建目录，并把失败状态交给启动页展示。
        let _ = fs::create_dir_all(&log_dir);
        Self {
            inner: Arc::new(Mutex::new(BackendRuntime {
                generation: 0,
                child: None,
                process_guard: None,
                endpoint: None,
                status: BackendStatus::stopped(None),
            })),
            log_dir: Arc::new(log_dir),
        }
    }

    pub(crate) fn status(&self) -> BackendStatus {
        self.lock().status.clone()
    }

    pub(crate) fn log_dir(&self) -> &Path {
        self.log_dir.as_ref()
    }

    pub(crate) fn is_restartable(&self) -> bool {
        matches!(
            self.lock().status.phase,
            BackendPhase::Failed | BackendPhase::Stopped
        )
    }

    pub(crate) fn launch(&self, app: AppHandle) -> Result<BackendStatus, String> {
        let generation = {
            let mut runtime = self.lock();
            if matches!(
                runtime.status.phase,
                BackendPhase::Starting | BackendPhase::Ready
            ) {
                return Ok(runtime.status.clone());
            }

            if let Some(child) = runtime.child.take() {
                runtime.endpoint = None;
                terminate_child(child, runtime.process_guard.take());
            }

            runtime.generation = runtime.generation.saturating_add(1);
            let generation = runtime.generation;
            runtime.status = BackendStatus::starting(None);
            generation
        };

        let supervisor = self.clone();
        if let Err(error) = thread::Builder::new()
            .name("risk-agent-backend-startup".to_owned())
            .spawn(move || supervisor.start_in_background(app, generation))
        {
            let technical_detail = format!("无法创建后台启动任务：{error}");
            let log_path = self.log_dir().join("desktop.log");
            {
                let mut runtime = self.lock();
                if runtime.generation == generation {
                    runtime.status = BackendStatus::failed(
                        "客户端启动任务创建失败",
                        "客户端暂时无法创建本地启动任务，请关闭其他程序后重试。",
                        Some(log_path.to_string_lossy().into_owned()),
                    );
                }
            }
            let _ = append_desktop_log(self.log_dir(), &technical_detail);
        }

        Ok(self.status())
    }

    pub(crate) fn stop(&self) {
        let (child, process_guard, endpoint) = {
            let mut runtime = self.lock();
            runtime.generation = runtime.generation.saturating_add(1);
            runtime.status = BackendStatus::stopped(runtime.status.log_path.clone());
            (
                runtime.child.take(),
                runtime.process_guard.take(),
                runtime.endpoint.take(),
            )
        };
        if let Some(child) = child {
            let graceful = match endpoint {
                Some(endpoint) => graceful_stop_child(child, process_guard, endpoint),
                None => {
                    terminate_child(child, process_guard);
                    false
                }
            };
            let outcome = if graceful {
                "backend stopped gracefully"
            } else {
                "backend required forced termination"
            };
            let _ = append_desktop_log(self.log_dir(), outcome);
        }
    }

    fn start_in_background(&self, app: AppHandle, generation: u64) {
        let result = self.spawn_and_wait(&app, generation).and_then(|ready| {
            show_main_window(&app, &ready.backend_url, &ready.bootstrap_token)?;
            Ok(ready.backend_url)
        });

        match result {
            Ok(backend_url) => {
                {
                    let mut runtime = self.lock();
                    if runtime.generation != generation {
                        return;
                    }
                    runtime.status = BackendStatus {
                        phase: BackendPhase::Ready,
                        message: "本地服务已就绪".to_owned(),
                        detail: None,
                        log_path: runtime.status.log_path.clone(),
                        backend_url: Some(backend_url),
                    };
                }
                self.monitor_ready_backend(&app, generation);
            }
            Err(error) => {
                let (child, process_guard) = {
                    let mut runtime = self.lock();
                    if runtime.generation != generation {
                        return;
                    }
                    let log_path = runtime.status.log_path.clone().or_else(|| {
                        Some(
                            self.log_dir()
                                .join("desktop.log")
                                .to_string_lossy()
                                .into_owned(),
                        )
                    });
                    runtime.status = BackendStatus::failed(
                        "本地服务启动失败",
                        friendly_startup_error(&error),
                        log_path,
                    );
                    runtime.endpoint = None;
                    (runtime.child.take(), runtime.process_guard.take())
                };
                if let Some(child) = child {
                    terminate_child(child, process_guard);
                }
                let _ = append_desktop_log(self.log_dir(), &format!("startup failed: {error}"));
            }
        }
    }

    fn spawn_and_wait(&self, app: &AppHandle, generation: u64) -> Result<ReadyBackend, String> {
        let executable = resolve_backend_executable(app)?;
        let backend_root = executable
            .parent()
            .ok_or_else(|| "打包后的后端可执行文件没有有效目录".to_owned())?;
        let install_root = env::current_exe()
            .map_err(|error| format!("无法定位桌面客户端安装目录：{error}"))?
            .parent()
            .ok_or_else(|| "桌面客户端没有有效安装目录".to_owned())?
            .to_path_buf();
        verify_backend_bundle(backend_root)?;
        let port = reserve_loopback_port()?;
        let desktop_token = generate_desktop_token()?;
        // This independent capability is exchanged exactly once by the main
        // WebView for an HttpOnly cookie. It is deliberately never stored in
        // BackendStatus, DesktopEndpoint, or any log message.
        let bootstrap_token = generate_desktop_token()?;
        let backend_url = format!("http://{BACKEND_HOST}:{port}/");
        let log_path = create_backend_log(self.log_dir(), generation)?;
        let (child, process_guard) = spawn_backend_process(
            &executable,
            port,
            &desktop_token,
            &bootstrap_token,
            &log_path,
            &install_root,
        )?;

        {
            let mut runtime = self.lock();
            if runtime.generation != generation {
                terminate_child(child, Some(process_guard));
                return Err("启动任务已取消".to_owned());
            }
            runtime.status = BackendStatus::starting(Some(log_path.to_string_lossy().into_owned()));
            runtime.child = Some(child);
            runtime.process_guard = Some(process_guard);
            runtime.endpoint = Some(DesktopEndpoint {
                port,
                token: desktop_token.clone(),
            });
        }

        let deadline = Instant::now() + STARTUP_TIMEOUT;
        while Instant::now() < deadline {
            if !self.is_current_generation(generation) {
                return Err("启动任务已取消".to_owned());
            }

            if let Some(exit_status) = self.child_exit_status(generation)? {
                return Err(format!("本地服务进程提前退出（{exit_status}）"));
            }

            if desktop_ready_is_valid(port, env!("CARGO_PKG_VERSION"), &desktop_token) {
                // 端口预留和后端 bind 之间无法做原子交接。再次检查
                // 合同和子进程，避免误认竞争期间抢占端口的其他服务。
                thread::sleep(Duration::from_millis(150));
                if let Some(exit_status) = self.child_exit_status(generation)? {
                    return Err(format!("本地服务进程提前退出（{exit_status}）"));
                }
                if desktop_ready_is_valid(port, env!("CARGO_PKG_VERSION"), &desktop_token) {
                    return Ok(ReadyBackend {
                        backend_url,
                        bootstrap_token,
                    });
                }
            }

            thread::sleep(HEALTH_POLL_INTERVAL);
        }

        Err(format!(
            "等待本地服务超过 {} 秒，请查看日志后重试",
            STARTUP_TIMEOUT.as_secs()
        ))
    }

    fn monitor_ready_backend(&self, app: &AppHandle, generation: u64) {
        loop {
            thread::sleep(RUNTIME_POLL_INTERVAL);
            if !self.is_current_generation(generation) {
                return;
            }
            match self.child_exit_status(generation) {
                Ok(None) => continue,
                Ok(Some(exit_status)) => {
                    self.handle_runtime_exit(app, generation, exit_status);
                    return;
                }
                Err(error) => {
                    self.handle_runtime_failure(app, generation, &error);
                    return;
                }
            }
        }
    }

    fn handle_runtime_exit(&self, app: &AppHandle, generation: u64, exit_status: ExitStatus) {
        let detail = format!("backend exited unexpectedly: {exit_status}");
        self.handle_runtime_failure(app, generation, &detail);
    }

    fn handle_runtime_failure(&self, app: &AppHandle, generation: u64, technical_detail: &str) {
        let (mut child, _process_guard) = {
            let mut runtime = self.lock();
            if runtime.generation != generation {
                return;
            }
            runtime.status = BackendStatus::failed(
                "本地服务运行中断",
                "本地建模服务意外停止，已保护当前客户端状态。请点击重试重新连接。",
                runtime.status.log_path.clone(),
            );
            runtime.endpoint = None;
            (runtime.child.take(), runtime.process_guard.take())
        };
        if let Some(child) = child.as_mut() {
            let _ = child.wait();
        }
        let _ = append_desktop_log(
            self.log_dir(),
            &format!("runtime backend failure: {technical_detail}"),
        );
        show_recovery_window(app);
    }

    fn is_current_generation(&self, generation: u64) -> bool {
        self.lock().generation == generation
    }

    fn child_exit_status(&self, generation: u64) -> Result<Option<ExitStatus>, String> {
        let mut runtime = self.lock();
        if runtime.generation != generation {
            return Ok(None);
        }
        let Some(child) = runtime.child.as_mut() else {
            return Err("本地服务进程状态丢失".to_owned());
        };
        child
            .try_wait()
            .map_err(|error| format!("无法读取本地服务进程状态：{error}"))
    }

    fn lock(&self) -> MutexGuard<'_, BackendRuntime> {
        self.inner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backend_status_serializes_phase_as_snake_case() {
        let status = BackendStatus::failed("启动失败", "详情", Some("a.log".to_owned()));
        let value = serde_json::to_value(status).expect("status should serialize");
        assert_eq!(value["phase"], "failed");
        assert_eq!(value["message"], "启动失败");
        assert!(value["backend_url"].is_null());
    }

    #[test]
    fn backend_status_never_exposes_bootstrap_capability() {
        let secret = "bootstrap-secret";
        let status = BackendStatus {
            phase: BackendPhase::Ready,
            message: "本地服务已就绪".to_owned(),
            detail: None,
            log_path: None,
            backend_url: Some("http://127.0.0.1:49152/".to_owned()),
        };
        let serialized = serde_json::to_string(&status).expect("status should serialize");
        assert!(!serialized.contains(secret));
    }
}
