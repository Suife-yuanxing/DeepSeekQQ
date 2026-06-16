$key = [Environment]::GetEnvironmentVariable('MINIMAX_API_KEY', 'User')
$env:MINIMAX_API_KEY = $key
$scripts = "$env:USERPROFILE\.agents\skills\council\scripts"
Set-Location $scripts
python council_call.py "$scripts\council-verified-20260616-002834.md" --mode=fast --models deepseek,minimax
