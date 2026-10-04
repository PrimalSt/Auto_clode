//! `--register` / `--unregister`: установщик зовёт их после установки и перед удалением.
//!
//! * `<папка установки>\bin` — в PATH пользователя: команда `agen` работает в любом терминале
//!   (там `agen.cmd`, который запускает Python приложения).
//! * Ядро Jupyter «Autogenerator»: блокноты запускаются тем же Python, что и приложение, и видят
//!   его модули (`from autogenerator.api import run`).

use crate::server::install_dir;
use std::path::{Path, PathBuf};

const KERNEL: &str = "autogenerator";

/// Добавить папку в список PATH (`;`-разделённый), если её там ещё нет.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn path_with(list: &str, dir: &str) -> Option<String> {
    if entries(list).any(|e| same(e, dir)) {
        return None;
    }
    let base = list.trim_end_matches(';');
    Some(if base.is_empty() {
        dir.to_string()
    } else {
        format!("{base};{dir}")
    })
}

/// Убрать папку из списка PATH; None — её там не было.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn path_without(list: &str, dir: &str) -> Option<String> {
    if !entries(list).any(|e| same(e, dir)) {
        return None;
    }
    Some(entries(list).filter(|e| !same(e, dir)).collect::<Vec<_>>().join(";"))
}

#[cfg_attr(not(windows), allow(dead_code))]
fn entries(list: &str) -> impl Iterator<Item = &str> {
    list.split(';').map(str::trim).filter(|e| !e.is_empty())
}

#[cfg_attr(not(windows), allow(dead_code))]
fn same(a: &str, b: &str) -> bool {
    let norm = |s: &str| s.trim().trim_end_matches(['\\', '/']).to_lowercase();
    norm(a) == norm(b)
}

/// Описание ядра Jupyter для Python приложения.
pub fn kernel_spec(python: &Path) -> serde_json::Value {
    serde_json::json!({
        "argv": [python.display().to_string(), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "Autogenerator",
        "language": "python",
        "env": {"PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1"},
        "metadata": {"debugger": true}
    })
}

fn kernels_dir() -> Option<PathBuf> {
    if cfg!(windows) {
        std::env::var_os("APPDATA").map(|p| PathBuf::from(p).join("jupyter").join("kernels"))
    } else {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share/jupyter/kernels"))
    }
}

fn bundled_python(dir: &Path) -> PathBuf {
    dir.join("python")
        .join(if cfg!(windows) { "python.exe" } else { "bin/python3" })
}

fn has_ipykernel(dir: &Path) -> bool {
    let lib = if cfg!(windows) {
        dir.join("python").join("Lib")
    } else {
        dir.join("python").join("lib")
    };
    site_packages(&lib).is_some_and(|sp| sp.join("ipykernel").is_dir())
}

fn site_packages(lib: &Path) -> Option<PathBuf> {
    let direct = lib.join("site-packages");
    if direct.is_dir() {
        return Some(direct);
    }
    // Linux: lib/python3.12/site-packages
    std::fs::read_dir(lib)
        .ok()?
        .flatten()
        .map(|e| e.path().join("site-packages"))
        .find(|p| p.is_dir())
}

pub fn register() -> i32 {
    let dir = install_dir();
    let mut code = 0;
    let bin = dir.join("bin");
    if cfg!(windows) && bin.is_dir() {
        match env_path::add(&bin.display().to_string()) {
            Ok(true) => println!("PATH пользователя: добавлена {}", bin.display()),
            Ok(false) => println!("PATH пользователя: {} уже есть", bin.display()),
            Err(e) => {
                eprintln!("PATH не изменён: {e}");
                code = 1;
            }
        }
    }
    if has_ipykernel(&dir) {
        if let Some(k) = kernels_dir() {
            let target = k.join(KERNEL);
            let spec = serde_json::to_string_pretty(&kernel_spec(&bundled_python(&dir))).unwrap_or_default();
            match std::fs::create_dir_all(&target).and_then(|_| std::fs::write(target.join("kernel.json"), spec)) {
                Ok(()) => println!("Ядро Jupyter: {}", target.display()),
                Err(e) => {
                    eprintln!("Ядро Jupyter не записано: {e}");
                    code = 1;
                }
            }
        }
    }
    code
}

pub fn unregister() -> i32 {
    let dir = install_dir();
    let bin = dir.join("bin");
    if let Err(e) = env_path::remove(&bin.display().to_string()) {
        eprintln!("PATH не изменён: {e}");
    }
    if let Some(k) = kernels_dir() {
        let target = k.join(KERNEL);
        // убрать только своё ядро: то, что указывает на Python этой установки
        let ours = std::fs::read_to_string(target.join("kernel.json")).is_ok_and(|t| {
            t.contains(&serde_json::to_string(&bundled_python(&dir).display().to_string()).unwrap_or_default())
        });
        if ours {
            let _ = std::fs::remove_dir_all(&target);
        }
    }
    0
}

#[cfg(windows)]
mod env_path {
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS, LPARAM};
    use windows_sys::Win32::System::Registry::{
        RegGetValueW, RegSetKeyValueW, HKEY_CURRENT_USER, REG_EXPAND_SZ, RRF_NOEXPAND, RRF_RT_REG_EXPAND_SZ,
        RRF_RT_REG_SZ,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SendMessageTimeoutW, HWND_BROADCAST, SMTO_ABORTIFHUNG, WM_SETTINGCHANGE,
    };

    fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn read() -> Result<String, String> {
        let (key, name) = (wide("Environment"), wide("Path"));
        let flags = RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | RRF_NOEXPAND;
        let mut size: u32 = 0;
        let rc = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                flags,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut size,
            )
        };
        if rc == ERROR_FILE_NOT_FOUND {
            return Ok(String::new());
        }
        if rc != ERROR_SUCCESS {
            return Err(format!("RegGetValue: {rc}"));
        }
        let mut buf = vec![0u16; (size as usize).div_ceil(2) + 1];
        let mut size = (buf.len() * 2) as u32;
        let rc = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                flags,
                std::ptr::null_mut(),
                buf.as_mut_ptr().cast(),
                &mut size,
            )
        };
        if rc != ERROR_SUCCESS {
            return Err(format!("RegGetValue: {rc}"));
        }
        let len = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
        Ok(String::from_utf16_lossy(&buf[..len]))
    }

    fn write(value: &str) -> Result<(), String> {
        let (key, name, data) = (wide("Environment"), wide("Path"), wide(value));
        let rc = unsafe {
            RegSetKeyValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                REG_EXPAND_SZ,
                data.as_ptr().cast(),
                (data.len() * 2) as u32,
            )
        };
        if rc != ERROR_SUCCESS {
            return Err(format!("RegSetKeyValue: {rc}"));
        }
        // открытые программы (проводник, новые терминалы) перечитывают переменные среды
        let env = wide("Environment");
        unsafe {
            SendMessageTimeoutW(
                HWND_BROADCAST,
                WM_SETTINGCHANGE,
                0,
                env.as_ptr() as LPARAM,
                SMTO_ABORTIFHUNG,
                5000,
                std::ptr::null_mut(),
            )
        };
        Ok(())
    }

    pub fn add(dir: &str) -> Result<bool, String> {
        match super::path_with(&read()?, dir) {
            Some(v) => write(&v).map(|_| true),
            None => Ok(false),
        }
    }

    pub fn remove(dir: &str) -> Result<bool, String> {
        match super::path_without(&read()?, dir) {
            Some(v) => write(&v).map(|_| true),
            None => Ok(false),
        }
    }
}

#[cfg(not(windows))]
mod env_path {
    /// Вне Windows PATH не трогаем: `agen` доступен через `uv run agen` в копии исходников.
    pub fn add(_dir: &str) -> Result<bool, String> {
        Ok(false)
    }

    pub fn remove(_dir: &str) -> Result<bool, String> {
        Ok(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_entries_are_added_once_and_removed_case_insensitively() {
        let dir = r"C:\Users\God\AppData\Local\Autogenerator\bin";
        assert_eq!(path_with("", dir).as_deref(), Some(dir));
        let list = path_with(r"C:\Tools;", dir).unwrap();
        assert_eq!(list, format!(r"C:\Tools;{dir}"));
        assert_eq!(path_with(&list.to_uppercase(), &format!(r"{dir}\")), None);
        assert_eq!(path_without(&list, &dir.to_lowercase()).as_deref(), Some(r"C:\Tools"));
        assert_eq!(path_without(r"C:\Tools", dir), None);
    }

    #[test]
    fn kernel_runs_the_app_python() {
        let spec = kernel_spec(Path::new(r"C:\App\python\python.exe"));
        assert_eq!(spec["argv"][0], r"C:\App\python\python.exe");
        assert_eq!(spec["argv"][2], "ipykernel_launcher");
    }
}
