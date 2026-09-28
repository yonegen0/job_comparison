param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,

    [Parameter(Mandatory = $true)]
    [string]$WindowTitle,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [Parameter(Mandatory = $true)]
    [string]$AllowedRoot,

    [Parameter(Mandatory = $true)]
    [string]$AnnotationsJson,

    [string]$Watermark = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrEmpty($Watermark)) {
    $Watermark = -join @(
        [char]0x30B5, [char]0x30F3, [char]0x30D7, [char]0x30EB,
        [char]0x753B, [char]0x9762
    )
}

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class UserGuideWin32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool GetClientRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool ClientToScreen(IntPtr hWnd, ref POINT point);

    [DllImport("user32.dll")]
    public static extern uint GetDpiForWindow(IntPtr hWnd);
}
"@

function Assert-NormalDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) {
        throw "Expected a directory: $Path"
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reparse points are not allowed: $Path"
    }
}

$allowed = [System.IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\')
$output = [System.IO.Path]::GetFullPath($OutputPath)
Assert-NormalDirectory -Path $allowed
if ([System.IO.Path]::GetDirectoryName($output).TrimEnd('\') -cne $allowed) {
    throw "The output path must be directly inside the approved image directory."
}
if ([System.IO.Path]::GetExtension($output) -cne ".png") {
    throw "Only PNG output is allowed."
}

$script:windowHandle = [IntPtr]::Zero
$callback = [UserGuideWin32+EnumWindowsProc]{
    param([IntPtr]$hWnd, [IntPtr]$lParam)
    [uint32]$ownerPid = 0
    [void][UserGuideWin32]::GetWindowThreadProcessId($hWnd, [ref]$ownerPid)
    if ($ownerPid -ne [uint32]$ProcessId -or -not [UserGuideWin32]::IsWindowVisible($hWnd)) {
        return $true
    }
    $title = New-Object System.Text.StringBuilder 512
    [void][UserGuideWin32]::GetWindowText($hWnd, $title, $title.Capacity)
    if ($title.ToString() -ceq $WindowTitle) {
        $script:windowHandle = $hWnd
        return $false
    }
    return $true
}
[void][UserGuideWin32]::EnumWindows($callback, [IntPtr]::Zero)
if ($script:windowHandle -eq [IntPtr]::Zero) {
    throw "The capture window was not found."
}

$dpi = [UserGuideWin32]::GetDpiForWindow($script:windowHandle)
if ($dpi -ne 96) {
    throw "Guide captures require 96 DPI / 100% display scaling. Detected: $dpi DPI"
}

$rect = New-Object UserGuideWin32+RECT
if (-not [UserGuideWin32]::GetClientRect($script:windowHandle, [ref]$rect)) {
    throw "Could not read the client rectangle."
}
$origin = New-Object UserGuideWin32+POINT
$origin.X = 0
$origin.Y = 0
if (-not [UserGuideWin32]::ClientToScreen($script:windowHandle, [ref]$origin)) {
    throw "Could not translate the client rectangle."
}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -lt 760 -or $width -gt 1700 -or $height -lt 560 -or $height -gt 900) {
    throw "Unexpected capture dimensions: ${width}x${height}"
}

$annotations = @()
if ($AnnotationsJson) {
    # Windows PowerShell 5.1 returns a JSON array as one Object[] value.
    # Keeping it unwrapped lets foreach enumerate the individual annotation objects.
    $annotations = $AnnotationsJson | ConvertFrom-Json
}

$bitmap = New-Object System.Drawing.Bitmap $width, $height, ([System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen(
        $origin.X,
        $origin.Y,
        0,
        0,
        (New-Object System.Drawing.Size $width, $height),
        [System.Drawing.CopyPixelOperation]::SourceCopy
    )
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    $navy = [System.Drawing.Color]::FromArgb(255, 19, 55, 105)
    $white = [System.Drawing.Color]::White
    $linePen = New-Object System.Drawing.Pen $navy, 2
    $badgeBrush = New-Object System.Drawing.SolidBrush $navy
    $textBrush = New-Object System.Drawing.SolidBrush $white
    $badgeFont = New-Object System.Drawing.Font "Yu Gothic UI", 13, ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
    try {
        foreach ($annotation in $annotations) {
            $targetX = [int]$annotation.target_x
            $targetY = [int]$annotation.target_y
            $badgeX = [int]$annotation.badge_x
            $badgeY = [int]$annotation.badge_y
            $diameter = 30
            $centerX = $badgeX + [int]($diameter / 2)
            $centerY = $badgeY + [int]($diameter / 2)
            $graphics.DrawLine($linePen, $centerX, $centerY, $targetX, $targetY)
            $graphics.FillEllipse($badgeBrush, $badgeX, $badgeY, $diameter, $diameter)
            $label = [string]$annotation.number
            $size = $graphics.MeasureString($label, $badgeFont)
            $graphics.DrawString(
                $label,
                $badgeFont,
                $textBrush,
                [single]($centerX - $size.Width / 2),
                [single]($centerY - $size.Height / 2 - 1)
            )
        }
    }
    finally {
        $linePen.Dispose()
        $badgeBrush.Dispose()
        $textBrush.Dispose()
        $badgeFont.Dispose()
    }

    $watermarkFont = New-Object System.Drawing.Font "Yu Gothic UI", 14, ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
    $watermarkBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(155, 19, 55, 105))
    try {
        $markSize = $graphics.MeasureString($Watermark, $watermarkFont)
        $graphics.DrawString(
            $Watermark,
            $watermarkFont,
            $watermarkBrush,
            [single]($width - $markSize.Width - 14),
            [single]10
        )
    }
    finally {
        $watermarkFont.Dispose()
        $watermarkBrush.Dispose()
    }

    $bitmap.Save($output, [System.Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}

$saved = Get-Item -LiteralPath $output -Force
if (($saved.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or $saved.Length -eq 0) {
    throw "The PNG output is not a normal non-empty file."
}
Write-Output "Captured $([System.IO.Path]::GetFileName($output)): ${width}x${height}"
