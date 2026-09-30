# 奥奇桌宠的 Windows 气泡提醒（零依赖：只用 Windows PowerShell + WinForms NotifyIcon）。
#
# 为什么参数是 base64：脚本要保持纯 ASCII，中文文本从命令行传进来在 PS 5.1 下
# 极易乱码（脚本按 ANSI 解析），所以宿主把 UTF-8 文本 base64 后再传。
#
# 宿主调用示例（由 lib/pet-runtime.js 自动完成）：
#   powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass `
#     -File toast.ps1 -TitleB64 <b64> -MessageB64 <b64>
param(
    [Parameter(Mandatory = $true)][string]$TitleB64,
    [Parameter(Mandatory = $true)][string]$MessageB64,
    [int]$TimeoutMs = 7000
)

$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $title = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($TitleB64))
    $message = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($MessageB64))

    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.BalloonTipTitle = $title
    $notify.BalloonTipText = $message
    $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $notify.Visible = $true
    $notify.ShowBalloonTip(5000)
    Start-Sleep -Milliseconds $TimeoutMs
    $notify.Visible = $false
    $notify.Dispose()
    exit 0
} catch {
    # 通知失败不该影响宿主，静默退出即可
    exit 1
}
