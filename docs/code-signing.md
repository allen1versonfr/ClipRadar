# Code signing (Windows)

Signing puts a verified publisher name on `ClipRadar.exe`, `ClipRadar-Setup.exe`, its uninstaller and
`VodClipper-Setup.exe`, instead of "Unknown publisher". That's what makes the SmartScreen
"Windows protected your PC" warning go away. Signing doesn't skip SmartScreen right away, though:
reputation builds up over the first downloads of the signed builds.

The build signs only when the secrets below are set. Without them, it builds and releases the same
unsigned app it always has. The in-app updater's SHA-256 check against GitHub's asset digest stays
either way. Signing adds who published the file, and the checksum still proves the file arrived intact.

## What the build does with a certificate

`.github/workflows/build.yml`, steps that run only when `SIGNING_CONFIGURED` is `true`:

1. **Set up code signing** finds `signtool.exe`. Then it either downloads Microsoft's Artifact
   Signing client (NuGet, pinned version and SHA-256) or imports the PFX into the runner's certificate store.
2. **Sign ClipRadar.exe** signs the exe right after PyInstaller builds it, before the tests and before
   Inno Setup packs it. The portable zip contains this signed exe too.
3. **Build installer** passes `/DSign` and a `SignTool` command to Inno Setup, which signs the
   uninstaller and `ClipRadar-Setup.exe` (`installer/clipradar.iss`).
4. **Verify signatures** runs `signtool verify /pa` on the exe and Setup, and checks they're timestamped.
   The install test checks the installed uninstaller the same way, and the release step checks `VodClipper-Setup.exe`.

Every signature uses a SHA-256 file digest and an RFC 3161 timestamp, so it stays valid after the
certificate expires. The commands are in `installer/sign.ps1`.

## Adding the secrets

GitHub repo › **Settings** › **Secrets and variables** › **Actions** › **New repository secret**.
Add one set or the other. If both are set, Azure is used.

### Option A: Azure Artifact Signing (Trusted Signing)

In the Azure portal:

1. Register the `Microsoft.CodeSigning` resource provider on the subscription (Subscription ›
   Resource providers). Then create an **Artifact Signing account** on the Basic plan. This needs a
   pay-as-you-go subscription, not a free trial. Note the region.
2. Give yourself the **Artifact Signing Identity Verifier** role. Then, under the account, complete
   **Identity validation**: individual, or organization.
3. Create a **Certificate profile** of type *Public Trust*.
4. In **Microsoft Entra ID** › App registrations, create an app (e.g. `clipradar-signing`), then add a
   **client secret** under Certificates & secrets.
5. On the signing account › Access control (IAM), give that app the
   **Artifact Signing Certificate Profile Signer** role.

Then add these secrets:

| Secret | Value |
|---|---|
| `AZURE_SIGNING_ENDPOINT` | the account's region endpoint, e.g. `https://eus.codesigning.azure.net` (it must match the region, or signing fails with 403) |
| `AZURE_SIGNING_ACCOUNT` | the Artifact Signing account name |
| `AZURE_SIGNING_PROFILE` | the certificate profile name |
| `AZURE_TENANT_ID` | Directory (tenant) ID of the app registration |
| `AZURE_CLIENT_ID` | Application (client) ID of the app registration |
| `AZURE_CLIENT_SECRET` | the client secret's **value** |

Client secrets expire (the default is 6 months, 24 at most). Put a new one in `AZURE_CLIENT_SECRET` before that.

### Option B: a certificate from a CA (PFX)

Use this only if the certificate can be exported as a `.pfx` file. Most OV certificates issued since
June 2023 can't be exported: the key lives on a hardware token or in the CA's cloud HSM. Those need the
CA's own signing tool (e.g. SSL.com eSigner, Certum SimplySign) and a change to `installer/sign.ps1`.

| Secret | Value |
|---|---|
| `WINDOWS_CERT_PFX_BASE64` | the `.pfx` file as base64. In PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx")) \| Set-Clipboard` |
| `WINDOWS_CERT_PASSWORD` | the PFX password (leave it out if there is none) |

## Checking it worked

Run the **Build and test** workflow (Actions › Build and test › Run workflow) on any branch. In the run:

- "Set up code signing" notes which account or certificate it used.
- "Verify signatures" notes `signed: ClipRadar-Setup.exe: CN=…, timestamped by …` for each file.

On a PC, right-click the downloaded `ClipRadar-Setup.exe` › Properties › **Digital Signatures**.

## Rules

- Never commit a certificate, key, password or token. They belong only in the Actions secrets.
- GitHub hides secret values in logs. The workflow never prints them or puts them on a command line:
  the PFX is decoded to a temp file, imported, and deleted, and signtool selects the certificate by thumbprint.
- Secrets aren't passed to workflows run from forks, so pull requests from forks build unsigned.
