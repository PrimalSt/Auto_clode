"""``python -m autogenerator.server`` — см. ``serve.py``.

Здесь ничего не импортируется на верхнем уровне: процессы-исполнители (spawn) загружают этот
модуль заново, и им не нужны FastAPI и uvicorn.
"""

if __name__ == "__main__":
    import sys

    from autogenerator.server.serve import main

    sys.exit(main())
