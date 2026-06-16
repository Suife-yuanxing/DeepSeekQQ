$key = [Environment]::GetEnvironmentVariable('MINIMAX_API_KEY', 'User')
$env:MINIMAX_API_KEY = $key

$models = @("MiniMax-M1", "abab6.5s-chat", "minimax-m1", "abab6s-chat", "MiniMax-Text-01")
$url = "https://api.minimaxi.com/v1/chat/completions"

foreach ($m in $models) {
    $body = @{
        model = $m
        messages = @(@{role = "user"; content = "Hi"})
        max_tokens = 10
    } | ConvertTo-Json -Depth 3
    try {
        $r = Invoke-RestMethod -Uri $url -Method Post -Headers @{
            "Authorization" = "Bearer $key"
            "Content-Type" = "application/json"
        } -Body $body -TimeoutSec 15
        Write-Host "✅ $m — OK"
    } catch {
        $msg = $_.ErrorDetails.Message | ConvertFrom-Json | Select-Object -ExpandProperty error | Select-Object -ExpandProperty message
        Write-Host "❌ $m — $msg"
    }
}
