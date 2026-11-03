# 清理占用 GPU 的孤儿换声/分离进程（Windows）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/cleanup_gpu_orphans.ps1 [-WhatIf]
param([switch]$WhatIf)
$procs = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq 'python.exe' -and (
    $_.CommandLine -like '*inference.py*' -or
    $_.CommandLine -like '*dsh_rvc2.py*train*' -or
    $_.CommandLine -like '*infer_cli.py*' -or
    $_.CommandLine -like '*multiprocessing*spawn_main*'
  )
}
if (-not $procs) { Write-Output 'no orphan python found'; exit 0 }
foreach ($p in $procs) {
  $line = $p.CommandLine.Substring(0, [Math]::Min(120, $p.CommandLine.Length))
  if ($WhatIf) { Write-Output ('would kill ' + $p.ProcessId + ' :: ' + $line) }
  else { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output ('killed ' + $p.ProcessId + ' :: ' + $line) }
}