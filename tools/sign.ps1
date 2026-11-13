<#
Signs a file with a code-signing certificate that you supply. build.bat calls this
after the exe is built. Set ONE of these environment variables before building:

  GSC_SIGN_THUMBPRINT  Thumbprint of a code-signing certificate in your Windows
                       certificate store (current user or local machine).
  GSC_SIGN_PFX         Path to a .pfx file. GSC_SIGN_PFX_PASSWORD holds its password.
  GSC_SIGNTOOL_ARGS    Arguments for signtool.exe after "sign", for hardware-token or
                       cloud signing such as Azure Artifact Signing. The file path is
                       added at the end.

Optional:
  GSC_TIMESTAMP_URL         Timestamp server. Default: http://timestamp.digicert.com
  GSC_SIGNTOOL              Full path to signtool.exe, if it is not found automatically.
  GSC_SIGN_ALLOW_UNTRUSTED  Set to 1 to accept a signature that Windows does not trust,
                            for testing with a certificate you made yourself.

With none of the signing variables set, the file is left unsigned and the script exits
with code 0, so a plain build still works.
#>
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'

try {
    $file = (Resolve-Path $Path).Path
    $stamp = if ($env:GSC_TIMESTAMP_URL) { $env:GSC_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }

    if ($env:GSC_SIGNTOOL_ARGS) {
        $signtool = $env:GSC_SIGNTOOL
        if (-not $signtool) {
            $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match '\\x64\\' } |
                Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
        }
        if (-not $signtool) { throw 'signtool.exe was not found. Install the Windows SDK or set GSC_SIGNTOOL.' }
        $parts = [regex]::Matches($env:GSC_SIGNTOOL_ARGS, '"[^"]*"|\S+') | ForEach-Object { $_.Value.Trim('"') }
        & $signtool sign @parts $file
        if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE." }
    }
    elseif ($env:GSC_SIGN_THUMBPRINT -or $env:GSC_SIGN_PFX) {
        if ($env:GSC_SIGN_THUMBPRINT) {
            $thumb = $env:GSC_SIGN_THUMBPRINT -replace '\s', ''
            $cert = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My |
                Where-Object { $_.Thumbprint -eq $thumb } | Select-Object -First 1
            if (-not $cert) { throw "No certificate with thumbprint $thumb was found in the Windows certificate store." }
        }
        else {
            $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(
                $env:GSC_SIGN_PFX, $env:GSC_SIGN_PFX_PASSWORD)
        }
        if (-not $cert.HasPrivateKey) { throw 'The certificate has no private key, so it cannot sign.' }
        Set-AuthenticodeSignature -FilePath $file -Certificate $cert -HashAlgorithm SHA256 `
            -TimestampServer $stamp | Out-Null
    }
    else {
        Write-Host 'No signing certificate configured (see tools\sign.ps1). The exe is not signed.'
        exit 0
    }

    $sig = Get-AuthenticodeSignature $file
    if (-not $sig.SignerCertificate) { throw "The file has no signature after signing: $($sig.StatusMessage)" }
    $trusted = $sig.Status -eq 'Valid'
    $untrustedOk = $env:GSC_SIGN_ALLOW_UNTRUSTED -eq '1' -and $sig.Status -in 'UnknownError', 'NotTrusted'
    if (-not ($trusted -or $untrustedOk)) { throw "The signature is not valid: $($sig.Status). $($sig.StatusMessage)" }

    Write-Host "Signed by: $($sig.SignerCertificate.Subject)"
    Write-Host "Status:    $($sig.Status)"
    if ($sig.TimeStamperCertificate) { Write-Host "Timestamp: $($sig.TimeStamperCertificate.Subject)" }
    else { Write-Warning 'No timestamp. The signature will stop being valid when the certificate expires.' }
    if (-not $trusted) { Write-Warning 'Windows does not trust this certificate. That is expected only for a test certificate.' }
}
catch {
    Write-Host "Signing failed: $($_.Exception.Message)"
    exit 1
}
