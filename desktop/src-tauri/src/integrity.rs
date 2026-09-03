use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::Read,
    path::{Component, Path, PathBuf},
};

use serde::Deserialize;
use sha2::{Digest, Sha256};

pub(crate) const MANIFEST_FILENAME: &str = "backend-manifest.json";
const MANIFEST_SCHEMA: &str = "risk-model-agent/backend-manifest/v1";
const MAX_MANIFEST_BYTES: u64 = 16 * 1024 * 1024;
const HASH_BUFFER_BYTES: usize = 1024 * 1024;
const EMBEDDED_MANIFEST_SHA256: &str = env!("RISK_AGENT_BACKEND_MANIFEST_SHA256");

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BackendManifest {
    schema_version: String,
    application_version: String,
    files: Vec<ManifestEntry>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ManifestEntry {
    path: String,
    size: u64,
    sha256: String,
}

pub(crate) fn verify_backend_bundle(root: &Path) -> Result<(), String> {
    verify_backend_bundle_with_expected(root, EMBEDDED_MANIFEST_SHA256, env!("CARGO_PKG_VERSION"))
        .map_err(|detail| format!("BACKEND_INTEGRITY: {detail}"))
}

fn verify_backend_bundle_with_expected(
    root: &Path,
    expected_manifest_sha256: &str,
    expected_application_version: &str,
) -> Result<(), String> {
    validate_sha256(expected_manifest_sha256, "客户端固化的清单摘要")?;
    let root_metadata = fs::symlink_metadata(root)
        .map_err(|error| format!("无法读取后端根目录 {}: {error}", root.display()))?;
    if root_metadata.file_type().is_symlink() || !root_metadata.is_dir() {
        return Err(format!("后端根路径必须是常规目录：{}", root.display()));
    }
    let canonical_root = root
        .canonicalize()
        .map_err(|error| format!("无法解析后端根目录 {}: {error}", root.display()))?;
    if !canonical_root.is_dir() {
        return Err(format!("后端根路径不是目录：{}", root.display()));
    }

    let manifest_path = canonical_root.join(MANIFEST_FILENAME);
    let manifest_metadata = fs::symlink_metadata(&manifest_path)
        .map_err(|error| format!("无法读取后端完整性清单：{error}"))?;
    if manifest_metadata.file_type().is_symlink() || !manifest_metadata.is_file() {
        return Err("后端完整性清单必须是常规文件".to_owned());
    }
    if manifest_metadata.len() > MAX_MANIFEST_BYTES {
        return Err(format!(
            "后端完整性清单超过 {} 字节上限",
            MAX_MANIFEST_BYTES
        ));
    }

    let manifest_bytes =
        fs::read(&manifest_path).map_err(|error| format!("无法读取后端完整性清单：{error}"))?;
    let manifest_sha256 = sha256_bytes(&manifest_bytes);
    if manifest_sha256 != expected_manifest_sha256 {
        return Err(format!(
            "后端完整性清单摘要不匹配：expected={expected_manifest_sha256}, actual={manifest_sha256}"
        ));
    }

    let manifest: BackendManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|error| format!("后端完整性清单 JSON 无效：{error}"))?;
    if manifest.schema_version != MANIFEST_SCHEMA {
        return Err(format!("后端清单版本不支持：{}", manifest.schema_version));
    }
    if manifest.application_version != expected_application_version {
        return Err(format!(
            "后端版本与客户端不匹配：expected={expected_application_version}, actual={}",
            manifest.application_version
        ));
    }
    if manifest.files.is_empty() {
        return Err("后端完整性清单没有文件".to_owned());
    }

    let mut expected_files = BTreeMap::new();
    let mut casefolded_paths = BTreeSet::new();
    for entry in &manifest.files {
        validate_manifest_path(&entry.path)?;
        validate_sha256(&entry.sha256, &format!("文件 {} 的 SHA-256", entry.path))?;
        if expected_files.insert(entry.path.clone(), entry).is_some() {
            return Err(format!("后端清单存在重复路径：{}", entry.path));
        }
        if !casefolded_paths.insert(entry.path.to_lowercase()) {
            return Err(format!("后端清单存在大小写冲突路径：{}", entry.path));
        }
    }

    let actual_files = collect_regular_files(&canonical_root)?;
    let expected_paths = expected_files.keys().cloned().collect::<BTreeSet<_>>();
    if actual_files != expected_paths {
        let missing = expected_paths.difference(&actual_files).next();
        let extra = actual_files.difference(&expected_paths).next();
        return Err(format!(
            "后端文件集合不匹配：missing={missing:?}, extra={extra:?}"
        ));
    }

    for (relative, entry) in expected_files {
        let path = canonical_root.join(relative_path(&relative)?);
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| format!("无法读取后端文件 {relative}: {error}"))?;
        if !metadata.is_file() && !metadata.file_type().is_symlink() {
            return Err(format!("后端资源不是常规文件：{relative}"));
        }
        let canonical_path = path
            .canonicalize()
            .map_err(|error| format!("无法解析后端文件 {relative}: {error}"))?;
        if !canonical_path.starts_with(&canonical_root) {
            return Err(format!("后端文件越出资源目录：{relative}"));
        }
        let target_metadata = fs::metadata(&canonical_path)
            .map_err(|error| format!("无法读取后端文件目标 {relative}: {error}"))?;
        if !target_metadata.is_file() {
            return Err(format!("后端资源目标不是常规文件：{relative}"));
        }
        if target_metadata.len() != entry.size {
            return Err(format!(
                "后端文件大小不匹配 {relative}: expected={}, actual={}",
                entry.size,
                target_metadata.len()
            ));
        }
        let actual_sha256 = sha256_file(&canonical_path)?;
        if actual_sha256 != entry.sha256 {
            return Err(format!(
                "后端文件摘要不匹配 {relative}: expected={}, actual={actual_sha256}",
                entry.sha256
            ));
        }
    }
    let final_files = collect_regular_files(&canonical_root)?;
    if final_files != expected_paths {
        return Err("校验期间后端文件集合发生变化".to_owned());
    }
    Ok(())
}

fn validate_manifest_path(relative: &str) -> Result<(), String> {
    if relative.is_empty()
        || relative == MANIFEST_FILENAME
        || relative.starts_with('/')
        || relative.contains('\\')
        || relative.contains('\0')
    {
        return Err(format!("后端清单路径无效：{relative:?}"));
    }
    let mut count = 0_usize;
    for segment in relative.split('/') {
        count += 1;
        if segment.is_empty() || segment == "." || segment == ".." || segment.contains(':') {
            return Err(format!("后端清单路径无效：{relative:?}"));
        }
    }
    if count == 0 {
        return Err(format!("后端清单路径无效：{relative:?}"));
    }
    Ok(())
}

fn validate_sha256(value: &str, label: &str) -> Result<(), String> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(format!("{label} 不是小写十六进制 SHA-256"));
    }
    Ok(())
}

fn relative_path(relative: &str) -> Result<PathBuf, String> {
    validate_manifest_path(relative)?;
    let mut path = PathBuf::new();
    for segment in relative.split('/') {
        path.push(segment);
    }
    Ok(path)
}

fn collect_regular_files(root: &Path) -> Result<BTreeSet<String>, String> {
    let mut files = BTreeSet::new();
    collect_directory(root, root, &mut files)?;
    Ok(files)
}

fn collect_directory(
    root: &Path,
    current: &Path,
    files: &mut BTreeSet<String>,
) -> Result<(), String> {
    let entries = fs::read_dir(current)
        .map_err(|error| format!("无法扫描后端目录 {}: {error}", current.display()))?;
    for entry in entries {
        let entry = entry.map_err(|error| format!("无法读取后端目录项：{error}"))?;
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| format!("无法读取后端资源 {}: {error}", path.display()))?;
        if metadata.file_type().is_symlink() {
            let canonical_path = path
                .canonicalize()
                .map_err(|error| format!("无法解析后端文件链接 {}: {error}", path.display()))?;
            if !canonical_path.starts_with(root) {
                return Err(format!("后端文件链接越出资源目录：{}", path.display()));
            }
            let target_metadata = fs::metadata(&canonical_path)
                .map_err(|error| format!("无法读取后端文件链接目标 {}: {error}", path.display()))?;
            if !target_metadata.is_file() {
                return Err(format!(
                    "后端仅允许指向目录内常规文件的链接：{}",
                    path.display()
                ));
            }
            let relative = path
                .strip_prefix(root)
                .map_err(|error| format!("无法计算后端相对路径：{error}"))?;
            let relative = path_to_posix(relative)?;
            if relative != MANIFEST_FILENAME {
                files.insert(relative);
            }
            continue;
        }
        if metadata.is_dir() {
            collect_directory(root, &path, files)?;
            continue;
        }
        if !metadata.is_file() {
            return Err(format!("后端资源必须是常规文件：{}", path.display()));
        }
        let relative = path
            .strip_prefix(root)
            .map_err(|error| format!("无法计算后端相对路径：{error}"))?;
        let relative = path_to_posix(relative)?;
        if relative != MANIFEST_FILENAME {
            files.insert(relative);
        }
    }
    Ok(())
}

fn path_to_posix(path: &Path) -> Result<String, String> {
    let mut parts = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => parts.push(
                value
                    .to_str()
                    .ok_or_else(|| "后端路径不是有效 UTF-8".to_owned())?,
            ),
            _ => return Err(format!("后端相对路径包含非法组件：{}", path.display())),
        }
    }
    Ok(parts.join("/"))
}

fn sha256_bytes(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file = File::open(path)
        .map_err(|error| format!("无法打开后端文件 {}: {error}", path.display()))?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; HASH_BUFFER_BYTES];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|error| format!("无法读取后端文件 {}: {error}", path.display()))?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_DIRECTORY_SEQUENCE: AtomicU64 = AtomicU64::new(0);

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new() -> Self {
            let sequence = TEST_DIRECTORY_SEQUENCE.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "risk-agent-integrity-test-{}-{sequence}",
                std::process::id()
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir(&path).expect("create temp directory");
            Self(path)
        }

        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn write_test_manifest(root: &Path, files: &[(&str, &[u8])]) -> (Vec<u8>, String) {
        let mut entries = Vec::new();
        for (relative, payload) in files {
            let path = root.join(relative_path(relative).expect("valid test path"));
            fs::create_dir_all(path.parent().expect("test file parent")).expect("create parent");
            fs::write(&path, payload).expect("write test file");
            entries.push(json!({
                "path": relative,
                "size": payload.len(),
                "sha256": sha256_bytes(payload),
            }));
        }
        entries.sort_by_key(|entry| entry["path"].as_str().unwrap_or_default().to_owned());
        let bytes = serde_json::to_vec_pretty(&json!({
            "schema_version": MANIFEST_SCHEMA,
            "application_version": "1.2.0",
            "files": entries,
        }))
        .expect("serialize manifest");
        fs::write(root.join(MANIFEST_FILENAME), &bytes).expect("write manifest");
        let digest = sha256_bytes(&bytes);
        (bytes, digest)
    }

    #[test]
    fn accepts_exact_bundle() {
        let directory = TestDirectory::new();
        let (_, digest) = write_test_manifest(
            directory.path(),
            &[
                ("risk-model-agent.exe", b"binary"),
                ("_internal/settings.json", b"{}"),
            ],
        );
        verify_backend_bundle_with_expected(directory.path(), &digest, "1.2.0")
            .expect("exact bundle should pass");
    }

    #[test]
    fn rejects_tampered_or_extra_file() {
        let directory = TestDirectory::new();
        let (_, digest) =
            write_test_manifest(directory.path(), &[("risk-model-agent.exe", b"binary")]);
        fs::write(directory.path().join("risk-model-agent.exe"), b"change")
            .expect("tamper test file");
        let error = verify_backend_bundle_with_expected(directory.path(), &digest, "1.2.0")
            .expect_err("tampered bundle must fail");
        assert!(error.contains("大小不匹配") || error.contains("摘要不匹配"));

        fs::write(directory.path().join("risk-model-agent.exe"), b"binary")
            .expect("restore test file");
        fs::write(directory.path().join("unexpected.dll"), b"extra").expect("write extra file");
        let error = verify_backend_bundle_with_expected(directory.path(), &digest, "1.2.0")
            .expect_err("extra file must fail");
        assert!(error.contains("文件集合不匹配"));
    }

    #[test]
    fn rejects_traversal_even_with_matching_manifest_digest() {
        let directory = TestDirectory::new();
        let bytes = serde_json::to_vec_pretty(&json!({
            "schema_version": MANIFEST_SCHEMA,
            "application_version": "1.2.0",
            "files": [{"path": "../escape", "size": 1, "sha256": "0".repeat(64)}],
        }))
        .expect("serialize malicious manifest");
        fs::write(directory.path().join(MANIFEST_FILENAME), &bytes).expect("write manifest");
        let error =
            verify_backend_bundle_with_expected(directory.path(), &sha256_bytes(&bytes), "1.2.0")
                .expect_err("traversal must fail");
        assert!(error.contains("路径无效"));
    }
}
