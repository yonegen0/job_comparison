# -*- mode: python ; coding: utf-8 -*-

analysis = Analysis(
    ["job_comparison_app/gui_entry.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("job_comparison_report/templates/report.html", "job_comparison_report/templates"),
        ("job_comparison_report/templates/report.css", "job_comparison_report/templates"),
    ],
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox", "pypdf"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="JobComparisonTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JobComparisonTool",
)
