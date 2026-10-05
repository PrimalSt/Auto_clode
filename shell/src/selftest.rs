//! `autogenerator.exe --self-test [--home ПАПКА] [--report ФАЙЛ]`: проверка установки без окна.
//!
//! Запускает сервер так же, как окно (тот же Python, объект задания, токен), и проверяет:
//! здоровье, токен, сведения о системе, исполнители и манифест модулей, отдачу интерфейса,
//! остановку (сервер должен остановиться сам по просьбе и без ошибки, а не завершиться
//! принудительно). Без `--home` берётся временная папка данных, которая потом удаляется: данные
//! пользователя не трогаются. Код выхода 0 — всё прошло. CI запускает это после тихой установки.

use crate::http;
use crate::settings::{self, Launch};
use crate::{job, server};
use std::path::PathBuf;
use std::time::{Duration, Instant};

pub fn run(home: Option<PathBuf>, report: Option<PathBuf>) -> i32 {
    let temp = home.is_none();
    let home =
        home.unwrap_or_else(|| std::env::temp_dir().join(format!("autogenerator-self-test-{}", std::process::id())));
    let token = crate::app::new_token();
    let launch = Launch {
        home: Some(home.clone()),
        dev_source: None,
    };
    let mut lines: Vec<String> = vec![format!(
        "Autogenerator {} — самопроверка, папка данных {}",
        settings::VERSION,
        home.display()
    )];
    let mut ok = true;
    let mut check = |name: &str, res: Result<String, String>| {
        match &res {
            Ok(info) => lines.push(format!("OK   {name}: {info}")),
            Err(e) => {
                ok = false;
                lines.push(format!("FAIL {name}: {e}"));
            }
        }
        res.is_ok()
    };
    if check(
        "python",
        server::find_python(&launch).map(|p| p.exe.display().to_string()),
    ) {
        let job = job::for_server();
        check(
            "объект задания",
            job.as_ref()
                .map(|_| match job::memory_limit() {
                    0 => "не нужен вне Windows".to_string(),
                    // ГБ — по 1024³ байт, как в окне приложения
                    m => format!("предел памяти процесса {:.1} ГБ", m as f64 / (1u64 << 30) as f64),
                })
                .map_err(Clone::clone),
        );
        let t0 = Instant::now();
        match server::start(&launch, &home, &token, job.as_ref().ok(), Duration::from_secs(180)) {
            Ok(mut srv) => {
                check(
                    "запуск сервера",
                    Ok(format!("{} за {:.1} с", srv.url, t0.elapsed().as_secs_f32())),
                );
                let get = |path: &str, token: Option<&str>, secs: u64| {
                    http::request(srv.port, "GET", path, token, Duration::from_secs(secs))
                };
                check(
                    "/api/health",
                    get("/api/health", None, 10).and_then(|r| {
                        (r.status == 200)
                            .then(|| r.body.clone())
                            .ok_or(format!("код {}", r.status))
                    }),
                );
                check(
                    "токен",
                    get("/api/system", None, 10).and_then(|r| {
                        (r.status == 401)
                            .then(|| "без токена — 401".into())
                            .ok_or(format!("без токена ответ {}", r.status))
                    }),
                );
                check(
                    "/api/system",
                    get("/api/system", Some(&token), 10).and_then(|r| match r.json() {
                        Some(j) if r.status == 200 => Ok(format!("версия {}, папка {}", j["version"], j["home"])),
                        _ => Err(format!("код {}: {}", r.status, r.body)),
                    }),
                );
                check(
                    "/api/modules",
                    get("/api/modules", Some(&token), 180).and_then(|r| match r.json() {
                        Some(j) if r.status == 200 && j["error"].is_null() => {
                            let plugins = j["plugins"]["plugins"].as_array().map(Vec::len).unwrap_or(0);
                            Ok(format!(
                                "исполнителей {}, плагинов {plugins}",
                                j["executors"].as_array().map(Vec::len).unwrap_or(0)
                            ))
                        }
                        Some(j) => Err(format!("код {}: {}", r.status, j["error"])),
                        None => Err(format!("код {}", r.status)),
                    }),
                );
                check(
                    "интерфейс",
                    get("/", None, 10).and_then(|r| {
                        (r.status == 200 && r.body.contains("<div id=\"root\""))
                            .then(|| "index.html отдаётся".into())
                            .ok_or(format!("код {}", r.status))
                    }),
                );
                check(
                    "остановка",
                    match srv.shutdown(&token) {
                        server::Stop::Clean => Ok("сервер остановился сам (код 0)".into()),
                        server::Stop::Failed(how) => Err(format!("сервер остановился с ошибкой ({how})")),
                        server::Stop::Killed(why) => Err(format!("сервер {why}: процесс завершён")),
                        server::Stop::Exited(how) => Err(format!("сервер завершился раньше времени ({how})")),
                    },
                );
            }
            Err(e) => {
                let log = match &e {
                    server::StartError::Failed { log, .. } => log.clone(),
                    server::StartError::Reported { hint, .. } => hint.clone().unwrap_or_default(),
                };
                check("запуск сервера", Err(format!("{}\n{log}", e.message())));
            }
        }
    }
    if temp {
        let _ = std::fs::remove_dir_all(&home);
    }
    lines.push(if ok {
        "Итог: всё в порядке".into()
    } else {
        "Итог: есть ошибки".into()
    });
    let text = lines.join("\n") + "\n";
    print!("{text}");
    if let Some(p) = report {
        if let Err(e) = std::fs::write(&p, &text) {
            eprintln!("Отчёт не записан в {}: {e}", p.display());
        }
    }
    settings::log(&format!("Самопроверка: {}", if ok { "ok" } else { "ошибки" }));
    i32::from(!ok)
}
