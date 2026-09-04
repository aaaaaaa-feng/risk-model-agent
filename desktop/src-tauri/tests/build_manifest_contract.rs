#[path = "../build_manifest.rs"]
mod build_manifest;

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use build_manifest::{select_manifest, MANIFEST_ENV};

static TEST_DIRECTORY_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn new() -> Self {
        let sequence = TEST_DIRECTORY_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "risk-agent-build-manifest-test-{}-{sequence}",
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

#[test]
fn debug_checks_allow_a_missing_frozen_backend() {
    let directory = TestDirectory::new();
    let missing = directory.path().join("backend-manifest.json");
    assert_eq!(
        select_manifest("debug", None, &missing).expect("debug selection"),
        None
    );
}

#[test]
fn release_build_requires_an_explicit_manifest() {
    let directory = TestDirectory::new();
    let default = directory.path().join("backend-manifest.json");
    fs::write(&default, b"{}").expect("write default manifest");

    let error = select_manifest("release", None, &default).expect_err("release must fail closed");
    assert!(error.contains(MANIFEST_ENV));
}

#[test]
fn release_build_accepts_an_explicit_regular_manifest() {
    let directory = TestDirectory::new();
    let manifest = directory.path().join("backend-manifest.json");
    fs::write(&manifest, b"{}").expect("write manifest");

    let selected = select_manifest(
        "release",
        Some(manifest.to_str().expect("utf-8 path")),
        &directory.path().join("missing.json"),
    )
    .expect("release selection")
    .expect("selected manifest");
    assert_eq!(selected, manifest);
}

#[test]
fn explicit_manifest_path_must_be_absolute_and_correctly_named() {
    let directory = TestDirectory::new();
    let wrong_name = directory.path().join("manifest.json");
    fs::write(&wrong_name, b"{}").expect("write manifest");

    assert!(
        select_manifest("release", Some("backend-manifest.json"), &wrong_name)
            .expect_err("relative path must fail")
            .contains("绝对路径")
    );
    assert!(select_manifest(
        "release",
        Some(wrong_name.to_str().expect("utf-8 path")),
        &wrong_name,
    )
    .expect_err("wrong filename must fail")
    .contains("backend-manifest.json"));
}
