mod backend;
mod ci_webview;
mod commands;
mod integrity;

use backend::BackendSupervisor;
use commands::{backend_status, open_log_directory, retry_backend};
use tauri::{Manager, RunEvent, WindowEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    #[cfg(desktop)]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
        let supervisor = app.state::<BackendSupervisor>();
        if supervisor.is_restartable() {
            if let Some(splash) = app.get_webview_window("splash") {
                let _ = splash.show();
                let _ = splash.unminimize();
                let _ = splash.set_focus();
            }
            let _ = supervisor.launch(app.clone());
            return;
        }
        if let Some(main) = app.get_webview_window("main") {
            if main.is_visible().unwrap_or(false) {
                let _ = main.unminimize();
                let _ = main.show();
                let _ = main.set_focus();
                return;
            }
        }
        if let Some(splash) = app.get_webview_window("splash") {
            let _ = splash.show();
            let _ = splash.set_focus();
        }
    }));

    let context = tauri::generate_context!();
    #[cfg(windows)]
    let context = {
        let mut context = context;
        ci_webview::configure_ci_webview_probe(&mut context);
        context
    };

    let app = builder
        .setup(|app| {
            let log_dir = app.path().app_log_dir().unwrap_or_else(|_| {
                std::env::temp_dir()
                    .join("RiskModelAgent")
                    .join("desktop-logs")
            });
            let supervisor = BackendSupervisor::new(log_dir);
            app.manage(supervisor.clone());
            // 可预期的后端/日志/线程错误由 supervisor 转为启动页中文状态，
            // 不能通过 `?` 让 Windows GUI 进程无提示退出。
            let _ = supervisor.launch(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_status,
            retry_backend,
            open_log_directory
        ])
        .build(context)
        .expect("风控建模 Agent 客户端初始化失败");

    app.run(|app_handle, event| match event {
        RunEvent::WindowEvent {
            label,
            event: WindowEvent::CloseRequested { api, .. },
            ..
        } if label == "main" || label == "splash" => {
            // splash 会在主界面期间保持隐藏，用于后端崩溃后的恢复页。
            // 因此任一用户可见窗口的关闭都必须显式退出整个应用，不能只销毁
            // 单个窗口而留下隐藏 splash 和后端进程。
            api.prevent_close();
            app_handle.exit(0);
        }
        RunEvent::Exit => app_handle.state::<BackendSupervisor>().stop(),
        _ => {}
    });
}
