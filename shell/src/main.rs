//! Autogenerator для Windows: окно приложения, запуск и надзор за локальным сервером
//! (ARCHITECTURE.md, раздел 4.1). Без аргументов — окно; служебные режимы:
//!
//! * `--self-test [--home ПАПКА] [--report ФАЙЛ]` — проверить установку без окна;
//! * `--register` / `--unregister` — `agen` в PATH и ядро Jupyter (зовёт установщик).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app;
mod http;
mod job;
mod register;
mod selftest;
mod server;
mod settings;

use std::path::PathBuf;

/// Служебные режимы запускаются из терминала или установщика: вывод — в их консоль.
fn attach_console() {
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::System::Console::{AttachConsole, ATTACH_PARENT_PROCESS};
        AttachConsole(ATTACH_PARENT_PROCESS);
    }
}

fn flag(args: &[String], name: &str) -> Option<PathBuf> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1))
        .map(PathBuf::from)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match args.first().map(String::as_str) {
        Some("--self-test") => {
            attach_console();
            selftest::run(flag(&args, "--home"), flag(&args, "--report"))
        }
        Some("--register") => {
            attach_console();
            register::register()
        }
        Some("--unregister") => {
            attach_console();
            register::unregister()
        }
        _ => {
            app::run();
            0
        }
    };
    std::process::exit(code);
}
