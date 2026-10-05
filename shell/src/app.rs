//! Окно приложения и надзор за сервером.
//!
//! Окно открывается со стартовой страницы оболочки (`ui/index.html`), а когда сервер готов,
//! переходит на его адрес `http://127.0.0.1:<порт>/`: интерфейс отдаёт сам сервер. Страницам
//! сервера оболочка добавляет `window.__AGEN__` (токен, системные диалоги, открытие файлов,
//! перетаскивание файлов, настройки запуска).
//!
//! Надзор: упал сервер — перезапуск; три сбоя подряд или ошибка, которую повтор не исправит, —
//! безопасный режим на стартовой странице (повторить, без режима разработчика, последний
//! удачный запуск, другая папка данных, журналы). Удачный запуск — тот, где сервер проработал
//! минуту (`STABLE`). Токен один на всё время работы оболочки.

use crate::server::{self, ServerProcess, StartError};
use crate::settings::{self, Launch, Settings};
use crate::{http, job};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;
use tauri::{
    AppHandle, DragDropEvent, Manager, RunEvent, State, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

const MAX_FAILURES: u32 = 3;
/// Сервер, проработавший дольше, упал «во время работы»: счёт сбоев начинается заново.
const STABLE: Duration = Duration::from_secs(60);
const START_TIMEOUT: Duration = Duration::from_secs(120);
/// Сколько при закрытии окна ждать ответа сервера, идут ли задания.
const CLOSE_CHECK: Duration = Duration::from_secs(3);
/// Что можно открыть программой по умолчанию из окна (отчёты, выгрузки, журналы, папки).
const OPENABLE: &[&str] = &[
    "pptx", "ppt", "potx", "xlsx", "xlsm", "xlsb", "xls", "csv", "txt", "log", "json", "yaml", "yml", "pdf", "png",
    "svg", "md",
];

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum Phase {
    Starting {
        attempt: u32,
        note: Option<String>,
    },
    Running {
        url: String,
    },
    Stopping,
    Safe {
        reason: String,
        code: Option<String>,
        hint: Option<String>,
        log: String,
    },
}

enum Cmd {
    Restart,
    Quit(mpsc::Sender<()>),
}

pub struct Shell {
    settings: Mutex<Settings>,
    token: String,
    phase: Mutex<Phase>,
    port: Arc<AtomicU16>,
    tx: mpsc::Sender<Cmd>,
    /// Предел памяти процесса из объекта задания (0 — объекта задания нет).
    memory_limit: AtomicU64,
    /// Идёт вопрос о закрытии окна (второе нажатие на крестик его не повторяет).
    closing: AtomicBool,
}

impl Shell {
    fn set_phase(&self, phase: Phase) {
        *self.phase.lock().unwrap() = phase;
    }

    fn restart(&self) {
        let _ = self.tx.send(Cmd::Restart);
    }

    /// Остановить сервер при выходе. Ждём чуть дольше, чем длится остановка сервера (просьба,
    /// ожидание, завершение процесса); если и этого мало — сервер завершит объект задания.
    fn quit(&self) {
        let (done, wait) = mpsc::channel();
        if self.tx.send(Cmd::Quit(done)).is_ok() {
            let _ = wait.recv_timeout(server::STOP_BUDGET + Duration::from_secs(2));
        }
    }

    fn update(&self, change: impl FnOnce(&mut Settings)) -> Result<(), String> {
        let mut s = self.settings.lock().unwrap();
        change(&mut s);
        s.save()
    }

    /// Запомнить удачный запуск — с теми настройками, с которыми сервер запускался (их могли
    /// уже поменять для следующего запуска).
    fn remember_good(&self, launch: &Launch) {
        let mut s = self.settings.lock().unwrap();
        if s.last_good.as_ref() != Some(launch) {
            s.last_good = Some(launch.clone());
            if let Err(e) = s.save() {
                settings::log(&e);
            }
        }
    }

    fn describe(&self, with_phase: bool) -> serde_json::Value {
        let s = self.settings.lock().unwrap();
        let path = |p: &Option<PathBuf>| p.as_ref().map(|p| p.display().to_string());
        let mut out = serde_json::json!({
            "version": settings::VERSION,
            "home": s.home().display().to_string(),
            "default_home": settings::default_home().display().to_string(),
            "custom_home": s.launch.home.is_some(),
            "dev_source": path(&s.launch.dev_source),
            "last_good": s.last_good.as_ref().map(|l| serde_json::json!({
                "home": path(&l.home), "dev_source": path(&l.dev_source),
            })),
            "can_use_last_good": s.last_good.as_ref().is_some_and(|l| *l != s.launch),
            "memory_limit": self.memory_limit.load(Ordering::SeqCst),
            "config": settings::config_dir().display().to_string(),
        });
        if with_phase {
            out["state"] = serde_json::to_value(&*self.phase.lock().unwrap()).unwrap_or_default();
        }
        out
    }
}

pub fn new_token() -> String {
    let mut b = [0u8; 32];
    getrandom::fill(&mut b).expect("getrandom");
    b.iter().map(|x| format!("{x:02x}")).collect()
}

// --- надзор ----------------------------------------------------------------------

enum Outcome {
    Restart,
    Quit(Option<mpsc::Sender<()>>),
    Exited(String),
}

/// Ждать команды или конца сервера; `stable` вызывается один раз, когда сервер проработал `STABLE`.
fn wait(srv: &mut ServerProcess, rx: &mpsc::Receiver<Cmd>, mut stable: impl FnMut()) -> Outcome {
    let mut seen_stable = false;
    loop {
        match rx.recv_timeout(Duration::from_millis(300)) {
            Ok(Cmd::Restart) => return Outcome::Restart,
            Ok(Cmd::Quit(done)) => return Outcome::Quit(Some(done)),
            Err(mpsc::RecvTimeoutError::Disconnected) => return Outcome::Quit(None),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        if let Some(how) = srv.exited() {
            return Outcome::Exited(how);
        }
        if !seen_stable && srv.uptime() > STABLE {
            seen_stable = true;
            stable();
        }
    }
}

/// Подсказка безопасного режима, когда сервер упал или не ответил (сам он причину не назвал).
fn crash_hint(launch: &Launch) -> String {
    let mut hint = "Последние строки вывода сервера — ниже, полные журналы — «Открыть журналы».".to_string();
    if launch.dev_source.is_some() {
        hint.push_str(
            " Сервер запускался из копии исходников: если сбой из-за правки кода, запустите без режима разработчика.",
        );
    }
    hint
}

fn supervise(app: AppHandle, rx: mpsc::Receiver<Cmd>) {
    let job = job::for_server()
        .map_err(|e| settings::log(&format!("Объект задания не создан: {e}")))
        .ok();
    if job.is_some() {
        app.state::<Shell>()
            .memory_limit
            .store(job::memory_limit(), Ordering::SeqCst);
    }
    let mut failures = 0u32;
    loop {
        let shell = app.state::<Shell>();
        let s = shell.settings.lock().unwrap().clone();
        let note = (failures > 0).then(|| "Сервер перезапускается после сбоя".to_string());
        shell.set_phase(Phase::Starting {
            attempt: failures + 1,
            note,
        });
        show_splash(&app);
        let safe = match server::start(&s.launch, &s.home(), &shell.token, job.as_ref(), START_TIMEOUT) {
            Ok(mut srv) => {
                shell.port.store(srv.port, Ordering::SeqCst);
                shell.set_phase(Phase::Running { url: srv.url.clone() });
                navigate(&app, &srv.url);
                // удачным запуск считается, когда сервер проработал минуту, а не сразу после готовности:
                // запуск, который поднялся и упал, не должен вытеснить прошлый удачный
                let outcome = wait(&mut srv, &rx, || shell.remember_good(&s.launch));
                shell.port.store(0, Ordering::SeqCst);
                match outcome {
                    Outcome::Restart => {
                        shell.set_phase(Phase::Stopping);
                        show_splash(&app);
                        srv.shutdown(&shell.token);
                        failures = 0;
                        continue;
                    }
                    Outcome::Quit(done) => {
                        shell.set_phase(Phase::Stopping);
                        srv.shutdown(&shell.token);
                        if let Some(done) = done {
                            let _ = done.send(());
                        }
                        return;
                    }
                    Outcome::Exited(how) => {
                        failures = if srv.uptime() > STABLE { 1 } else { failures + 1 };
                        settings::log(&format!(
                            "Сервер завершился ({how}), сбой {failures}\n{}",
                            srv.log_tail()
                        ));
                        if failures < MAX_FAILURES {
                            std::thread::sleep(Duration::from_secs(1));
                            continue;
                        }
                        Phase::Safe {
                            reason: format!(
                                "Сервер приложения завершился с ошибкой {MAX_FAILURES} раза подряд ({how})"
                            ),
                            code: None,
                            hint: Some(crash_hint(&s.launch)),
                            log: srv.log_tail(),
                        }
                    }
                }
            }
            Err(StartError::Reported {
                code,
                message,
                hint,
                log,
            }) => Phase::Safe {
                reason: message,
                code: Some(code),
                hint,
                log,
            },
            Err(StartError::Failed { message, log }) => {
                failures += 1;
                if failures < MAX_FAILURES {
                    std::thread::sleep(Duration::from_secs(1));
                    continue;
                }
                Phase::Safe {
                    reason: message,
                    code: None,
                    hint: Some(crash_hint(&s.launch)),
                    log,
                }
            }
        };
        settings::log("Безопасный режим");
        shell.set_phase(safe);
        show_splash(&app);
        match rx.recv() {
            Ok(Cmd::Restart) => failures = 0,
            Ok(Cmd::Quit(done)) => {
                let _ = done.send(());
                return;
            }
            Err(_) => return,
        }
    }
}

// --- окно ------------------------------------------------------------------------

fn splash_url() -> Url {
    let url = if cfg!(windows) {
        "http://tauri.localhost/index.html"
    } else {
        "tauri://localhost/index.html"
    };
    Url::parse(url).expect("splash url")
}

fn is_splash(url: &Url) -> bool {
    matches!(
        (url.scheme(), url.host_str()),
        ("tauri", Some("localhost")) | ("http" | "https", Some("tauri.localhost"))
    )
}

/// Куда окну можно перейти: стартовая страница и текущий сервер. Остальное — в браузере.
fn allowed(port: u16, url: &Url) -> bool {
    is_splash(url)
        || (url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && port != 0 && url.port() == Some(port))
        || url.as_str() == "about:blank"
}

fn navigate(app: &AppHandle, url: &str) {
    if let (Some(w), Ok(u)) = (app.get_webview_window("main"), Url::parse(url)) {
        let _ = w.navigate(u);
    }
}

fn show_splash(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        if !w.url().is_ok_and(|u| is_splash(&u)) {
            let _ = w.navigate(splash_url());
        }
    }
}

fn focus(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

const BRIDGE: &str = r#"(function () {
  if (location.hostname !== "127.0.0.1" || window.__AGEN__) return;
  var invoke = function (cmd, args) { return window.__TAURI_INTERNALS__.invoke(cmd, args || {}); };
  var listeners = { drop: [], hover: [] };
  var on = function (kind) {
    return function (cb) {
      listeners[kind].push(cb);
      return function () { listeners[kind] = listeners[kind].filter(function (f) { return f !== cb; }); };
    };
  };
  var emit = function (kind, value) {
    listeners[kind].forEach(function (f) { try { f(value); } catch (e) { console.error(e); } });
  };
  window.__AGEN__ = {
    shell: true,
    version: __VERSION__,
    token: __TOKEN__,
    pickFiles: function (o) { return invoke("pick_files", { opts: o || {} }); },
    pickFolder: function (o) { return invoke("pick_folder", { opts: o || {} }); },
    openPath: function (p) { return invoke("open_path", { path: p, reveal: false }); },
    revealPath: function (p) { return invoke("open_path", { path: p, reveal: true }); },
    settings: function () { return invoke("shell_settings"); },
    setHome: function (p) { return invoke("set_home", { path: p }); },
    setDevSource: function (p) { return invoke("set_dev_source", { path: p }); },
    restart: function () { return invoke("restart_server"); },
    openLogs: function () { return invoke("open_logs"); },
    onDrop: on("drop"),
    onDragHover: on("hover"),
    _drop: function (paths) { emit("drop", paths); },
    _hover: function (over) { emit("hover", over); }
  };
})();"#;

fn bridge_script(token: &str) -> String {
    let json = |s: &str| serde_json::to_string(s).unwrap_or_default();
    BRIDGE
        .replace("__TOKEN__", &json(token))
        .replace("__VERSION__", &json(settings::VERSION))
}

/// Что спросить при закрытии окна: `None` — заданий нет, закрыть без вопроса. Сервер не ответил
/// (`active` пусто) — неизвестно, идут ли задания, поэтому вопрос задаётся.
fn close_question(active: Option<u64>) -> Option<String> {
    match active {
        Some(0) => None,
        Some(n) => Some(format!("Идут задания: {n}. Если закрыть приложение, они прервутся.")),
        None => Some(
            "Сервер приложения не ответил, поэтому неизвестно, идут ли задания. Если закрыть приложение, \
             идущие задания прервутся."
                .into(),
        ),
    }
}

/// Закрыть окно, пока идут задания, — только после вопроса. Сервер спрашивается в отдельном
/// потоке, чтобы окно не замирало, пока он отвечает.
fn confirm_close(window: &WebviewWindow, api: &tauri::CloseRequestApi) {
    let shell = window.state::<Shell>();
    let port = shell.port.load(Ordering::SeqCst);
    if port == 0 {
        return;
    }
    api.prevent_close();
    if shell.closing.swap(true, Ordering::SeqCst) {
        return; // вопрос уже задаётся
    }
    let window = window.clone();
    std::thread::spawn(move || {
        let shell = window.state::<Shell>();
        let active = http::request(port, "GET", "/api/system", Some(&shell.token), CLOSE_CHECK)
            .ok()
            .filter(|r| r.status == 200)
            .and_then(|r| r.json())
            .and_then(|j| j["jobs_active"].as_u64());
        let close = match close_question(active) {
            None => true,
            Some(text) => window
                .dialog()
                .message(text)
                .title("Закрыть Autogenerator?")
                .kind(MessageDialogKind::Warning)
                .buttons(MessageDialogButtons::OkCancelCustom(
                    "Закрыть".into(),
                    "Не закрывать".into(),
                ))
                .parent(&window)
                .blocking_show(),
        };
        if close {
            window.app_handle().exit(0);
        } else {
            shell.closing.store(false, Ordering::SeqCst);
        }
    });
}

fn main_window(app: &AppHandle, token: &str, port: Arc<AtomicU16>) -> tauri::Result<WebviewWindow> {
    let w = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Autogenerator")
        .inner_size(1360.0, 860.0)
        .min_inner_size(960.0, 600.0)
        .center()
        .initialization_script(bridge_script(token))
        .on_navigation(move |url| {
            let ok = allowed(port.load(Ordering::SeqCst), url);
            if !ok && matches!(url.scheme(), "http" | "https" | "mailto") {
                let _ = tauri_plugin_opener::open_url(url.as_str(), None::<&str>);
            }
            ok
        })
        .build()?;
    let target = w.clone();
    w.on_window_event(move |event| match event {
        WindowEvent::DragDrop(DragDropEvent::Enter { .. }) => {
            let _ = target.eval("window.__AGEN__&&window.__AGEN__._hover(true)");
        }
        WindowEvent::DragDrop(DragDropEvent::Leave) => {
            let _ = target.eval("window.__AGEN__&&window.__AGEN__._hover(false)");
        }
        WindowEvent::DragDrop(DragDropEvent::Drop { paths, .. }) => {
            let list: Vec<String> = paths.iter().map(|p| p.display().to_string()).collect();
            let json = serde_json::to_string(&list).unwrap_or_else(|_| "[]".into());
            let _ = target.eval(format!(
                "window.__AGEN__&&(window.__AGEN__._hover(false),window.__AGEN__._drop({json}))"
            ));
        }
        WindowEvent::CloseRequested { api, .. } => confirm_close(&target, api),
        _ => {}
    });
    Ok(w)
}

// --- команды окна ----------------------------------------------------------------

#[derive(Debug, Default, Deserialize)]
pub struct PickOpts {
    title: Option<String>,
    multiple: Option<bool>,
    extensions: Option<Vec<String>>,
}

#[tauri::command]
async fn pick_files(window: WebviewWindow, opts: Option<PickOpts>) -> Result<Option<Vec<String>>, String> {
    let opts = opts.unwrap_or_default();
    let mut d = window.dialog().file().set_parent(&window);
    if let Some(t) = opts.title {
        d = d.set_title(t);
    }
    let ext: Vec<String> = opts
        .extensions
        .unwrap_or_default()
        .iter()
        .map(|e| e.trim_start_matches('.').to_string())
        .collect();
    if !ext.is_empty() {
        let refs: Vec<&str> = ext.iter().map(String::as_str).collect();
        d = d.add_filter(format!("Файлы ({})", ext.join(", ")), &refs);
    }
    // диалог блокирует поток команды (не главный поток окна)
    let picked = if opts.multiple.unwrap_or(false) {
        d.blocking_pick_files()
    } else {
        d.blocking_pick_file().map(|p| vec![p])
    };
    Ok(picked.map(|v| {
        v.into_iter()
            .filter_map(|p| p.into_path().ok())
            .map(|p| p.display().to_string())
            .collect()
    }))
}

#[tauri::command]
async fn pick_folder(window: WebviewWindow, opts: Option<PickOpts>) -> Result<Option<String>, String> {
    let mut d = window.dialog().file().set_parent(&window);
    if let Some(t) = opts.and_then(|o| o.title) {
        d = d.set_title(t);
    }
    Ok(d.blocking_pick_folder()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.display().to_string()))
}

/// Сетевой путь (`\\сервер\папка`, `\\?\UNC\…`): открывать его нельзя — Windows сам отправил бы
/// серверу данные для входа. `\\?\C:\…` — локальный.
fn is_network(path: &str) -> bool {
    let p = path.replace('/', "\\");
    match p.strip_prefix(r"\\?\") {
        Some(rest) => rest.as_bytes().get(1) != Some(&b':'),
        None => p.starts_with(r"\\"),
    }
}

#[tauri::command]
fn open_path(path: String, reveal: Option<bool>) -> Result<(), String> {
    let raw = path.trim().trim_matches('"');
    if is_network(raw) {
        return Err(format!("Сетевые пути приложение не открывает: {raw}"));
    }
    let p = PathBuf::from(raw);
    if !p.is_absolute() {
        return Err(format!("Нужен полный путь: {raw}"));
    }
    if !p.exists() {
        return Err(format!("Не найдено: {}", p.display()));
    }
    if reveal.unwrap_or(false) {
        return tauri_plugin_opener::reveal_item_in_dir(&p).map_err(|e| e.to_string());
    }
    let ext = p
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_lowercase)
        .unwrap_or_default();
    if p.is_file() && !OPENABLE.contains(&ext.as_str()) {
        return Err(format!("Такие файлы приложение не открывает: {}", p.display()));
    }
    tauri_plugin_opener::open_path(&p, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn shell_state(shell: State<'_, Shell>) -> serde_json::Value {
    shell.describe(true)
}

#[tauri::command]
fn shell_settings(shell: State<'_, Shell>) -> serde_json::Value {
    shell.describe(false)
}

fn local_folder(path: Option<String>) -> Result<Option<PathBuf>, String> {
    let Some(p) = path
        .map(|p| p.trim().trim_matches('"').to_string())
        .filter(|p| !p.is_empty())
    else {
        return Ok(None);
    };
    if is_network(&p) {
        return Err("Папка должна быть на этом компьютере, а не в сети".into());
    }
    let pb = PathBuf::from(&p);
    if !pb.is_absolute() {
        return Err(format!("Нужен полный путь к папке: {p}"));
    }
    Ok(Some(pb))
}

/// Другая папка данных (None — по умолчанию). Сервер перезапускается с ней.
#[tauri::command]
fn set_home(shell: State<'_, Shell>, path: Option<String>) -> Result<(), String> {
    let home = local_folder(path)?;
    shell.update(|s| s.launch.home = home)?;
    settings::log("Папка данных изменена: перезапуск сервера");
    shell.restart();
    Ok(())
}

/// Режим разработчика: сервер из копии исходников (None — выключить).
#[tauri::command]
fn set_dev_source(shell: State<'_, Shell>, path: Option<String>) -> Result<(), String> {
    let src = local_folder(path)?;
    if let Some(p) = &src {
        server::check_dev_source(p)?;
    }
    shell.update(|s| s.launch.dev_source = src)?;
    settings::log("Режим разработчика изменён: перезапуск сервера");
    shell.restart();
    Ok(())
}

#[tauri::command]
fn restart_server(shell: State<'_, Shell>) {
    shell.restart();
}

#[tauri::command]
fn use_last_good(shell: State<'_, Shell>) -> Result<(), String> {
    shell.update(|s| {
        if let Some(l) = s.last_good.clone() {
            s.launch = l;
        }
    })?;
    shell.restart();
    Ok(())
}

/// Журналы: папка `logs` в папке данных (журнал сервера, если сервер уже запускался с этой
/// папкой) и `shell.log` в папке настроек (журнал оболочки) — оба в окнах проводника.
#[tauri::command]
fn open_logs(shell: State<'_, Shell>) -> Result<(), String> {
    let server_logs = shell.settings.lock().unwrap().home().join("logs");
    if server_logs.is_dir() {
        tauri_plugin_opener::open_path(&server_logs, None::<&str>).map_err(|e| e.to_string())?;
    }
    let shell_log = settings::config_dir().join("shell.log");
    if shell_log.is_file() {
        tauri_plugin_opener::reveal_item_in_dir(&shell_log).map_err(|e| e.to_string())
    } else {
        tauri_plugin_opener::open_path(settings::config_dir(), None::<&str>).map_err(|e| e.to_string())
    }
}

pub fn run() {
    let token = new_token();
    let port = Arc::new(AtomicU16::new(0));
    let (tx, rx) = mpsc::channel();
    let shell = Shell {
        settings: Mutex::new(Settings::load()),
        token: token.clone(),
        phase: Mutex::new(Phase::Starting { attempt: 1, note: None }),
        port: port.clone(),
        tx,
        memory_limit: AtomicU64::new(0),
        closing: AtomicBool::new(false),
    };
    settings::log(&format!("Оболочка {} запущена", settings::VERSION));
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| focus(app)))
        .plugin(tauri_plugin_dialog::init())
        .manage(shell)
        .invoke_handler(tauri::generate_handler![
            pick_files,
            pick_folder,
            open_path,
            shell_state,
            shell_settings,
            set_home,
            set_dev_source,
            restart_server,
            use_last_good,
            open_logs,
        ])
        .setup(move |app| {
            main_window(app.handle(), &token, port)?;
            let handle = app.handle().clone();
            std::thread::Builder::new()
                .name("supervisor".into())
                .spawn(move || supervise(handle, rx))?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Окно приложения не создано");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            app.state::<Shell>().quit();
            settings::log("Оболочка закрыта");
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_goes_only_to_the_splash_and_the_current_server() {
        let u = |s: &str| Url::parse(s).unwrap();
        assert!(allowed(0, &u("http://tauri.localhost/index.html")));
        assert!(allowed(0, &u("tauri://localhost/index.html")));
        assert!(allowed(8123, &u("http://127.0.0.1:8123/#/runs")));
        assert!(!allowed(8123, &u("http://127.0.0.1:9000/")));
        assert!(!allowed(0, &u("http://127.0.0.1:8123/")));
        assert!(!allowed(8123, &u("http://localhost:8123/")));
        assert!(!allowed(8123, &u("https://example.com/")));
    }

    #[test]
    fn bridge_gets_a_quoted_token_and_folders_must_be_local() {
        let script = bridge_script("ab\"c");
        assert!(script.contains(r#"token: "ab\"c""#), "{script}");
        assert!(!script.contains("__TOKEN__") && !script.contains("__VERSION__"));
        assert!(local_folder(Some(r"\\server\share".into())).is_err());
        assert!(local_folder(Some("relative".into())).is_err());
        assert_eq!(local_folder(Some("  ".into())).unwrap(), None);
        assert_eq!(new_token().len(), 64);
    }

    #[test]
    fn network_paths_are_not_opened() {
        assert!(is_network(r"\\server\share\report.pptx"));
        assert!(is_network("//server/share"));
        assert!(is_network(r"\\?\UNC\server\share"));
        assert!(!is_network(r"\\?\C:\Reports\report.pptx"));
        assert!(!is_network(r"C:\Reports\report.pptx"));
        assert!(open_path(r"\\server\share\x.pptx".into(), None)
            .unwrap_err()
            .contains("Сетевые"));
        assert!(open_path("relative.pptx".into(), None)
            .unwrap_err()
            .contains("полный путь"));
    }

    #[test]
    fn closing_asks_when_jobs_run_or_the_server_is_silent() {
        assert_eq!(close_question(Some(0)), None);
        assert!(close_question(Some(2)).unwrap().contains("Идут задания: 2"));
        assert!(close_question(None).unwrap().contains("не ответил"));
    }

    #[test]
    fn crash_hint_suggests_leaving_dev_mode_only_in_dev_mode() {
        let mut launch = Launch::default();
        assert!(!crash_hint(&launch).contains("разработчика"));
        launch.dev_source = Some(PathBuf::from("/src"));
        assert!(crash_hint(&launch).contains("без режима разработчика"));
    }
}
