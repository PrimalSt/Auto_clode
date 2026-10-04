//! Настройки оболочки: папка данных, режим разработчика и последний удачный запуск.
//! Лежат в `shell.json` в папке настроек пользователя (`%APPDATA%\Autogenerator`).

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// С чем запускается сервер.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Launch {
    /// Папка данных; `None` — по умолчанию (`%LOCALAPPDATA%\Autogenerator`).
    #[serde(default)]
    pub home: Option<PathBuf>,
    /// Режим разработчика: копия исходников, из которой запускается сервер.
    #[serde(default)]
    pub dev_source: Option<PathBuf>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Settings {
    #[serde(flatten)]
    pub launch: Launch,
    /// Как сервер запустился в последний раз успешно: безопасный режим предлагает вернуться к этому.
    #[serde(default)]
    pub last_good: Option<Launch>,
}

/// Папка настроек оболочки (`AGEN_SHELL_CONFIG` — для тестов и своих сборок).
pub fn config_dir() -> PathBuf {
    if let Some(p) = std::env::var_os("AGEN_SHELL_CONFIG") {
        return PathBuf::from(p);
    }
    #[cfg(windows)]
    if let Some(p) = std::env::var_os("APPDATA") {
        return PathBuf::from(p).join("Autogenerator");
    }
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
        .unwrap_or_else(std::env::temp_dir);
    base.join("autogenerator")
}

/// Папка данных по умолчанию — так же, как у сервера и `agen` (модуль storage).
pub fn default_home() -> PathBuf {
    if let Some(p) = std::env::var_os("AGEN_HOME") {
        return PathBuf::from(p);
    }
    #[cfg(windows)]
    if let Some(p) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(p).join("Autogenerator");
    }
    let base = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local").join("share")))
        .unwrap_or_else(std::env::temp_dir);
    base.join("autogenerator")
}

/// Строка в журнал оболочки (`shell.log` в папке настроек; больше 1 МБ — начинается заново).
pub fn log(message: &str) {
    use std::io::Write;
    let dir = config_dir();
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let path = dir.join("shell.log");
    if std::fs::metadata(&path).map(|m| m.len() > 1 << 20).unwrap_or(false) {
        let _ = std::fs::rename(&path, dir.join("shell.log.1"));
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{} {message}", utc_now());
    }
}

/// Текущее время UTC как `2026-10-04 05:06:07` (без зависимостей от библиотек дат).
pub fn utc_now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let (days, rem) = ((secs / 86_400) as i64, secs % 86_400);
    // дни от 1970-01-01 в григорианскую дату (алгоритм Х. Хиннанта)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02}",
        rem / 3600,
        rem / 60 % 60,
        rem % 60
    )
}

impl Settings {
    pub fn load() -> Settings {
        let path = config_dir().join("shell.json");
        std::fs::read_to_string(path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    /// Записать атомарно: временный файл и переименование.
    pub fn save(&self) -> Result<(), String> {
        let dir = config_dir();
        std::fs::create_dir_all(&dir).map_err(|e| format!("Папка настроек {}: {e}", dir.display()))?;
        let text = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        let tmp = dir.join("shell.json.tmp");
        std::fs::write(&tmp, text).map_err(|e| format!("Настройки не записаны: {e}"))?;
        std::fs::rename(&tmp, dir.join("shell.json")).map_err(|e| format!("Настройки не записаны: {e}"))
    }

    pub fn home(&self) -> PathBuf {
        self.launch.home.clone().unwrap_or_else(default_home)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn launch_is_flat_in_json_and_missing_fields_are_defaults() {
        let s: Settings = serde_json::from_str(r#"{"home": "D:/data"}"#).unwrap();
        assert_eq!(s.launch.home, Some(PathBuf::from("D:/data")));
        assert!(s.launch.dev_source.is_none() && s.last_good.is_none());
        let text = serde_json::to_string(&s).unwrap();
        assert!(text.contains(r#""home":"D:/data""#), "{text}");
        assert!(serde_json::from_str::<Settings>("{}").is_ok());
    }
}
