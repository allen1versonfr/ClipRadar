; Clip Radar installer (Inno Setup 6). Built by .github/workflows/build.yml:
;   ISCC.exe /DAppVersion=2.0.0 installer\clipradar.iss   ->  dist\ClipRadar-Setup.exe
;
; Installs per-user (no admin prompt) to %LOCALAPPDATA%\Programs\Clip Radar, adds Start menu
; and desktop shortcuts, and registers an uninstaller in Windows Settings > Apps.
;
; The uninstaller also removes Clip Radar's app data (downloaded tools, caption models, logs)
; and asks whether to delete the VODs and clips folder too (default: keep them).
; Scripted uninstall that also deletes them:  unins000.exe /VERYSILENT /DELETECLIPS=1
;
; In-app updates run the new Setup as:  ClipRadar-Setup.exe /SILENT /SUPPRESSMSGBOXES /UPDATE=1
; It waits for Clip Radar to close itself, upgrades in place and opens Clip Radar again.
;
; Clip Radar was called VodClipper before 4.0. Same AppId, so updating from VodClipper (in the app or
; by running Setup) upgrades that install: the old program folder and shortcuts are removed, and the
; app moves its data and clips folders to the new names the first time it starts.

#ifndef AppVersion
  #define AppVersion "1.0"
#endif

[Setup]
AppId={{8E0B5F2A-6C1D-4E8B-9F3A-2D7C1B4A9E10}
AppName=Clip Radar
AppVersion={#AppVersion}
AppVerName=Clip Radar {#AppVersion}
AppPublisher=Clip Radar
AppPublisherURL=https://github.com/allen1versonfr/vodclipper
AppSupportURL=https://github.com/allen1versonfr/vodclipper/issues
AppUpdatesURL=https://github.com/allen1versonfr/vodclipper/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Clip Radar
; a VodClipper install moves to the new folder name
UsePreviousAppDir=no
DefaultGroupName=Clip Radar
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=ClipRadar-Setup
SetupIconFile=..\src\ui\icon.ico
UninstallDisplayIcon={app}\ClipRadar.exe
UninstallDisplayName=Clip Radar
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
#ifdef Sign
; code signing (only when the build has a certificate, docs/code-signing.md): Inno Setup signs the
; uninstaller and Setup itself with the "clipradar" tool the build defines as /Sclipradar=... (installer\sign.ps1)
SignTool=clipradar
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; clear the previous version's libraries so an upgrade doesn't mix old and new files
Type: filesandordirs; Name: "{app}\_internal"
; upgrading from VodClipper: its program folder and shortcuts (your clips and settings are elsewhere and are kept)
Type: filesandordirs; Name: "{localappdata}\Programs\VodClipper"
Type: files; Name: "{autoprograms}\VodClipper.lnk"
Type: files; Name: "{autoprograms}\Uninstall VodClipper.lnk"
Type: files; Name: "{autodesktop}\VodClipper.lnk"

[Files]
Source: "..\dist\ClipRadar\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Clip Radar"; Filename: "{app}\ClipRadar.exe"
Name: "{autoprograms}\Uninstall Clip Radar"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Clip Radar"; Filename: "{app}\ClipRadar.exe"; Tasks: desktopicon

[Registry]
; Clip Radar writes where it keeps clips here; removed on uninstall
Root: HKCU; Subkey: "Software\ClipRadar"; Flags: uninsdeletekey

[Run]
Filename: "{app}\ClipRadar.exe"; Description: "Open Clip Radar"; Flags: nowait postinstall skipifsilent
; after an in-app update, reopen Clip Radar
Filename: "{app}\ClipRadar.exe"; Flags: nowait; Check: IsAppUpdate

[UninstallRun]
; stop Clip Radar (and any ffmpeg/download it started) so its files can be removed
Filename: "{sys}\taskkill.exe"; Parameters: "/f /t /im ClipRadar.exe"; Flags: runhidden; RunOnceId: "StopClipRadar"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  ClipsDir: String;
  DataDir: String;

function IsAppUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  if IsAppUpdate() then
  begin
    { started by Clip Radar itself, which is closing: give it a moment, then make sure it's gone.
      No /t here: this installer is Clip Radar's child and must not be killed with it. }
    Sleep(2500);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im ClipRadar.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
    { an update started from VodClipper (the old name) }
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im VodClipper.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(500);
  end
  else
    { close a running Clip Radar before upgrading it }
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /t /im ClipRadar.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /t /im VodClipper.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  { read the folders before the uninstaller removes the registry key }
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Software\ClipRadar', 'ClipsFolder', ClipsDir) then
    if not RegQueryStringValue(HKEY_CURRENT_USER, 'Software\VodClipper', 'ClipsFolder', ClipsDir) then
      ClipsDir := '';
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Software\ClipRadar', 'DataFolder', DataDir) then
    if not RegQueryStringValue(HKEY_CURRENT_USER, 'Software\VodClipper', 'DataFolder', DataDir) then
      DataDir := '';
  Result := True;
end;

function IsAppFolder(const Dir: String): Boolean;
var
  Name: String;
begin
  { safety: only ever delete a folder that is literally named after the app (or its old name) }
  Name := ExtractFileName(RemoveBackslashUnlessRoot(Dir));
  Result := (Dir <> '') and DirExists(Dir) and
            ((CompareText(Name, 'Clip Radar') = 0) or (CompareText(Name, 'ClipRadar') = 0) or (CompareText(Name, 'VodClipper') = 0));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Local: String;
  Answer: Integer;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;

  { app data: downloaded tools, Whisper caption models, logs, window cache }
  Local := ExpandConstant('{localappdata}\ClipRadar');
  if DirExists(Local) then
    DelTree(Local, True, True, True);
  if DirExists(ExpandConstant('{localappdata}\VodClipper')) then
    DelTree(ExpandConstant('{localappdata}\VodClipper'), True, True, True);
  RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\VodClipper');
  if (DataDir <> '') and (CompareText(DataDir, Local) <> 0) and IsAppFolder(DataDir) then
    DelTree(DataDir, True, True, True);

  { the user's VODs and clips: ask, default keep }
  if IsAppFolder(ClipsDir) then
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
