//! Windows WebView2 instrumentation used only by the installed-client CI smoke.
//!
//! GitHub's Windows runner may launch the desktop client from an elevated host.
//! WebView2 intentionally ignores browser flags supplied through the ordinary
//! process environment in that case, so the smoke port must be applied through
//! `CoreWebView2EnvironmentOptions::AdditionalBrowserArguments`. Tauri exposes
//! that option through each window config.

#[cfg(any(windows, test))]
const CI_DEBUG_PORT_ENV: &str = "RISK_AGENT_SMOKE_WEBVIEW_DEBUG_PORT";
#[cfg(any(windows, test))]
const DEFAULT_WEBVIEW_ARGUMENTS: &str =
    "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection \
     --autoplay-policy=no-user-gesture-required";

#[cfg(any(windows, test))]
fn ci_debug_arguments(
    raw_port: Option<&str>,
    github_actions: Option<&str>,
    ci: Option<&str>,
    runner_os: Option<&str>,
) -> Option<String> {
    if github_actions != Some("true") || ci != Some("true") || runner_os != Some("Windows") {
        return None;
    }
    let port = raw_port?.parse::<u16>().ok()?;
    if port == 0 {
        return None;
    }
    Some(format!(
        "{DEFAULT_WEBVIEW_ARGUMENTS} --remote-debugging-address=127.0.0.1 \
         --remote-debugging-port={port}"
    ))
}

#[cfg(windows)]
pub(crate) fn configure_ci_webview_probe(context: &mut tauri::Context<tauri::Wry>) {
    let raw_port = std::env::var(CI_DEBUG_PORT_ENV).ok();
    // The port is only configuration for the desktop WebView. Remove it before
    // the supervisor starts Python so model workers never inherit the CI
    // instrumentation switch.
    std::env::remove_var(CI_DEBUG_PORT_ENV);

    let github_actions = std::env::var("GITHUB_ACTIONS").ok();
    let ci = std::env::var("CI").ok();
    let runner_os = std::env::var("RUNNER_OS").ok();
    let Some(arguments) = ci_debug_arguments(
        raw_port.as_deref(),
        github_actions.as_deref(),
        ci.as_deref(),
        runner_os.as_deref(),
    ) else {
        return;
    };

    // WebViews sharing one data directory must use the same browser arguments.
    // Apply the fixed allowlisted string to both splash and main; never pass an
    // arbitrary caller-controlled browser argument through to WebView2.
    for window in &mut context.config_mut().app.windows {
        window.additional_browser_args = Some(arguments.clone());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ci_probe_requires_the_exact_github_windows_context() {
        assert_eq!(CI_DEBUG_PORT_ENV, "RISK_AGENT_SMOKE_WEBVIEW_DEBUG_PORT");
        let enabled =
            ci_debug_arguments(Some("49152"), Some("true"), Some("true"), Some("Windows"))
                .expect("valid GitHub Windows smoke should enable the probe");
        assert!(enabled.contains("--remote-debugging-address=127.0.0.1"));
        assert!(enabled.contains("--remote-debugging-port=49152"));
        assert!(enabled.contains("msSmartScreenProtection"));

        for disabled in [
            ci_debug_arguments(Some("49152"), Some("false"), Some("true"), Some("Windows")),
            ci_debug_arguments(Some("49152"), Some("true"), Some("false"), Some("Windows")),
            ci_debug_arguments(Some("49152"), Some("true"), Some("true"), Some("Linux")),
            ci_debug_arguments(Some("0"), Some("true"), Some("true"), Some("Windows")),
            ci_debug_arguments(
                Some("not-a-port"),
                Some("true"),
                Some("true"),
                Some("Windows"),
            ),
        ] {
            assert!(disabled.is_none());
        }
    }

    #[test]
    fn ci_probe_never_forwards_arbitrary_browser_arguments() {
        assert!(ci_debug_arguments(
            Some("49152 --disable-web-security"),
            Some("true"),
            Some("true"),
            Some("Windows"),
        )
        .is_none());
    }
}
