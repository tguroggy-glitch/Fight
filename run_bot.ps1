$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "-u","main.py" `
    -RedirectStandardOutput "bot_run.log" -RedirectStandardError "bot_run_err.log" `
    -WindowStyle Hidden -PassThru
$p.Id
