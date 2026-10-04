//! Объект задания Windows (Job Object) для сервера и всех его процессов.
//!
//! * Закрылась оболочка (даже аварийно) — Windows завершает сервер, исполнители и тесты:
//!   «висящих» python.exe не остаётся.
//! * Каждому процессу — предел памяти (доля физической памяти). Расчёт на миллионах строк,
//!   которому не хватило памяти, получает ошибку памяти в своём исполнителе, а не подвешивает
//!   весь компьютер; сервер перезапускает исполнитель (ARCHITECTURE.md, раздел 9).

/// Какую долю физической памяти может занять один процесс.
pub const MEMORY_SHARE: f64 = 0.8;

#[cfg(windows)]
mod imp {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_PROCESS_MEMORY,
    };
    use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};

    pub struct Job(HANDLE);

    // Описатель объекта задания можно использовать из любого потока.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    pub fn total_memory() -> u64 {
        let mut m: MEMORYSTATUSEX = unsafe { std::mem::zeroed() };
        m.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
        if unsafe { GlobalMemoryStatusEx(&mut m) } == 0 {
            return 0;
        }
        m.ullTotalPhys
    }

    impl Job {
        pub fn new(memory_limit: u64) -> Result<Job, String> {
            let h = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if h.is_null() {
                return Err(format!("CreateJobObject: {}", std::io::Error::last_os_error()));
            }
            let job = Job(h);
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if memory_limit > 0 {
                info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY;
                info.ProcessMemoryLimit = memory_limit as usize;
            }
            let ok = unsafe {
                SetInformationJobObject(
                    job.0,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const core::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            };
            if ok == 0 {
                return Err(format!("SetInformationJobObject: {}", std::io::Error::last_os_error()));
            }
            Ok(job)
        }

        pub fn assign(&self, child: &std::process::Child) -> Result<(), String> {
            let ok = unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) };
            if ok == 0 {
                return Err(format!("AssignProcessToJobObject: {}", std::io::Error::last_os_error()));
            }
            Ok(())
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe { CloseHandle(self.0) };
        }
    }
}

#[cfg(not(windows))]
mod imp {
    /// Вне Windows объекта задания нет: сервер сам следит за оболочкой (`--parent`).
    pub struct Job;

    pub fn total_memory() -> u64 {
        0
    }

    impl Job {
        pub fn new(_memory_limit: u64) -> Result<Job, String> {
            Ok(Job)
        }

        pub fn assign(&self, _child: &std::process::Child) -> Result<(), String> {
            Ok(())
        }
    }
}

pub use imp::{total_memory, Job};

/// Объект задания с пределом памяти на процесс (`MEMORY_SHARE` физической памяти).
pub fn for_server() -> Result<Job, String> {
    let limit = (total_memory() as f64 * MEMORY_SHARE) as u64;
    Job::new(limit)
}
