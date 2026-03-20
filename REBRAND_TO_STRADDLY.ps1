# Comprehensive Rebranding Script: Straddly → Straddly
# This script updates all files, folders, and content

$replacements = @(
    # Exact phrase replacements (order matters - do specific before general)
    @{ Old = "Straddly"; New = "Straddly"; Description = "Title case" }
    @{ Old = "Straddly"; New = "straddly"; Description = "Lowercase phrase" }
    @{ Old = "Straddly"; New = "STRADDLY"; Description = "Uppercase phrase" }
    @{ Old = "straddly"; New = "straddly"; Description = "Kebab case" }
    @{ Old = "straddly"; New = "straddly"; Description = "Snake case" }
    @{ Old = "straddly\.pro"; New = "straddly.pro"; Description = "Domain" }
    @{ Old = "straddly"; New = "straddly"; Description = "Compact form" }
    @{ Old = "straddly"; New = "Straddly"; Description = "PascalCase" }
    @{ Old = "straddly"; New = "straddly"; Description = "camelCase" }
    @{ Old = "straddly"; New = "STRADDLY"; Description = "CONSTANT_CASE" }
    @{ Old = "straddly"; New = "straddly"; Description = "snake_case repeated" }
    @{ Old = "trading\.nexus"; New = "straddly"; Description = "Dotted format" }
    @{ Old = "com\.straddly"; New = "com.straddly"; Description = "Package name" }
    @{ Old = "trading\.nexus\.app"; New = "straddly.app"; Description = "App package" }
)

Write-Host "=== STRADDLY REBRANDING ===" -ForegroundColor Magenta
Write-Host "Starting comprehensive rebranding process..." -ForegroundColor Cyan
Write-Host ""

# Text file extensions to process
$textExts = @(".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml", ".sql", ".html", ".css", ".md", ".txt", ".env", ".sh", ".gradle", ".xml", ".toml", ".dockerfile", ".properties", ".cfg", ".conf", ".ini", ".dockerfile", ".ps1")

# Get all text files
$files = Get-ChildItem -Path "." -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
    $ext = $_.Extension.ToLower()
    $name = $_.Name.ToLower()
    
    # Include if matches extension patterns or specific file names
    ($ext -in $textExts) -or
    ($name -like "dockerfile*") -or
    ($name -like "docker-compose*") -or
    ($name -like ".gitignore*") -or
    ($name -like "package.json") -or
    ($name -like "*.lock") -or
    ($name -like "requirements*.txt") -or
    ($name -like "setup.py") -or
    ($name -like "tsconfig*") -or
    ($name -like ".env*") -or
    ($name -like "*.gradle")
}

$totalFiles = $files.Count
$processedCount = 0
$changedCount = 0

Write-Host "Processing $totalFiles files..." -ForegroundColor Yellow
Write-Host ""

foreach ($file in $files) {
    $processedCount++
    
    # Show progress
    if ($processedCount % 100 -eq 0) {
        Write-Host "[$processedCount/$totalFiles] Processing..." -NoNewline -ForegroundColor Gray
        Write-Host "`r" -NoNewline
    }
    
    try {
        $content = Get-Content $file.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($null -eq $content) { continue }
        
        $originalContent = $content
        
        # Apply replacements in order
        foreach ($replacement in $replacements) {
            # Use case-insensitive replace for most patterns
            $content = $content -ireplace [regex]::Escape($replacement.Old), $replacement.New
        }
        
        # Write back if changed
        if ($content -ne $originalContent) {
            Set-Content -Path $file.FullName -Value $content -Encoding UTF8 -NoNewline -ErrorAction SilentlyContinue
            $changedCount++
        }
    }
    catch {
        # Silently skip files that can't be processed
    }
}

Write-Host "`n`nPhase 1 Complete!" -ForegroundColor Green
Write-Host "Files processed: $processedCount" -ForegroundColor Green
Write-Host "Files changed: $changedCount" -ForegroundColor Green

# Phase 2: Rename directories
Write-Host "`nPhase 2: Renaming directories..." -ForegroundColor Cyan
$dirRenamePairs = @(
    @{ Old = "straddly"; New = "straddly" }
    @{ Old = "straddly"; New = "straddly" }
    @{ Old = "straddly"; New = "straddly" }
)

Get-ChildItem -Path "." -Recurse -Directory -ErrorAction SilentlyContinue | 
    Where-Object { $_.Name -match "trading" } | 
    ForEach-Object {
        foreach ($pair in $dirRenamePairs) {
            if ($_.Name -like "*$($pair.Old)*") {
                $newName = $_.Name -replace [regex]::Escape($pair.Old), $pair.New
                try {
                    Rename-Item -Path $_.FullName -NewName $newName -ErrorAction Stop
                    Write-Host "✓ Renamed directory: $($_.Name) → $newName" -ForegroundColor Green
                }
                catch {
                    # Skip if already renamed or locked
                }
                break
            }
        }
    }

# Phase 3: Rename files
Write-Host "`nPhase 3: Renaming files..." -ForegroundColor Cyan
$renamedFileCount = 0
Get-ChildItem -Path "." -Recurse -File -ErrorAction SilentlyContinue | 
    Where-Object { $_.Name -match "trading" } | 
    ForEach-Object {
        foreach ($pair in $dirRenamePairs) {
            if ($_.Name -like "*$($pair.Old)*") {
                $newName = $_.Name -replace [regex]::Escape($pair.Old), $pair.New
                try {
                    Rename-Item -Path $_.FullName -NewName $newName -ErrorAction Stop
                    Write-Host "✓ Renamed file: $($_.Name) → $newName" -ForegroundColor Green
                    $renamedFileCount++
                }
                catch {
                    # Skip if locked
                }
                break
            }
        }
    }

Write-Host "`nPhase 3 Complete!" -ForegroundColor Green
Write-Host "Files renamed: $renamedFileCount" -ForegroundColor Green

Write-Host "`n=== REBRANDING COMPLETE ===" -ForegroundColor Magenta
Write-Host "Content replacements: $changedCount files" -ForegroundColor Green
Write-Host "Files/directories renamed: $renamedFileCount files" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Verify all replacements look correct"
Write-Host "2. Test build/run processes"
Write-Host "3. Update git commit history if needed"
