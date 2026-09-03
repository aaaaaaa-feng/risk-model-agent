use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub(super) fn create_backend_log(log_dir: &Path, generation: u64) -> Result<PathBuf, String> {
    fs::create_dir_all(log_dir).map_err(|error| format!("无法创建客户端日志目录：{error}"))?;
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("无法生成日志时间戳：{error}"))?
        .as_secs();
    Ok(log_dir.join(format!("backend-{timestamp}-{generation}.log")))
}

pub(super) fn append_desktop_log(log_dir: &Path, line: &str) -> Result<(), std::io::Error> {
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_dir.join("desktop.log"))?;
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default();
    writeln!(file, "[{timestamp}] {line}")
}
