use tauri::{AppHandle, State};

use crate::backend::{open_directory, BackendStatus, BackendSupervisor};

#[tauri::command]
pub(crate) fn backend_status(supervisor: State<'_, BackendSupervisor>) -> BackendStatus {
    supervisor.status()
}

#[tauri::command]
pub(crate) fn retry_backend(
    app: AppHandle,
    supervisor: State<'_, BackendSupervisor>,
) -> Result<BackendStatus, String> {
    supervisor.launch(app)
}

#[tauri::command]
pub(crate) fn open_log_directory(supervisor: State<'_, BackendSupervisor>) -> Result<(), String> {
    open_directory(supervisor.log_dir())
}
