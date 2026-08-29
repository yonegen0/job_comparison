@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

set "launcher_dir=%~dp0"
set "candidate_dir=%launcher_dir%"
set "candidate_exe=%candidate_dir%JobComparisonTool.exe"

if exist "%candidate_exe%" (
  if not exist "%candidate_dir%_internal\." goto incomplete_distribution
  goto launch
)

set "candidate_dir=%launcher_dir%release\current\"
set "candidate_exe=%candidate_dir%JobComparisonTool.exe"

if exist "%candidate_exe%" (
  if not exist "%candidate_dir%_internal\." goto incomplete_distribution
  goto launch
)

echo.
echo 求人比較ツールを起動できませんでした。
echo.
echo JobComparisonTool.exe が見つかりません。
echo 配布ZIPをすべて展開してから、もう一度実行してください。
echo.
echo 開発用フォルダーの場合は、配布担当者が
echo build_release.bat を実行して release\current を作成してください。
echo.
pause
exit /b 1

:incomplete_distribution
echo.
echo 求人比較ツールの配布ファイルが不足しています。
echo.
echo JobComparisonTool.exeだけを移動せず、
echo 配布ZIPをフォルダーごと展開し直してください。
echo.
pause
exit /b 1

:launch
start "" /D "%candidate_dir%" "%candidate_exe%"
if errorlevel 1 goto launch_failed
exit /b 0

:launch_failed
echo.
echo 求人比較ツールを起動できませんでした。
echo 配布ZIPをフォルダーごと展開し直してから、もう一度実行してください。
echo.
pause
exit /b 1
