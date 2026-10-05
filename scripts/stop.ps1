# stop.ps1 —— 停止控制台（由 stop.bat 调用）
# 关键：设备会锁存最后一条指令，必须先下发全 0 帧，再杀进程
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = 'utf-8'
} catch { }

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   停止控制台" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. 先让玩具停 ----------
Write-Host "[1/3] 先给玩具下发全 0 帧 ..."
$sentViaHttp = $false
try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:8090/api/stop' -Method Post -TimeoutSec 5 | Out-Null
    Write-Host "   已通过面板通道下发" -ForegroundColor Green
    $sentViaHttp = $true
} catch {
    Write-Host "   面板没在跑"
}

# ---------- 2. 杀进程 ----------
Write-Host "[2/3] 停止面板进程 ..."
$stopped = $false
if (Test-Path 'panel.pid') {
    $t = (Get-Content 'panel.pid' -Raw).Trim()
    if ($t -match '^\d+$') {
        $p = Get-Process -Id ([int]$t) -ErrorAction SilentlyContinue
        if ($p) {
            Stop-Process -Id $p.Id -Force
            Write-Host "   已停止 PID $t"
            $stopped = $true
        }
    }
    Remove-Item 'panel.pid' -Force -ErrorAction SilentlyContinue
}
Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -like 'python*') {
        Stop-Process -Id $p.Id -Force
        Write-Host "   已停止占用 8090 的 PID $($p.Id)"
        $stopped = $true
    }
}
if (-not $stopped) { Write-Host "   没有正在运行的面板" }

# ---------- 3. 兜底：直连串口再发一次 ----------
Write-Host "[3/3] 兜底全停（直连串口）..."
Start-Sleep -Milliseconds 800
& python toy_ctl.py stop 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   已直连串口补发全 0 帧" -ForegroundColor Green
} elseif ($sentViaHttp) {
    Write-Host "   串口被占用，但第 1 步已经发过了" -ForegroundColor DarkGray
} else {
    Write-Host "   串口不可用，停止未确认" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "已停止。" -ForegroundColor Green
