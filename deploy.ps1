# Copies journal.py to the device and drops the running tmux session.
# Adjust $PI if the device address changes.
$PI = "walker@192.168.1.246"

# The Pi runs this file directly. A Windows editor that saves CRLF would break
# the shebang, so refuse to deploy a file with carriage returns in it.
if ((Get-Content journal.py -Raw) -match "`r") {
    Write-Host "journal.py has CRLF line endings - fix before deploying" -ForegroundColor Red
    exit 1
}

scp journal.py "${PI}:~/journal.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "copy failed" -ForegroundColor Red
    exit 1
}

ssh $PI "tmux kill-session -t journal 2>/dev/null; true"
Write-Host "deployed - tmux session dropped" -ForegroundColor Green
Write-Host "whether the app relaunches by itself depends on ~/.profile using exec; see docs/STATE.md" -ForegroundColor DarkGray
