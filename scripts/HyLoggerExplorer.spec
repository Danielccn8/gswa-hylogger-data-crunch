from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

root = Path(SPECPATH).parent
hidden = collect_submodules('hylogger') + collect_submodules('uvicorn')
hidden += ['psycopg_binary', 'psycopg_pool', 'tkinter', 'tkinter.messagebox']
hidden += ['backend.main', 'backend.legacy', 'db.migrate']
datas = [(str(root/'configs'), 'configs'), (str(root/'pyproject.toml'), '.'),
         (str(root/'platform/frontend'), 'platform/frontend'),
         (str(root/'platform/db/schema.sql'), 'platform/db'),
         (str(root/'platform/db/migrations'), 'platform/db/migrations'),
         (str(root/'build/desktop-resources/vendor'), 'vendor')]
for package in ['nvcl_kit','pytsg']:
    datas += collect_data_files(package)
    datas += copy_metadata(package)
a = Analysis([str(root/'scripts/desktop_entry.py')], pathex=[str(root/'src'),str(root/'platform')],
             binaries=[], datas=datas, hiddenimports=hidden,
             excludes=['IPython','jupyterlab','notebook','pytest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='HyLoggerExplorer',
          debug=False, strip=False, upx=False, console=False)
