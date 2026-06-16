$key = [Environment]::GetEnvironmentVariable('MINIMAX_API_KEY', 'User')
$env:MINIMAX_API_KEY = $key
Set-Location "$env:USERPROFILE\.agents\skills\council\scripts"
python test_boundary.py
