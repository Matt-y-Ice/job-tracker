# Convert Word files to PDF with the installed Microsoft Word.
# Arguments come in pairs: input.docx output.pdf [input2.docx output2.pdf ...]
# Paths are passed as arguments (never pasted into code), so odd characters in names are safe.
$ErrorActionPreference = "Stop"
try {
    $word = New-Object -ComObject Word.Application
} catch {
    Write-Output "WORD_MISSING"
    exit 2
}
try {
    $word.Visible = $false
    $word.DisplayAlerts = 0
    for ($i = 0; $i -lt $args.Count; $i += 2) {
        $doc = $word.Documents.Open($args[$i], $false, $true)   # not a conversion prompt, read-only
        try {
            $doc.SaveAs([ref]$args[$i + 1], [ref]17)            # 17 = PDF
        } finally {
            $doc.Close([ref]0)
        }
    }
} finally {
    $word.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
exit 0
