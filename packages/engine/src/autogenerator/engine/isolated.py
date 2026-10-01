"""Отдельный процесс для пользовательского кода (ARCHITECTURE.md, разделы 5.1 и 6.4).

Родитель записывает задание (код, пути к Parquet, контекст) в JSON и запускает
``python -m autogenerator.engine.isolated задание.json`` с таймаутом и лимитом памяти.
Бесконечный цикл, нехватка памяти или падение интерпретатора завершают только этот
процесс: узел получает ошибку, остальной сценарий считается дальше. Настройки pandas и
Polars, которые поменял код, тоже остаются в этом процессе.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from autogenerator.contracts import AgenError, DateSpan, ErrorCode, Period

from .resources import gb, run_limited, set_own_memory_limit


def _env() -> dict[str, str]:
    env = dict(os.environ)
    paths = [p for p in sys.path if p and Path(p).exists()]
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_job(job: dict[str, Any], job_path: Path, *, timeout: float | None, memory: int | None) -> dict[str, Any]:
    """Выполнить задание в отдельном процессе и вернуть его итог (``ok``, ``logs``,
    ``warnings``, ``rows`` или ``value``). Ошибка кода — ``AgenError`` с номером строки."""
    job = {**job, "memory": memory}
    result_path = job_path.with_suffix(".result.json")
    job["result"] = str(result_path)
    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps(job, ensure_ascii=False, default=str), encoding="utf-8")
    result_path.unlink(missing_ok=True)
    code, stderr = run_limited(
        [sys.executable, "-m", "autogenerator.engine.isolated", str(job_path)],
        timeout=timeout,
        memory=memory,
        env=_env(),
    )
    if code is None:
        raise AgenError(
            ErrorCode.TIMEOUT,
            f"код работал дольше {timeout:.0f} с и был остановлен",
            hint="Проверьте циклы в коде или увеличьте timeout у шага.",
        )
    if result_path.exists():
        res: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
        if not res.get("ok"):
            err = res.get("error") or {}
            raise AgenError(
                ErrorCode.USER_CODE,
                err.get("message") or "ошибка в коде",
                details={
                    "line": err.get("line"),
                    "traceback": err.get("traceback", ""),
                    "logs": res.get("logs") or [],
                    "warnings": res.get("warnings") or [],
                },
                hint=err.get("hint"),
            )
        return res
    tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
    memory_text = f" Лимит памяти процесса — {gb(memory)}." if memory else ""
    if "MemoryError" in stderr or "memory allocation" in stderr.lower() or (code != 0 and not tail):
        raise AgenError(
            ErrorCode.USER_CODE,
            f"процесс с кодом завершился: не хватило памяти.{memory_text}",
            hint="Попробуйте режим lazy или batches, или сузьте окно данных.",
        )
    raise AgenError(ErrorCode.USER_CODE, f"процесс с кодом завершился с кодом {code}: {tail}{memory_text}")


# --- сторона дочернего процесса ----------------------------------------------------------


def _context(data: dict[str, Any]) -> Any:
    from .usercode import UserContext

    windows = {k: DateSpan.model_validate(v) for k, v in (data.get("windows") or {}).items()}
    return UserContext(
        period=Period.model_validate(data["period"]),
        anchor=date.fromisoformat(data["anchor"]),
        windows=windows,
        columns=data.get("columns") or {},
        node=data.get("node", ""),
    )


def _child(job: dict[str, Any]) -> dict[str, Any]:
    from . import usercode as uc

    ctx = _context(job["context"])
    logs: list[str] = []
    out: dict[str, Any] = {"ok": False, "logs": logs, "warnings": ctx.warnings}
    try:
        mode = job["mode"]
        node = job["node"]
        frame = job.get("frame", "pandas")
        ptypes = job.get("pandas_types", "numpy")
        if mode == "table":
            out["rows"] = uc.run_table(
                Path(job["input"]), Path(job["output"]), job["code"], node, ctx, frame, ptypes, logs
            )
        elif mode == "batches":
            out["rows"] = uc.run_batches(
                Path(job["input"]), Path(job["output"]), job["code"], node, ctx, frame, ptypes, logs
            )
        elif mode in ("build", "value"):
            tables = {k: Path(v) for k, v in job["tables"].items()}
            target = Path(job["output"]) if job.get("output") else None
            res = uc.run_build(tables, target, job["code"], node, ctx, frame, ptypes, mode, logs)
            out["value" if mode == "value" else "rows"] = res
        else:
            raise AgenError(ErrorCode.USER_CODE, f"неизвестный режим {mode}")
        out["ok"] = True
    except uc.UserCodeError as e:
        out["error"] = {"message": e.message, "line": e.line, "traceback": e.trace}
    except AgenError as e:
        out["error"] = {"message": e.message, "hint": e.hint}
    except MemoryError:
        out["error"] = {
            "message": "не хватило памяти",
            "hint": "Попробуйте режим lazy или batches, или сузьте окно данных.",
        }
    except Exception as e:
        out["error"] = {"message": f"{type(e).__name__}: {e}"}
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    job_path = Path(args[0])
    job = json.loads(job_path.read_text(encoding="utf-8"))
    set_own_memory_limit(job.get("memory"))
    out = _child(job)
    Path(job["result"]).write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
