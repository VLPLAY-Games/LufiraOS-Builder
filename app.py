#!/usr/bin/env python3
"""app.py — единая точка входа для однофайловой PyInstaller-сборки
(LufiraOS-Builder как один исполняемый файл для Linux, без исходников).

python3 build.py/gui.py по отдельности остаются основным способом работы
при запуске из исходников — этот файл нужен только как цель для
`pyinstaller --onefile`. Один и тот же бинарник ведёт себя по-разному в
зависимости от argv: если первый аргумент — известная подкоманда build.py
(build/run/debug/monitor/clear), работаем как build.py (так GUI дозванивается
сама до себя через sys.executable, когда frozen — см. gui.py _build_py_cmd());
иначе — как обычный запуск gui.py (двойной клик, без аргументов).
"""

import sys

_BUILD_PY_SUBCOMMANDS = {"build", "run", "debug", "monitor", "clear"}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in _BUILD_PY_SUBCOMMANDS:
        import build
        build.main()
    else:
        import gui
        gui.BuilderGUI().mainloop()
