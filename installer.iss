; VodClipper installer (Inno Setup 6). Built by .github/workflows/build-exe.yml:
;   ISCC.exe /DAppVersion=1.3 installer.iss      ->  dist\VodClipper-Setup.exe
;
; Installs per-user (no admin prompt) to %LOCALAPPDATA%\Programs\VodClipper, adds Start menu
; and desktop shortcuts, and registers an uninstaller in Windows Settings > Apps.
;
; The uninstaller also removes VodClipper's app data (downloaded tools, caption models, logs)
; and asks whether to delete the VODs and clips folder too (default: keep them).
; Scripted uninstall that also deletes them:  unins000.exe /VERYSILENT /DELETECLIPS=1

#ifndef AppVersion
  #define AppVersion "1.0"
#endif

[Setup]
AppId={{8E0B5F2A-6C1D-4E8B-9F3A-2D7C1B4A9E10}
AppName=VodClipper
AppVersion={#AppVersion}
AppVerName=VodClipper {#AppVersion}
AppPublisher=VodClipper
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\VodClipper
DefaultGroupName=VodClipper
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=VodClipper-Setup
SetupIconFile=ui\icon.ico
UninstallDisplayIcon={app}\VodClipper.exe
UninstallDisplayName=VodClipper
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; clear the previous version's libraries so an upgrade doesn't mix old and new files
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\VodClipper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\VodClipper"; Filename: "{app}\VodClipper.exe"
Name: "{autoprograms}\Uninstall VodClipper"; Filename: "{uninstallexe}"
Name: "{autodesktop}\VodClipper"; Filename: "{app}\VodClipper.exe"; Tasks: desktopicon

[Registry]
; VodClipper writes where it keeps clips here; removed on uninstall
Root: HKCU; Subkey: "Software\VodClipper"; Flags: uninsdeletekey

[Run]
Filename: "{app}\VodClipper.exe"; Description: "Open VodClipper"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop VodClipper (and any ffmpeg/download it started) so its files can be removed
Filename: "{sys}\taskkill.exe"; Parameters: "/f /t /im VodClipper.exe"; Flags: runhidden; RunOnceId: "StopVodClipper"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  ClipsDir: String;
  DataDir: String;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { close a running VodClipper before upgrading it }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /t /im VodClipper.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  { read the folders before the uninstaller removes the registry key }
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Software\VodClipper', 'ClipsFolder', ClipsDir) then
    ClipsDir := '';
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Software\VodClipper', 'DataFolder', DataDir) then
    DataDir := '';
  Result := True;
end;

function IsVodClipperFolder(const Dir: String): Boolean;
begin
  { safety: only ever delete a folder that is literally named "VodClipper" }
  Result := (Dir <> '') and DirExists(Dir) and
            (CompareText(ExtractFileName(RemoveBackslashUnlessRoot(Dir)), 'VodClipper') = 0);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Local: String;
  Answer: Integer;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;

  { app data: downloaded tools, Whisper caption models, logs, window cache }
  Local := ExpandConstant('{localappdata}\VodClipper');
  if DirExists(Local) then
    DelTree(Local, True, True, True);
  if (DataDir <> '') and (CompareText(DataDir, Local) <> 0) and IsVodClipperFolder(DataDir) then
    DelTree(DataDir, True, True, True);

  { the user's VODs and clips: ask, default keep }
  if IsVodClipperFolder(ClipsDir) then
  begin
    if ExpandConstant('{param:DELETECLIPS|0}') = '1' then
      Answer := IDYES
    else
      Answer := SuppressibleMsgBox(
        'Also delete your downloaded VODs and rendered clips?' + #13#10#13#10 +
        ClipsDir + #13#10#13#10 +
        'Choose No to keep them.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO);
    if Answer = IDYES then
      DelTree(ClipsDir, True, True, True);
  end;
end;
