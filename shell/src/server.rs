//! Запуск локального сервера (`python -m autogenerator.server`) и его процесс.
//!
//! Сервер печатает `AGEN_SERVER_READY {"port": …}`, когда принимает соединения, или
//! `AGEN_SERVER_ERROR {"code": …, "message": …}`, если запуститься не может (например, папка данных
//! уже открыта). Вывод сервера читается всё время его работы: последние строки нужны безопасному
//! режиму, а непрочитанный канал остановил бы сервер.

use crate::http;
use crate::job::Job;
use crate::settings::{self, Launch};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const READY: &str = "AGEN_SERVER_READY";
const ERROR: &str = "AGEN_SERVER_ERROR";
const LOG_LINES: usize = 200;
const STOP_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug)]
pub enum StartError {
    /// Сервер сам сказал, почему не запустился: повтор с теми же настройками не поможет.
    Reported {
        code: String,
        message: String,
        hint: Option<String>,
    },
    /// Python не найден, процесс не запустился, упал или не ответил вовремя.
    Failed { message: String, log: String },
}

impl StartError {
    pub fn message(&self) -> String {
        match self {
            StartError::Reported { message, .. } | StartError::Failed { message, .. } => message.clone(),
        }
    }
}

/// Каким Python запускать сервер.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Python {
    pub exe: PathBuf,
    /// Python из установщика (а не из `.venv` копии исходников).
    pub bundled: bool,
}

fn venv_python(root: &Path) -> PathBuf {
    if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    }
}

/// Папка, где лежит программа (там же — `python/`, `bin/` и `sdk/` из установщика).
pub fn install_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Копия исходников, если программа собрана внутри неё (`cargo run` в `shell/`).
fn checkout_around(dir: &Path) -> Option<PathBuf> {
    dir.ancestors()
        .find(|p| p.join("pyproject.toml").is_file() && p.join("packages").join("server").is_dir())
        .map(Path::to_path_buf)
}

/// Python для сервера: `AGEN_PYTHON`; в режиме разработчика — `.venv` копии исходников;
/// иначе — Python из установщика; при разработке самой оболочки — `.venv` репозитория вокруг неё.
pub fn find_python(launch: &Launch) -> Result<Python, String> {
    if let Some(p) = std::env::var_os("AGEN_PYTHON") {
        return Ok(Python {
            exe: PathBuf::from(p),
            bundled: false,
        });
    }
    if let Some(src) = &launch.dev_source {
        let exe = venv_python(src);
        if exe.is_file() {
            return Ok(Python { exe, bundled: false });
        }
        return Err(format!(
            "В папке исходников {} нет окружения .venv. Выполните в ней `uv sync` или выключите режим разработчика.",
            src.display()
        ));
    }
    let dir = install_dir();
    let bundled = dir
        .join("python")
        .join(if cfg!(windows) { "python.exe" } else { "bin/python3" });
    if bundled.is_file() {
        return Ok(Python {
            exe: bundled,
            bundled: true,
        });
    }
    if let Some(root) = checkout_around(&dir) {
        let exe = venv_python(&root);
        if exe.is_file() {
            return Ok(Python { exe, bundled: false });
        }
    }
    Err(format!(
        "Не найден Python приложения: ожидался {}. Переустановите приложение.",
        bundled.display()
    ))
}

/// Проверить папку исходников для режима разработчика.
pub fn check_dev_source(path: &Path) -> Result<(), String> {
    if !(path.join("pyproject.toml").is_file() && path.join("packages").join("server").is_dir()) {
        return Err(format!(
            "{} — не копия исходников Autogenerator (нет pyproject.toml и packages/server)",
            path.display()
        ));
    }
    if !venv_python(path).is_file() {
        return Err(format!(
            "В {} нет окружения .venv: выполните в этой папке `uv sync`",
            path.display()
        ));
    }
    Ok(())
}

type Log = Arc<Mutex<VecDeque<String>>>;

fn push(log: &Log, line: String) {
    let mut l = log.lock().unwrap();
    if l.len() == LOG_LINES {
        l.pop_front();
    }
    l.push_back(line);
}

enum Signal {
    Ready(serde_json::Value),
    Error(serde_json::Value),
}

fn read_lines(stream: impl Read + Send + 'static, log: Log, tx: Option<mpsc::Sender<Signal>>) {
    std::thread::spawn(move || {
        let mut r = BufReader::new(stream);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match r.read_until(b'\n', &mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let line = String::from_utf8_lossy(&buf).trim_end().to_string();
            if let Some(tx) = &tx {
                let parse = |rest: &str| serde_json::from_str(rest.trim()).unwrap_or(serde_json::Value::Null);
                if let Some(rest) = line.strip_prefix(READY) {
                    let _ = tx.send(Signal::Ready(parse(rest)));
                    continue;
                }
                if let Some(rest) = line.strip_prefix(ERROR) {
                    let _ = tx.send(Signal::Error(parse(rest)));
                }
            }
            push(&log, line);
        }
    });
}

pub struct ServerProcess {
    child: Child,
    pub port: u16,
    pub url: String,
    log: Log,
    started: Instant,
}

/// Запустить сервер и дождаться строки готовности.
pub fn start(
    launch: &Launch,
    home: &Path,
    token: &str,
    job: Option<&Job>,
    timeout: Duration,
) -> Result<ServerProcess, StartError> {
    let python = find_python(launch).map_err(|message| StartError::Failed {
        message,
        log: String::new(),
    })?;
    let mut cmd = Command::new(&python.exe);
    cmd.args(["-m", "autogenerator.server", "--port", "0", "--parent"])
        .arg(std::process::id().to_string())
        .arg("--home")
        .arg(home);
    if launch.dev_source.is_some() {
        cmd.arg("--dev");
    }
    let cwd = launch.dev_source.clone().unwrap_or_else(install_dir);
    cmd.current_dir(cwd)
        .env("AGEN_TOKEN", token)
        .env("AGEN_WINDOWLESS", "1")
        .env("PYTHONUTF8", "1")
        .env("PYTHONSAFEPATH", "1")
        .env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if python.bundled {
        cmd.env("PYTHONNOUSERSITE", "1");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    settings::log(&format!(
        "Запуск сервера: {} (папка данных {})",
        python.exe.display(),
        home.display()
    ));
    let mut child = cmd.spawn().map_err(|e| StartError::Failed {
        message: format!("Сервер не запустился ({}): {e}", python.exe.display()),
        log: String::new(),
    })?;
    if let Some(job) = job {
        if let Err(e) = job.assign(&child) {
            settings::log(&format!("Объект задания не назначен: {e}"));
        }
    }
    let log: Log = Arc::new(Mutex::new(VecDeque::new()));
    let (tx, rx) = mpsc::channel();
    read_lines(child.stdout.take().expect("stdout"), log.clone(), Some(tx));
    read_lines(child.stderr.take().expect("stderr"), log.clone(), None);
    let tail = |log: &Log| log.lock().unwrap().iter().cloned().collect::<Vec<_>>().join("\n");
    let t0 = Instant::now();
    loop {
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(Signal::Ready(info)) => {
                let port = info["port"].as_u64().unwrap_or(0) as u16;
                let url = info["url"]
                    .as_str()
                    .map(str::to_string)
                    .unwrap_or_else(|| format!("http://127.0.0.1:{port}"));
                settings::log(&format!("Сервер готов: {url} за {:.1} с", t0.elapsed().as_secs_f32()));
                return Ok(ServerProcess {
                    child,
                    port,
                    url,
                    log,
                    started: Instant::now(),
                });
            }
            Ok(Signal::Error(e)) => {
                let _ = wait_exit(&mut child, Duration::from_secs(5));
                let text = |k: &str| e[k].as_str().map(str::to_string);
                let err = StartError::Reported {
                    code: text("code").unwrap_or_else(|| "start_failed".into()),
                    message: text("message").unwrap_or_else(|| "Сервер не запустился".into()),
                    hint: text("hint"),
                };
                settings::log(&format!("Сервер не запустился: {err:?}"));
                return Err(err);
            }
            Err(mpsc::RecvTimeoutError::Timeout) | Err(mpsc::RecvTimeoutError::Disconnected) => {}
        }
        if let Ok(Some(status)) = child.try_wait() {
            std::thread::sleep(Duration::from_millis(300)); // дочитать последние строки
            let message = format!("Сервер завершился при запуске ({})", describe(status));
            settings::log(&format!("{message}\n{}", tail(&log)));
            return Err(StartError::Failed {
                message,
                log: tail(&log),
            });
        }
        if t0.elapsed() > timeout {
            let _ = child.kill();
            let _ = child.wait();
            let message = format!("Сервер не ответил за {} с", timeout.as_secs());
            settings::log(&format!("{message}\n{}", tail(&log)));
            return Err(StartError::Failed {
                message,
                log: tail(&log),
            });
        }
    }
}

fn describe(status: std::process::ExitStatus) -> String {
    match status.code() {
        Some(c) => format!("код {c}"),
        None => "прерван".into(),
    }
}

fn wait_exit(child: &mut Child, timeout: Duration) -> bool {
    let t0 = Instant::now();
    while t0.elapsed() < timeout {
        if let Ok(Some(_)) = child.try_wait() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    false
}

impl ServerProcess {
    /// Процесс завершился: как именно (иначе None).
    pub fn exited(&mut self) -> Option<String> {
        match self.child.try_wait() {
            Ok(Some(status)) => Some(describe(status)),
            Ok(None) => None,
            Err(e) => Some(e.to_string()),
        }
    }

    pub fn uptime(&self) -> Duration {
        self.started.elapsed()
    }

    pub fn log_tail(&self) -> String {
        self.log.lock().unwrap().iter().cloned().collect::<Vec<_>>().join("\n")
    }

    /// Остановить: попросить сервер (он закроет базу и исполнители), а если не успел — завершить.
    pub fn shutdown(&mut self, token: &str) {
        if self.exited().is_some() {
            return;
        }
        let asked = http::request(
            self.port,
            "POST",
            "/api/system/shutdown",
            Some(token),
            Duration::from_secs(3),
        );
        if asked.is_err() || !wait_exit(&mut self.child, STOP_TIMEOUT) {
            settings::log("Сервер не остановился сам: процесс завершён");
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
    }
}

impl Drop for ServerProcess {
    fn drop(&mut self) {
        if let Ok(None) = self.child.try_wait() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dev_source_without_venv_is_explained() {
        let dir = std::env::temp_dir().join(format!("agen-shell-test-{}", std::process::id()));
        std::fs::create_dir_all(dir.join("packages").join("server")).unwrap();
        std::fs::write(dir.join("pyproject.toml"), "").unwrap();
        let err = check_dev_source(&dir).unwrap_err();
        assert!(err.contains("uv sync"), "{err}");
        let launch = Launch {
            home: None,
            dev_source: Some(dir.clone()),
        };
        if std::env::var_os("AGEN_PYTHON").is_none() {
            assert!(find_python(&launch).unwrap_err().contains(".venv"));
        }
        assert!(check_dev_source(&std::env::temp_dir())
            .unwrap_err()
            .contains("не копия"));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
