# level-test.ps1 —— 打开档位测试台（由 档位测试台.bat 调用）
#
# 做两件事：
#   1. 控制台（toy_panel.py）没在跑就拉起来 —— 它才是真正驱动设备的那个
#   2. 拉起档位测试台（level_test.py，网页在 8091）并打开浏览器

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = 'utf-8'
} catch { }

function Test-Port([int]$p) {
    @(Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue).Count -gt 0
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   计算者-X · 档位测试台" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ---- 1. 控制台 ----
if (Test-Port 8090) {
    Write-Host "  [1/2] 控制台已在运行 (8090)" -ForegroundColor Green
} else {
    Write-Host "  [1/2] 拉起控制台 (8090) ..." -ForegroundColor Yellow
    Start-Process -FilePath 'python' -ArgumentList '-u', 'toy_panel.py' `
        -WorkingDirectory $root -WindowStyle Minimized
    $ok = $false
    foreach ($i in 1..30) {
        Start-Sleep -Milliseconds 700
        if (Test-Port 8090) { $ok = $true; break }
    }
    if ($ok) {
        Write-Host "        控制台已就绪" -ForegroundColor Green
        Start-Sleep -Seconds 3      # 等它把玩具连上
    } else {
        Write-Host "        控制台还没起来 —— ESP32 插好了吗？页面照常能开。" -ForegroundColor Yellow
    }
}

# ---- 2. 测试台 ----
if (Test-Port 8091) {
    Write-Host "  [2/2] 测试台已在运行 (8091)" -ForegroundColor Green
} else {
    Write-Host "  [2/2] 拉起测试台 (8091) ..." -ForegroundColor Yellow
    Start-Process -FilePath 'python' -ArgumentList '-u', 'level_test.py' `
        -WorkingDirectory $root -WindowStyle Minimized
    $ok = $false
    foreach ($i in 1..20) {
        Start-Sleep -Milliseconds 500
        if (Test-Port 8091) { $ok = $true; break }
    }
    if ($ok) { Write-Host "        测试台已就绪" -ForegroundColor Green }
    else { Write-Host "        测试台启动失败，手动跑 python level_test.py 看报错" -ForegroundColor Red }
}

$url = 'http://127.0.0.1:8091'
Write-Host ""
Write-Host "  地址： $url" -ForegroundColor Cyan
Write-Host "  关掉那两个最小化的窗口即停止" -ForegroundColor DarkGray
Write-Host ""
Start-Process $url
Start-Sleep -Seconds 2
