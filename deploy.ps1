# Copies journal.py to the device and drops the running tmux session, which
# ~/.profile then relaunches with the new code.
#
# Addressed by mDNS name rather than IP: the lease has moved twice already
# (.246 -> .8), and avahi-daemon on the device follows it. Override with
# $env:JOURNAL_HOST if mDNS is unavailable on some network.
$PI = if ($env:JOURNAL_HOST) { $env:JOURNAL_HOST } else { "walker@journal.local" }

# The Pi runs this file directly. A Windows editor that saves CRLF would break
# the shebang, so refuse to deploy a file with carriage returns in it.
if ((Get-Content journal.py -Raw) -match "`r") {
    Write-Host "journal.py has CRLF line endings - fix before deploying" -ForegroundColor Red
    exit 1
}

scp journal.py "${PI}:~/journal.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "copy failed - is the device on the network?" -ForegroundColor Red
    Write-Host "  try: ssh $PI hostname" -ForegroundColor DarkGray
    exit 1
}

ssh $PI "tmux kill-session -t journal 2>/dev/null; true"
Write-Host "deployed to $PI" -ForegroundColor Green

# Give autologin time to respawn the login shell, then confirm the app is back.
Start-Sleep -Seconds 12
$session = ssh $PI "tmux ls 2>&1"
if ($session -match "^journal:") {
    Write-Host "app relaunched - $session" -ForegroundColor Green
} else {
    Write-Host "app did NOT relaunch: $session" -ForegroundColor Yellow
    Write-Host "  check ~/.profile on the device; see device/README.md" -ForegroundColor DarkGray
    exit 1
}
