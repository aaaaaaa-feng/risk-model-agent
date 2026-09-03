use std::path::{Path, PathBuf};

pub(crate) const MANIFEST_ENV: &str = "RISK_AGENT_BACKEND_MANIFEST";

pub(crate) fn select_manifest(
    profile: &str,
    explicit: Option<&str>,
    development_default: &Path,
) -> Result<Option<PathBuf>, String> {
    if let Some(value) = explicit.map(str::trim).filter(|value| !value.is_empty()) {
        let path = PathBuf::from(value);
        if !path.is_absolute() {
            return Err(format!("{MANIFEST_ENV} 必须是绝对路径：{value}"));
        }
        validate_regular_manifest(&path)?;
        return Ok(Some(path));
    }

    if profile == "release" {
        return Err(format!(
            "正式构建必须设置 {MANIFEST_ENV}，并指向冻结后端的 backend-manifest.json"
        ));
    }

    if development_default.exists() {
        validate_regular_manifest(development_default)?;
        return Ok(Some(development_default.to_path_buf()));
    }
    Ok(None)
}

fn validate_regular_manifest(path: &Path) -> Result<(), String> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|error| format!("后端完整性清单不可用 {}：{error}", path.display()))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!("后端完整性清单必须是常规文件：{}", path.display()));
    }
    if path.file_name().and_then(|name| name.to_str()) != Some("backend-manifest.json") {
        return Err(format!(
            "{MANIFEST_ENV} 必须指向 backend-manifest.json：{}",
            path.display()
        ));
    }
    Ok(())
}
