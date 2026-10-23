# Authenticode-signs files for the release build (.github/workflows/build.yml). The build runs it
# on ClipRadar.exe, and Inno Setup runs it (its SignTool directive) on Setup and the uninstaller.
#
#   pwsh -File installer\sign.ps1 <file> [<file> ...]           sign: SHA-256 digest, RFC 3161 timestamp
#   pwsh -File installer\sign.ps1 -Verify <file> [<file> ...]   check: signtool verify /pa, and timestamped
#
# Everything comes from the environment, set up by the build's "Set up code signing" step:
#   SIGNTOOL        signtool.exe from the Windows SDK
#   SIGN_MODE       azure  - Azure Artifact Signing (Trusted Signing): SIGN_DLIB + SIGN_METADATA, and the
#                            app registration in AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET
#                   pfx    - a certificate imported into the user's store: SIGN_THUMBPRINT
# No password or token is ever on a command line here. docs/code-signing.md says how to set it up.
param(
  [switch]$Verify,
  [Parameter(Mandatory, ValueFromRemainingArguments)][string[]]$Files
)
$ErrorActionPreference = "Stop"
$signtool = $env:SIGNTOOL
if (-not $signtool -or -not (Test-Path $signtool)) { Write-Host "::error title=signing::SIGNTOOL not set up"; exit 1 }

if ($Verify) {
  $bad = 0
  foreach ($f in $Files) {
    & $signtool verify /pa /q $f | Out-Null
    $rc = $LASTEXITCODE
    $sig = Get-AuthenticodeSignature $f
    if ($rc -ne 0 -or $sig.Status -ne "Valid") {
      Write-Host "::error title=FAIL signature::$(Split-Path $f -Leaf): $($sig.Status) $($sig.StatusMessage)"; $bad++
    } elseif (-not $sig.TimeStamperCertificate) {
      Write-Host "::error title=FAIL signature::$(Split-Path $f -Leaf) is signed but not timestamped"; $bad++
    } else {
      Write-Host "::notice title=signed::$(Split-Path $f -Leaf): $($sig.SignerCertificate.Subject), timestamped by $($sig.TimeStamperCertificate.Subject)"
    }
  }
  exit [int]($bad -gt 0)
}

switch ($env:SIGN_MODE) {
  "azure" { $how = @("/tr", "http://timestamp.acs.microsoft.com", "/dlib", $env:SIGN_DLIB, "/dmdf", $env:SIGN_METADATA) }
  "pfx"   { $how = @("/tr", "http://timestamp.digicert.com", "/sha1", $env:SIGN_THUMBPRINT, "/s", "My") }
  default { Write-Host "::error title=signing::SIGN_MODE is '$env:SIGN_MODE', expected azure or pfx"; exit 1 }
}
foreach ($f in $Files) {
  # timestamp servers fail now and then; three tries, a little apart
  foreach ($try in 1..3) {
    & $signtool sign /fd SHA256 /td SHA256 @how $f
    if ($LASTEXITCODE -eq 0) { break }
    if ($try -eq 3) { Write-Host "::error title=signing::could not sign $(Split-Path $f -Leaf)"; exit 1 }
    Start-Sleep -Seconds (10 * $try)
  }
}
exit 0
