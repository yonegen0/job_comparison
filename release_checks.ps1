param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseDirectory,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9]+\.[0-9]+\.[0-9]+$")]
    [string]$ExpectedVersion,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedZip
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$sourceDirectory = [System.IO.Path]::GetFullPath(
    $(if ([System.IO.Path]::IsPathRooted($ReleaseDirectory)) {
        $ReleaseDirectory
    } else {
        Join-Path $repoRoot $ReleaseDirectory
    })
)
$expectedZipPath = [System.IO.Path]::GetFullPath(
    $(if ([System.IO.Path]::IsPathRooted($ExpectedZip)) {
        $ExpectedZip
    } else {
        Join-Path $repoRoot $ExpectedZip
    })
)
$releaseRoot = Split-Path -Parent $expectedZipPath
$expectedPackageName = "JobComparisonTool-$ExpectedVersion-win-x64"
$expectedZipName = "$expectedPackageName.zip"
$finalChecksumName = "SHA256SUMS-$ExpectedVersion.txt"
$finalChecksumPath = Join-Path $releaseRoot $finalChecksumName
$currentDirectory = Join-Path $releaseRoot "current"
$archiveRoot = Join-Path $releaseRoot "archive"
$guideSource = Join-Path $repoRoot "docs\user-guide"
$guideDirectoryName = "使い方ガイド"
$guideMarkdownName = "求人比較ツール_使い方.md"
$guideImageNames = @(
    "00-workflow-overview.png",
    "01-start-candidate.png",
    "02-candidate-example.png",
    "03-recruit-initial-setup.png",
    "04-recruit-ready.png",
    "05-replace-confirmation.png",
    "06-general-comparison-confirmation.png",
    "07-candidate-analysis-complete.png",
    "08-pdf-organize-preview.png",
    "09-pdf-text-preview.png",
    "10-report-preview.png",
    "11-pdf-copy-preview.png"
)

if ((Split-Path -Leaf $expectedZipPath) -cne $expectedZipName) {
    throw "ExpectedZip must end with $expectedZipName."
}
if ($releaseRoot -ine (Join-Path $repoRoot "release")) {
    throw "ExpectedZip must be directly under the repository release directory."
}
if ($sourceDirectory -ine (Join-Path $repoRoot "dist\JobComparisonTool")) {
    throw "ReleaseDirectory must be the PyInstaller onedir output."
}

function Assert-NormalFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer) {
        throw "Expected a normal file: $($item.Name)"
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reparse points are not allowed in a release: $($item.Name)"
    }
}

function Assert-NormalDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) {
        throw "Expected a directory: $($item.Name)"
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reparse points are not allowed in a release: $($item.Name)"
    }
}

function Get-PngChunkTypes {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $types = New-Object System.Collections.ArrayList
    $offset = 8
    while ($offset -lt $Bytes.Length) {
        if ($offset + 12 -gt $Bytes.Length) {
            throw "PNG chunk is truncated."
        }
        [uint32]$length =
            ([uint32]$Bytes[$offset] -shl 24) -bor
            ([uint32]$Bytes[$offset + 1] -shl 16) -bor
            ([uint32]$Bytes[$offset + 2] -shl 8) -bor
            [uint32]$Bytes[$offset + 3]
        $type = [System.Text.Encoding]::ASCII.GetString($Bytes, $offset + 4, 4)
        [void]$types.Add($type)
        $offset += 12 + [int64]$length
        if ($offset -gt $Bytes.Length) {
            throw "PNG chunk extends beyond the file."
        }
        if ($type -ceq "IEND") {
            if ($offset -ne $Bytes.Length) {
                throw "PNG contains trailing bytes."
            }
            break
        }
    }
    return @($types)
}

function Assert-UserGuide {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-NormalDirectory -Path $Path
    $markdown = Join-Path $Path $guideMarkdownName
    $images = Join-Path $Path "images"
    Assert-NormalFile -Path $markdown
    Assert-NormalDirectory -Path $images

    $expectedGuideItems = @($guideMarkdownName, "images") | Sort-Object
    $actualGuideItems = @(Get-ChildItem -LiteralPath $Path -Force | ForEach-Object Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject $expectedGuideItems -DifferenceObject $actualGuideItems).Count -ne 0) {
        throw "The user-guide root allowlist does not match."
    }
    $expectedImages = @($guideImageNames | Sort-Object)
    $actualImages = @(Get-ChildItem -LiteralPath $images -Force | ForEach-Object Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject $expectedImages -DifferenceObject $actualImages).Count -ne 0) {
        throw "The user-guide image allowlist does not match."
    }

    $markdownBytes = [System.IO.File]::ReadAllBytes($markdown)
    if ($markdownBytes.Length -eq 0 -or $markdownBytes.Length -gt 307200) {
        throw "The user-guide Markdown size is invalid."
    }
    if ($markdownBytes.Length -ge 3 -and
        $markdownBytes[0] -eq 0xEF -and
        $markdownBytes[1] -eq 0xBB -and
        $markdownBytes[2] -eq 0xBF) {
        throw "The user-guide Markdown must not contain a UTF-8 BOM."
    }
    $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $markdownText = $strictUtf8.GetString($markdownBytes)
    if (-not $markdownText.StartsWith("# 求人比較ツール 使い方ガイド`n") -or
        -not $markdownText.Contains("対象バージョン: $ExpectedVersion") -or
        -not $markdownText.EndsWith("`n") -or
        $markdownText.Contains("`r") -or
        $markdownText.Contains("C:\Users\")) {
        throw "The user-guide Markdown contract is invalid."
    }
    foreach ($name in $guideImageNames) {
        if (-not $markdownText.Contains("(images/$name)")) {
            throw "The user guide does not reference image: $name"
        }
    }

    [int64]$totalImageBytes = 0
    $pngSignature = [byte[]](137, 80, 78, 71, 13, 10, 26, 10)
    foreach ($name in $guideImageNames) {
        $image = Join-Path $images $name
        Assert-NormalFile -Path $image
        $bytes = [System.IO.File]::ReadAllBytes($image)
        if ($bytes.Length -le 24 -or $bytes.Length -gt 1048576) {
            throw "The user-guide PNG size is invalid: $name"
        }
        for ($index = 0; $index -lt $pngSignature.Length; $index++) {
            if ($bytes[$index] -ne $pngSignature[$index]) {
                throw "The user-guide PNG signature is invalid: $name"
            }
        }
        $chunkTypes = @(Get-PngChunkTypes -Bytes $bytes)
        if ($chunkTypes[0] -cne "IHDR" -or $chunkTypes[-1] -cne "IEND") {
            throw "The user-guide PNG structure is invalid: $name"
        }
        if (@($chunkTypes | Where-Object { $_ -in @("tEXt", "zTXt", "iTXt", "eXIf") }).Count -ne 0) {
            throw "The user-guide PNG contains metadata: $name"
        }
        $totalImageBytes += $bytes.Length
    }
    if ($totalImageBytes -gt 8388608) {
        throw "The user-guide PNG total size exceeds 8 MB."
    }
}

function Assert-Package {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-NormalDirectory -Path $Path
    $requiredTopLevel = @(
        "JobComparisonTool.exe",
        "求人比較ツールを起動.bat",
        "はじめにお読みください.txt",
        "SHA256SUMS.txt",
        "_internal",
        $guideDirectoryName
    ) | Sort-Object
    $actualTopLevel = @(Get-ChildItem -LiteralPath $Path -Force | ForEach-Object Name | Sort-Object)
    $difference = @(Compare-Object -ReferenceObject $requiredTopLevel -DifferenceObject $actualTopLevel)
    if ($difference.Count -ne 0) {
        throw "The release top-level allowlist does not match."
    }

    $executable = Join-Path $Path "JobComparisonTool.exe"
    $launcher = Join-Path $Path "求人比較ツールを起動.bat"
    $readme = Join-Path $Path "はじめにお読みください.txt"
    $checksum = Join-Path $Path "SHA256SUMS.txt"
    $internal = Join-Path $Path "_internal"
    $guide = Join-Path $Path $guideDirectoryName
    foreach ($file in @($executable, $launcher, $readme, $checksum)) {
        Assert-NormalFile -Path $file
    }
    Assert-NormalDirectory -Path $internal
    Assert-UserGuide -Path $guide

    foreach ($name in @("_tcl_data", "_tk_data", "_tkinter.pyd", "tcl86t.dll", "tk86t.dll")) {
        if (-not (Test-Path -LiteralPath (Join-Path $internal $name))) {
            throw "Tk/Tcl runtime file is missing: $name"
        }
    }
    $cffiRuntime = @(Get-ChildItem -LiteralPath $internal -File -Filter "_cffi_backend*.pyd")
    if ($cffiRuntime.Count -eq 0) {
        throw "cffi runtime is missing: _cffi_backend.pyd"
    }
    $cryptographyRuntime = @(Get-ChildItem -LiteralPath $internal -Recurse -Force | Where-Object {
        (-not $_.PSIsContainer) -and $_.FullName -match 'cryptography' -and $_.Extension -in @('.pyd', '.dll')
    })
    if ($cryptographyRuntime.Count -eq 0) {
        throw "cryptography binding is missing."
    }
    $templateRoot = Join-Path $internal "job_comparison_report\templates"
    foreach ($name in @("report.html", "report.css")) {
        Assert-NormalFile -Path (Join-Path $templateRoot $name)
    }

    $allItems = @(Get-ChildItem -LiteralPath $Path -Recurse -Force)
    $reparseItems = @($allItems | Where-Object {
        ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    })
    if ($reparseItems.Count -ne 0) {
        throw "Reparse points were found in the release."
    }

    $bannedDirectoryNames = @(
        "reports", "outputs", "tests", "__pycache__", ".pytest_cache",
        ".venv", ".venv-build", "build", "dist", "release"
    )
    $allowedGuideMarkdown = Join-Path $guide $guideMarkdownName
    $allowedComparisonTemplate = Join-Path $internal "job_comparison_app\assets\pdf_comparison_guide.md"
    Assert-NormalFile -Path $allowedComparisonTemplate
    $allowedGuidePngs = @($guideImageNames | ForEach-Object { Join-Path (Join-Path $guide "images") $_ })
    $bannedFiles = @($allItems | Where-Object {
        (-not $_.PSIsContainer) -and (
            $_.Extension -in @(".py", ".pyc", ".pdf", ".csv", ".json", ".jsonl") -or
            ($_.Extension -eq ".md" -and $_.FullName -ine $allowedGuideMarkdown -and $_.FullName -ine $allowedComparisonTemplate) -or
            ($_.Extension -eq ".png" -and $_.FullName -notin $allowedGuidePngs) -or
            ($_.Extension -in @(".html", ".htm") -and $_.FullName -ine (Join-Path $templateRoot "report.html")) -or
            ($_.Extension -eq ".css" -and $_.FullName -ine (Join-Path $templateRoot "report.css"))
        )
    })
    $bannedDirectories = @($allItems | Where-Object {
        $_.PSIsContainer -and $_.Name -in $bannedDirectoryNames
    })
    if ($bannedFiles.Count -ne 0 -or $bannedDirectories.Count -ne 0) {
        throw "Source or user-data files were found in the release."
    }

    $checksumText = [System.IO.File]::ReadAllText($checksum, [System.Text.Encoding]::ASCII)
    $checksumMatch = [regex]::Match(
        $checksumText,
        "\A([0-9a-f]{64})  JobComparisonTool\.exe\n\z"
    )
    if (-not $checksumMatch.Success) {
        throw "SHA256SUMS.txt has an invalid format."
    }
    $actualHash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($checksumMatch.Groups[1].Value -cne $actualHash) {
        throw "JobComparisonTool.exe does not match SHA256SUMS.txt."
    }

    $launcherBytes = [System.IO.File]::ReadAllBytes($launcher)
    if ($launcherBytes.Length -ge 3 -and
        $launcherBytes[0] -eq 0xEF -and
        $launcherBytes[1] -eq 0xBB -and
        $launcherBytes[2] -eq 0xBF) {
        throw "The launcher BAT must not contain a UTF-8 BOM."
    }
    $launcherText = [System.Text.Encoding]::UTF8.GetString($launcherBytes)
    if ($launcherText -match "(?<!`r)`n") {
        throw "The launcher BAT must use CRLF line endings."
    }
    if (-not $launcherText.Contains("release\current") -or
        -not $launcherText.Contains("_internal") -or
        $launcherText.Contains("dist\JobComparisonTool")) {
        throw "The launcher BAT does not implement the approved lookup contract."
    }

    $readmeText = [System.IO.File]::ReadAllText($readme, [System.Text.Encoding]::UTF8)
    if (-not $readmeText.Contains("求人比較ツール $ExpectedVersion")) {
        throw "The bundled readme version does not match ExpectedVersion."
    }
    return $actualHash
}

function Move-WithReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$Receipts
    )
    if (Test-Path -LiteralPath $Destination) {
        throw "Archive destination already exists: $Destination"
    }
    Move-Item -LiteralPath $Source -Destination $Destination -ErrorAction Stop
    [void]$Receipts.Add([pscustomobject]@{ Source = $Source; Destination = $Destination })
}

function Remove-OwnedStage {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $parent = Split-Path -Parent $fullPath
    $leaf = Split-Path -Leaf $fullPath
    if ($parent -cne $releaseRoot -or $leaf -notmatch '^\.staging-[0-9a-f]{32}$') {
        throw "Refusing to remove a directory outside the owned release stage."
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force -ErrorAction Stop
    }
}

Assert-NormalDirectory -Path $sourceDirectory
Assert-NormalFile -Path (Join-Path $sourceDirectory "JobComparisonTool.exe")
Assert-NormalDirectory -Path (Join-Path $sourceDirectory "_internal")
Assert-UserGuide -Path $guideSource

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null
$failedBuildArchive = Join-Path $archiveRoot "failed-builds"
$staleStages = @(Get-ChildItem -LiteralPath $releaseRoot -Directory -Force | Where-Object {
    $_.Name -match '^\.staging-[0-9a-f]{32}$'
})
if ($staleStages.Count -gt 0) {
    New-Item -ItemType Directory -Path $failedBuildArchive -Force | Out-Null
    foreach ($staleStage in $staleStages) {
        if (($staleStage.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "A stale release stage is a reparse point and was not moved."
        }
        $failedDestination = Join-Path $failedBuildArchive $staleStage.Name
        if (Test-Path -LiteralPath $failedDestination) {
            throw "A failed-build archive with the same name already exists."
        }
        Move-Item -LiteralPath $staleStage.FullName -Destination $failedDestination -ErrorAction Stop
    }
}

$stageName = ".staging-$([guid]::NewGuid().ToString('N'))"
$stageRoot = Join-Path $releaseRoot $stageName
$packageDirectory = Join-Path $stageRoot "package"
$stagedZip = Join-Path $stageRoot $expectedZipName
$stagedFinalChecksum = Join-Path $stageRoot $finalChecksumName
$verificationRoot = Join-Path $stageRoot "verify"
$archiveReceipts = New-Object System.Collections.ArrayList
$publishedPaths = New-Object System.Collections.ArrayList
$originalError = $null

try {
    New-Item -ItemType Directory -Path $packageDirectory -ErrorAction Stop | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceDirectory "JobComparisonTool.exe") -Destination $packageDirectory
    Copy-Item -LiteralPath (Join-Path $sourceDirectory "_internal") -Destination $packageDirectory -Recurse
    Copy-Item -LiteralPath (Join-Path $repoRoot "求人比較ツールを起動.bat") -Destination $packageDirectory
    Copy-Item -LiteralPath (Join-Path $repoRoot "はじめにお読みください.txt") -Destination $packageDirectory
    Copy-Item -LiteralPath $guideSource -Destination (Join-Path $packageDirectory $guideDirectoryName) -Recurse

    $executable = Join-Path $packageDirectory "JobComparisonTool.exe"
    $executableHash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(
        (Join-Path $packageDirectory "SHA256SUMS.txt"),
        "$executableHash  JobComparisonTool.exe`n",
        [System.Text.Encoding]::ASCII
    )
    $validatedExecutableHash = Assert-Package -Path $packageDirectory

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $packageDirectory,
        $stagedZip,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )
    New-Item -ItemType Directory -Path $verificationRoot -ErrorAction Stop | Out-Null
    Expand-Archive -LiteralPath $stagedZip -DestinationPath $verificationRoot -Force
    $verifiedExecutableHash = Assert-Package -Path $verificationRoot
    if ($verifiedExecutableHash -cne $validatedExecutableHash) {
        throw "The ZIP executable differs from the staged executable."
    }

    $zipHash = (Get-FileHash -LiteralPath $stagedZip -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(
        $stagedFinalChecksum,
        "$zipHash  $expectedZipName`n",
        [System.Text.Encoding]::ASCII
    )

    $priorVersionArtifacts = @(Get-ChildItem -LiteralPath $releaseRoot -File -Force | Where-Object {
        ($_.Name -match '^JobComparisonTool-[0-9]+\.[0-9]+\.[0-9]+-win-x64\.zip$' -and
            $_.FullName -ine $expectedZipPath) -or
        ($_.Name -match '^SHA256SUMS-[0-9]+\.[0-9]+\.[0-9]+\.txt$' -and
            $_.FullName -ine $finalChecksumPath)
    } | ForEach-Object FullName)
    $replaceItems = @(
        @($currentDirectory, $expectedZipPath, $finalChecksumPath) |
            Where-Object { Test-Path -LiteralPath $_ }
    ) + $priorVersionArtifacts
    if ($replaceItems.Count -gt 0) {
        $suffix = [guid]::NewGuid().ToString("N").Substring(0, 8)
        $replacementArchive = Join-Path $archiveRoot ("replaced-" + (Get-Date -Format "yyyyMMdd_HHmmss") + "-" + $suffix)
        New-Item -ItemType Directory -Path $replacementArchive -ErrorAction Stop | Out-Null
        foreach ($replaceItem in $replaceItems) {
            Move-WithReceipt -Source $replaceItem -Destination (Join-Path $replacementArchive (Split-Path -Leaf $replaceItem)) -Receipts $archiveReceipts
        }
    }

    Move-Item -LiteralPath $packageDirectory -Destination $currentDirectory -ErrorAction Stop
    [void]$publishedPaths.Add($currentDirectory)
    Move-Item -LiteralPath $stagedZip -Destination $expectedZipPath -ErrorAction Stop
    [void]$publishedPaths.Add($expectedZipPath)
    Move-Item -LiteralPath $stagedFinalChecksum -Destination $finalChecksumPath -ErrorAction Stop
    [void]$publishedPaths.Add($finalChecksumPath)

    $finalExecutableHash = Assert-Package -Path $currentDirectory
    if ($finalExecutableHash -cne $validatedExecutableHash) {
        throw "The published executable differs from the validated executable."
    }
    $publishedZipHash = (Get-FileHash -LiteralPath $expectedZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($publishedZipHash -cne $zipHash) {
        throw "The published ZIP differs from the validated ZIP."
    }
    $publishedChecksum = [System.IO.File]::ReadAllText($finalChecksumPath, [System.Text.Encoding]::ASCII)
    if ($publishedChecksum -cne "$zipHash  $expectedZipName`n") {
        throw "The published ZIP checksum is invalid."
    }

    Remove-OwnedStage -Path $stageRoot
    Write-Output "Release validation and publication succeeded: $ExpectedVersion"
}
catch {
    $originalError = $_
    $rollbackErrors = New-Object System.Collections.ArrayList
    $publishedInReverse = @($publishedPaths)
    [array]::Reverse($publishedInReverse)
    foreach ($publishedPath in $publishedInReverse) {
        try {
            if (Test-Path -LiteralPath $publishedPath) {
                $failedName = "failed-" + (Split-Path -Leaf $publishedPath)
                Move-Item -LiteralPath $publishedPath -Destination (Join-Path $stageRoot $failedName) -ErrorAction Stop
            }
        }
        catch {
            [void]$rollbackErrors.Add($_.Exception.Message)
        }
    }
    $receiptsInReverse = @($archiveReceipts)
    [array]::Reverse($receiptsInReverse)
    foreach ($receipt in $receiptsInReverse) {
        try {
            if (Test-Path -LiteralPath $receipt.Destination) {
                Move-Item -LiteralPath $receipt.Destination -Destination $receipt.Source -ErrorAction Stop
            }
        }
        catch {
            [void]$rollbackErrors.Add($_.Exception.Message)
        }
    }
    try {
        if (Test-Path -LiteralPath $stageRoot) {
            New-Item -ItemType Directory -Path $failedBuildArchive -Force | Out-Null
            $failedDestination = Join-Path $failedBuildArchive $stageName
            if (-not (Test-Path -LiteralPath $failedDestination)) {
                Move-Item -LiteralPath $stageRoot -Destination $failedDestination -ErrorAction Stop
            }
        }
    }
    catch {
        [void]$rollbackErrors.Add($_.Exception.Message)
    }
    if ($rollbackErrors.Count -ne 0) {
        throw "$($originalError.Exception.Message) Rollback also failed: $($rollbackErrors -join '; ')"
    }
    throw $originalError
}
