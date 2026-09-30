# 用无头 Edge 把 test/client-ui-preview.html 渲染成 docs/in-app-dock.png。
#
# 为什么要有这个脚本：界面内小宠物要 DSH 重启才会加载，做不到时就用本地 harness
# 在真 Chromium 里跑同一份 lib/client.js，至少把「组件代码 + 主题 token 渲染对不对」验掉。
#
# 踩过的两个坑，都写在这儿免得再踩：
#   1. 别加 --virtual-time-budget：页面里组件有 setInterval 轮询，配合虚拟时间会让
#      无头 Chromium 一直不退出（实测挂死 60s+）。
#   2. 出问题要清理时只杀 `--headless` 的主进程（taskkill /T 连子进程），
#      绝不要按名字杀 msedge.exe —— 那会把你正在用的浏览器一起关掉。
#
# 用法： pwsh -File tools/shoot_client_ui.ps1
param(
  [string]$Out = "",
  [int]$Width = 1000,
  [int]$Height = 430,
  [int]$TimeoutSec = 45
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Out) { $Out = Join-Path $root "docs\in-app-dock.png" }
$page = Join-Path $root "test\client-ui-preview.html"
if (-not (Test-Path $page)) { throw "找不到 $page" }

$candidates = @(
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "没找到 Edge/Chrome，无法渲染" }

$profile = Join-Path $env:TEMP ("aoqi-preview-" + (Get-Random))
if (Test-Path $Out) { Remove-Item $Out -Force }
$url = "file:///" + ($page -replace '\\', '/')
$arguments = @(
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  '--no-default-browser-check', '--disable-extensions',
  "--user-data-dir=$profile", "--window-size=$Width,$Height",
  "--screenshot=$Out", $url
)
$process = Start-Process -FilePath $browser -ArgumentList $arguments -PassThru
$exited = $process.WaitForExit($TimeoutSec * 1000)
if (-not $exited) {
  Write-Output "浏览器 $TimeoutSec 秒没退出，强杀 pid=$($process.Id)"
  taskkill /PID $process.Id /T /F 2>&1 | Out-Null
}
Start-Sleep -Milliseconds 600
Remove-Item $profile -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path $Out) {
  Write-Output "已生成 $Out（$((Get-Item $Out).Length) 字节）"
} else {
  Write-Output "渲染失败：$Out 没生成"
  exit 1
}
