@echo off
rem yaan <command> runs the Yaan CLI inside the api container, from any folder (D143).
rem It joins the running api (exec), or starts a throwaway one when the stack is down (run --rm).
rem The same as ./yaan, for cmd and PowerShell.
setlocal
pushd "%~dp0"
rem The running api's container id, or nothing (findstr can't match Docker's LF-only lines).
set running=
for /f %%i in ('docker compose --profile app ps -q --status running api') do set running=%%i
if defined running (
  docker compose --profile app exec api yaan %*
) else (
  docker compose --profile app run --rm api yaan %*
)
set code=%errorlevel%
popd
exit /b %code%
