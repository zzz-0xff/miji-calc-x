# start.ps1 —— 启动计算者-X 控制台（由 start.bat 调用）
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   计算者-X 控制台  +  华为手环心率" -ForegroundColor Cyan
Write-Host "   PC --USB COM3--> ESP32-S3 --BLE--> 玩具 + 手环" -ForegroundColor DarkCyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. python ----------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[错误] 找不到 python。请装 Python 3 并勾选 Add to PATH。" -ForegroundColor Red
    return
}
Write-Host "[1/5] python: $($py.Source)"

# ---------- 2. pyserial ----------
& python -c "import serial" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[提示] 正在安装 pyserial ..." -ForegroundColor Yellow
    & python -m pip install pyserial --quiet
    & python -c "import serial" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] pyserial 装不上，手动执行: python -m pip install pyserial" -ForegroundColor Red
        return
    }
}
Write-Host "[2/5] pyserial 就绪"

# ---------- 3. 串口（自动识别，别写死 COM 号）----------
$portLine = (& python findport.py 2>$null | Select-String -Pattern '^结果:' | Select-Object -First 1)
if ($portLine -and $portLine.ToString() -notmatch '未找到') {
    $found = ($portLine.ToString() -replace '^结果:\s*', '').Trim()
    Write-Host "[3/5] 网关串口: $found" -ForegroundColor Green
} else {
    $all = @()
    try { $all = [System.IO.Ports.SerialPort]::GetPortNames() } catch { }
    Write-Host "[3/5] [警告] 没识别到网关串口。当前系统串口: $($all -join ', ')" -ForegroundColor Yellow
    Write-Host "        面板会照常启动并每 3 秒自动重试 —— 插好 ESP32 就会自动接上。" -ForegroundColor DarkGray
}

# ---------- 4. 清理旧实例 ----------
Write-Host "[4/5] 清理旧实例 ..."
$killed = $false
if (Test-Path 'panel.pid') {
    $t = (Get-Content 'panel.pid' -Raw).Trim()
    if ($t -match '^\d+$') {
        $p = Get-Process -Id ([int]$t) -ErrorAction SilentlyContinue
        if ($p) {
            Stop-Process -Id $p.Id -Force
            Write-Host "   已停掉旧面板 PID $t"
            $killed = $true
            Start-Sleep -Milliseconds 800
        }
    }
    Remove-Item 'panel.pid' -Force -ErrorAction SilentlyContinue
}
Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -like 'python*') {
        Stop-Process -Id $p.Id -Force
        Write-Host "   已停掉占用 8090 的 PID $($p.Id)"
        $killed = $true
    }
}
if (-not $killed) { Write-Host "   没有正在运行的实例" }

# ---------- 5. 启动面板 ----------
Write-Host "[5/5] 启动控制台 ..."
Remove-Item 'panel.log','panel.err' -Force -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath 'python' -ArgumentList 'toy_panel.py' -PassThru `
    -WindowStyle Minimized -RedirectStandardOutput 'panel.log' -RedirectStandardError 'panel.err'
$proc.Id | Out-File 'panel.pid' -Encoding ascii -NoNewline
Write-Host "   已启动，PID = $($proc.Id)   日志: panel.log"

# ---------- 等端口 + 开浏览器 ----------
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    if (Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue) { $ok = $true; break }
    Start-Sleep -Milliseconds 300
}
if ($ok) {
    Start-Process 'http://127.0.0.1:8090'
    Write-Host "   页面已打开: http://127.0.0.1:8090" -ForegroundColor Green
} else {
    Write-Host "   [错误] 8090 没起来，看 panel.err:" -ForegroundColor Red
    if (Test-Path 'panel.err') { Get-Content 'panel.err' -Tail 20 }
}

Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host "   停止: stop.bat      状态: status.bat      急停: panic.bat"
Write-Host "------------------------------------------------------------"
