<#
  Регистрирует задачи Планировщика заданий Windows для digest_bot (папка \AIDigestBot).

    fetch            каждые 30 минут
    moderate         каждую минуту
    publish          в слоты из config.yaml -> posting.slots (по умолчанию 10:00, 14:00, 19:00)
    ads-check        раз в час
    collect-metrics  раз в день в 23:50 (внутри — sync-tracker); вечером, чтобы «постов за день» было полным
    weekly-report    по понедельникам в 09:00

  Запуск: двойной клик по install_tasks.bat (сам запросит права администратора)
  или:   powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1 [-DryRun] [-Uninstall]

  Скрипт можно запускать повторно — задачи перезаписываются. Поменяли posting.slots в
  config.yaml? Запустите скрипт ещё раз.
  Задачи работают от вашей учётной записи в режиме «независимо от входа в систему»
  (S4U, без хранения пароля, без окон) и переживают перезагрузку.
#>
param(
    [switch]$DryRun,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runner = Join-Path $Root 'scripts\run_task.bat'
$Python = Join-Path $Root 'venv\Scripts\python.exe'
$TaskPath = '\AIDigestBot\'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $DryRun -and -not (Test-Admin)) {
    Write-Host 'Нужны права администратора — перезапускаю с повышением...'
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($Uninstall) { $argList += '-Uninstall' }
    Start-Process powershell -Verb RunAs -ArgumentList $argList -Wait
    exit
}

if ($Uninstall) {
    Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    Write-Host 'Задачи \AIDigestBot удалены.'
    exit
}

foreach ($f in @($Runner, $Python)) {
    if (-not (Test-Path $f)) { throw "Не найден $f — сначала создайте venv и поставьте зависимости (шаг 1)." }
}

# --- слоты публикации из config.yaml (yaml читает Python из venv) ---
$slotsRaw = & $Python -c "import sys; sys.path.insert(0, r'$Root'); from digest_bot.config import load_config; print(' '.join(load_config().posting_slots))"
if ($LASTEXITCODE -ne 0 -or -not $slotsRaw) { throw 'Не удалось прочитать posting.slots из config.yaml' }
$slots = @($slotsRaw.Trim() -split '\s+')
foreach ($s in $slots) {
    if ($s -notmatch '^([01]?\d|2[0-3]):[0-5]\d$') { throw "Некорректный слот '$s' в posting.slots (нужно HH:MM)" }
}

$midnight = (Get-Date).Date
$forever = New-TimeSpan -Days 3650

function New-Repeating([int]$minutes) {
    New-ScheduledTaskTrigger -Once -At $midnight -RepetitionInterval (New-TimeSpan -Minutes $minutes) -RepetitionDuration $forever
}

$tasks = [ordered]@{
    'fetch'           = @(New-Repeating 30)
    'moderate'        = @(New-Repeating 1)
    'publish'         = @($slots | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
    'ads-check'       = @(New-Repeating 60)
    'collect-metrics' = @(New-ScheduledTaskTrigger -Daily -At '23:50')
    'weekly-report'   = @(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At '09:00')
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Write-Host "Проект: $Root"
Write-Host "Слоты публикации: $($slots -join ', ')"
Write-Host "Пользователь задач: $user"
Write-Host ''

foreach ($name in $tasks.Keys) {
    $action = New-ScheduledTaskAction -Execute $Runner -Argument $name -WorkingDirectory $Root
    if ($DryRun) {
        Write-Host ("[dry-run] {0,-16} триггеров: {1}" -f $name, @($tasks[$name]).Count)
        continue
    }
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $name -Action $action -Trigger $tasks[$name] `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host ("[ok]      {0,-16} зарегистрирована" -f $name)
}

if ($DryRun) { Write-Host "`nDry-run: ничего не зарегистрировано."; exit }

# --- проверка: реальный прогон самой безобидной задачи (moderate только читает апдейты) ---
Write-Host "`nПробный запуск moderate..."
Start-ScheduledTask -TaskPath $TaskPath -TaskName 'moderate'
Start-Sleep -Seconds 8
$info = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName 'moderate'
if ($info.LastTaskResult -eq 0) {
    Write-Host 'Пробный запуск OK (код 0). Логи: logs\<команда>.log'
} else {
    Write-Host ("Пробный запуск вернул код {0}. Смотрите logs\moderate.log" -f $info.LastTaskResult) -ForegroundColor Yellow
}
Write-Host "`nСписок задач: Get-ScheduledTask -TaskPath '$TaskPath' | Get-ScheduledTaskInfo"
