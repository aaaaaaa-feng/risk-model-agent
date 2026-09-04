use std::{env, fs, path::PathBuf};

use sha2::{Digest, Sha256};

mod build_manifest;

use build_manifest::{select_manifest, MANIFEST_ENV};

const COMMANDS: &[&str] = &["backend_status", "retry_backend", "open_log_directory"];
const BACKEND_MANIFEST: &str = "../../dist/risk-model-agent/backend-manifest.json";
const DEVELOPMENT_DIGEST: &str = "0000000000000000000000000000000000000000000000000000000000000000";

fn main() {
    let manifest_path =
        PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"))
            .join(BACKEND_MANIFEST);
    println!("cargo:rerun-if-env-changed={MANIFEST_ENV}");
    println!("cargo:rerun-if-changed={}", manifest_path.display());
    let profile = env::var("PROFILE").expect("PROFILE");
    let explicit = env::var(MANIFEST_ENV).ok();
    let selected = select_manifest(&profile, explicit.as_deref(), &manifest_path)
        .unwrap_or_else(|error| panic!("{error}。请先运行 scripts/create_backend_manifest.py"));
    let digest = if let Some(selected) = selected {
        println!("cargo:rerun-if-changed={}", selected.display());
        let manifest = fs::read(&selected)
            .unwrap_or_else(|error| panic!("后端完整性清单不可用 {}：{error}", selected.display()));
        format!("{:x}", Sha256::digest(&manifest))
    } else {
        println!(
            "cargo:warning=未找到冻结后端完整性清单；当前仅编译开发/检查版本，运行时会拒绝启动未校验后端"
        );
        DEVELOPMENT_DIGEST.to_owned()
    };
    println!("cargo:rustc-env=RISK_AGENT_BACKEND_MANIFEST_SHA256={digest}");

    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("无法生成 Tauri 桌面客户端构建信息");
}
