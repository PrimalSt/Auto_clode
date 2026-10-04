// Права на команды оболочки: окно зовёт их и со стартовой страницы, и со страниц сервера
// (capabilities/default.json).
const COMMANDS: &[&str] = &[
    "pick_files",
    "pick_folder",
    "open_path",
    "shell_state",
    "shell_settings",
    "set_home",
    "set_dev_source",
    "restart_server",
    "use_last_good",
    "open_logs",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("tauri-build");
}
