$shell = New-Object -ComObject Shell.Application
$computer = $shell.NameSpace(0x11)
foreach($item in $computer.Items()) {
    Write-Host "$($item.Name) | Path: $($item.Path)"
}

# Also check for MTP devices
$shell = New-Object -ComObject Shell.Application
$mtp = $shell.NameSpace(17)
if ($mtp) {
    Write-Host "--- MTP Namespace ---"
    foreach($item in $mtp.Items()) {
        Write-Host "$($item.Name) | Path: $($item.Path)"
    }
}
