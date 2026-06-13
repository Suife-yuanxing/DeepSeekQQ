chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$shell = New-Object -ComObject Shell.Application

# Navigate to OnePlus phone
$computer = $shell.NameSpace(0x11)
$phone = $null
foreach($item in $computer.Items()) {
    if ($item.Name.Contains("Ace") -or $item.Name.Contains("一加")) {
        $phone = $item
        Write-Host "Found phone: $($item.Name)"
        break
    }
}

if (-not $phone) {
    Write-Host "Phone not found, listing all devices:"
    foreach($item in $computer.Items()) {
        Write-Host "  $($item.Name)"
    }
    exit 1
}

# Navigate into phone
$phoneFolder = $shell.NameSpace($phone.Path)
Write-Host "Phone folder items:"
foreach($item in $phoneFolder.Items()) {
    Write-Host "  [$($item.Name)] IsFolder=$($item.IsFolder)"
}

# Get internal storage - it's the only folder
$internal = $null
foreach($item in $phoneFolder.Items()) {
    if ($item.IsFolder) {
        $internal = $item
        break
    }
}

if (-not $internal) {
    Write-Host "No subfolder found"
    exit 1
}

Write-Host "`nAccessing: $($internal.Name)"
$internalNS = $shell.NameSpace($internal.Path)

Write-Host "Internal storage items:"
foreach($item in $internalNS.Items()) {
    Write-Host "  [$($item.Name)] IsFolder=$($item.IsFolder)"
}

# Find Pictures
$pictures = $null
foreach($item in $internalNS.Items()) {
    if ($item.Name -like "*Picture*" -or $item.Name -like "*图片*") {
        $pictures = $item
        Write-Host "`nFound Pictures: $($item.Name)"
        break
    }
}

if (-not $pictures) {
    Write-Host "Pictures not found"
    exit 1
}

# Navigate to Pictures
$picsNS = $shell.NameSpace($pictures.Path)
Write-Host "`nPictures contents:"
foreach($item in $picsNS.Items()) {
    Write-Host "  [$($item.Name)] IsFolder=$($item.IsFolder)"
}

# Find Screenshots
$screenshots = $null
foreach($item in $picsNS.Items()) {
    if ($item.Name -like "*Screenshot*" -or $item.Name -like "*截图*" -or $item.Name -like "*screen*") {
        $screenshots = $item
        Write-Host "`nFound Screenshots: $($item.Name)"
        break
    }
}

if (-not $screenshots) {
    Write-Host "Screenshots not found"
    exit 1
}

# List screenshots
$ssNS = $shell.NameSpace($screenshots.Path)
Write-Host "`nScreenshots contents ($($ssNS.Items().Count) items):"
foreach($item in $ssNS.Items()) {
    $sizeKB = [math]::Round($item.ExtendedProperty("System.Size") / 1024, 1)
    Write-Host "  $($item.Name) - ${sizeKB}KB"
}
