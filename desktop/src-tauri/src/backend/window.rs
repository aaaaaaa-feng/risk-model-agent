use tauri::{AppHandle, LogicalSize, Manager};

pub(super) fn show_main_window(
    app: &AppHandle,
    backend_url: &str,
    bootstrap_token: &str,
) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "客户端主窗口未初始化".to_owned())?;
    let url = desktop_bootstrap_url(backend_url, bootstrap_token)?;
    if let Ok(Some(monitor)) = main.current_monitor() {
        let scale = monitor.scale_factor();
        let size = monitor.size();
        let (width, height) = fitted_main_window_size(
            f64::from(size.width) / scale,
            f64::from(size.height) / scale,
        );
        main.set_size(LogicalSize::new(width, height))
            .map_err(|error| format!("无法适配当前屏幕尺寸：{error}"))?;
        main.center()
            .map_err(|error| format!("无法居中客户端主窗口：{error}"))?;
    }
    main.navigate(url)
        // A platform navigation error could include the URL. Do not attach the
        // raw error because that first URL carries the one-use capability.
        .map_err(|_| "无法在客户端内初始化本地会话".to_owned())?;
    main.show()
        .map_err(|error| format!("无法显示客户端主窗口：{error}"))?;
    main.set_focus()
        .map_err(|error| format!("无法聚焦客户端主窗口：{error}"))?;

    if let Some(splash) = app.get_webview_window("splash") {
        splash
            .hide()
            .map_err(|error| format!("无法隐藏启动页：{error}"))?;
    }
    Ok(())
}

fn desktop_bootstrap_url(backend_url: &str, bootstrap_token: &str) -> Result<tauri::Url, String> {
    let mut url = backend_url
        .parse::<tauri::Url>()
        .map_err(|error| format!("本地服务地址无效：{error}"))?;
    url.set_path("/api/v1/desktop/bootstrap");
    url.set_query(None);
    url.query_pairs_mut().append_pair("token", bootstrap_token);
    Ok(url)
}

fn fitted_main_window_size(available_width: f64, available_height: f64) -> (f64, f64) {
    const DESIRED_WIDTH: f64 = 1280.0;
    const DESIRED_HEIGHT: f64 = 800.0;
    // 低于 820px 时 React 工作台已经切换到单栏响应式布局。这里的硬下限
    // 必须低于常见 1366x768 笔记本在 150%/175% 缩放后的逻辑工作区，
    // 否则操作系统会强制窗口超出屏幕，底部对话和弹窗操作区无法触达。
    const MINIMUM_WIDTH: f64 = 680.0;
    const MINIMUM_HEIGHT: f64 = 360.0;
    const HORIZONTAL_MARGIN: f64 = 24.0;
    const VERTICAL_MARGIN: f64 = 48.0;

    let width = (available_width - HORIZONTAL_MARGIN).clamp(MINIMUM_WIDTH, DESIRED_WIDTH);
    let height = (available_height - VERTICAL_MARGIN).clamp(MINIMUM_HEIGHT, DESIRED_HEIGHT);
    (width, height)
}

pub(super) fn show_recovery_window(app: &AppHandle) {
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.hide();
    }
    if let Some(splash) = app.get_webview_window("splash") {
        let _ = splash.show();
        let _ = splash.unminimize();
        let _ = splash.set_focus();
    }
}

pub(super) fn friendly_startup_error(error: &str) -> String {
    if error.starts_with("BACKEND_INTEGRITY:") {
        return "客户端文件校验失败，请重新下载安装。技术原因已写入本机日志。".to_owned();
    }
    if error.contains("缺少本地建模服务") {
        return "客户端安装不完整，请重新下载安装。技术原因已写入本机日志。".to_owned();
    }
    if error.contains("提前退出") {
        return "本地服务未能正常启动，请打开日志目录查看原因后重试。".to_owned();
    }
    if error.contains("超过") {
        return "本地服务启动超时，可能是电脑资源紧张或安全软件拦截；请稍后重试。".to_owned();
    }
    "客户端未能启动本地建模服务，请打开日志目录查看原因后重试。".to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn main_webview_bootstraps_with_a_one_use_url() {
        let secret = "bootstrap-secret";
        let url = desktop_bootstrap_url("http://127.0.0.1:49152/", secret)
            .expect("bootstrap URL should be valid");
        assert_eq!(
            url.as_str(),
            "http://127.0.0.1:49152/api/v1/desktop/bootstrap?token=bootstrap-secret"
        );
    }

    #[test]
    fn main_window_fits_a_common_scaled_laptop_display() {
        for scale in [1.25, 1.5, 1.75] {
            let available_width = 1366.0 / scale;
            let available_height = 768.0 / scale;
            let (width, height) = fitted_main_window_size(available_width, available_height);
            assert!(width <= available_width);
            assert!(height <= available_height);
        }

        assert_eq!(
            fitted_main_window_size(1366.0 / 1.5, 768.0 / 1.5),
            (886.6666666666666, 464.0)
        );
    }

    #[test]
    fn integrity_failures_get_a_safe_user_message() {
        let message = friendly_startup_error("BACKEND_INTEGRITY: secret technical detail");
        assert_eq!(
            message,
            "客户端文件校验失败，请重新下载安装。技术原因已写入本机日志。"
        );
        assert!(!message.contains("secret technical detail"));
    }
}
