# status.ps1 —— 查看状态（由 status.bat 调用）
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   计算者-X 状态" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 服务进程 ----------
Write-Host "--- 服务进程 ---" -ForegroundColor DarkCyan
$pidFile = if (Test-Path 'panel.pid') { (Get-Content 'panel.pid' -Raw).Trim() } else { $null }
if ($pidFile) {
    $p = Get-Process -Id ([int]$pidFile) -ErrorAction SilentlyContinue
    if ($p) { Write-Host "  panel.pid = $pidFile  (运行中，内存 $([math]::Round($p.WorkingSet64/1MB,1)) MB)" }
    else    { Write-Host "  panel.pid = $pidFile  (已退出)" -ForegroundColor Yellow }
} else {
    Write-Host "  panel.pid = 无"
}
$listeners = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    foreach ($l in $listeners) { Write-Host "  监听: $($l.LocalAddress):$($l.LocalPort)  PID=$($l.OwningProcess)" }
} else {
    Write-Host "  监听: 无" -ForegroundColor Yellow
}

# ---------- 串口 ----------
Write-Host ""
Write-Host "--- 串口 ---" -ForegroundColor DarkCyan
try {
    $ports = [System.IO.Ports.SerialPort]::GetPortNames()
    Write-Host "  可用串口: $($ports -join ', ')"
} catch {
    Write-Host "  可用串口: 查询失败"
}
if ((Test-Path 'panel.err') -and ((Get-Item 'panel.err').Length -gt 0)) {
    Write-Host "  panel.err 非空:" -ForegroundColor Yellow
    Get-Content 'panel.err' -Tail 8 -Encoding UTF8 | ForEach-Object { Write-Host "    $_" }
}

# ---------- 玩具 + 心率 ----------
Write-Host ""
Write-Host "--- 玩具 + 心率 ---" -ForegroundColor DarkCyan
try {
    $s = Invoke-RestMethod -Uri 'http://127.0.0.1:8090/api/status' -TimeoutSec 5
    $toy = if ($s.toy_connected) { '是' } else { '否' }
    $band = if ($s.band_connected) { '是' } else { '否' }
    Write-Host "  玩具连接 : $toy"
    Write-Host "  手环连接 : $band"
    Write-Host "  当前心率 : $($s.hr) bpm   ($($s.hr_age) 秒前更新)" -ForegroundColor Magenta
    if ($s.running) {
        Write-Host "  运行状态 : 运行中  伸缩=$($s.telescopic) 震动=$($s.vibration) 还剩 $($s.remain)s" -ForegroundColor Green
    } else {
        Write-Host "  运行状态 : 空闲"
    }
    Write-Host "  当前帧   : $($s.frame)"
    Write-Host "  累计发帧 : $($s.sent)"
    if ($s.hr_history.Count -gt 0) {
        Write-Host "  心率序列 : $($s.hr_history -join ' ')"
    }
    Write-Host ""
    Write-Host "  最近日志:"
    $s.msgs | ForEach-Object { Write-Host "    $_" }
} catch {
    Write-Host "  面板没在跑，读不到。先双击 start.bat" -ForegroundColor Yellow
}

# ---------- 日志尾 ----------
Write-Host ""
Write-Host "--- panel.log 尾部 ---" -ForegroundColor DarkCyan
if (Test-Path 'panel.log') {
    Get-Content 'panel.log' -Tail 10 -Encoding UTF8 | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Host "  panel.log 不存在"
}
