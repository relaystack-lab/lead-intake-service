param(
    [string]$PythonPath = ".\.venv\Scripts\python.exe"
)

& $PythonPath -m lead_intake.cli generate-fernet-key
