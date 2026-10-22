; Installatore Windows di wmt-pdf (Inno Setup 6).
; Installa per l'utente (niente diritti di amministratore) in %LOCALAPPDATA%\Programs\wmt-pdf,
; con l'icona nel menu Start e il comando wpdf nel PATH dell'utente.
;
; Uso: iscc /DAppVersion=0.1.0 /DSourceDir=..\dist\wmt-pdf /DIconFile=..\build\icons\wmt-pdf.ico packaging\windows\wmt-pdf.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\wmt-pdf"
#endif
#ifndef IconFile
  #define IconFile "..\build\icons\wmt-pdf.ico"
#endif

[Setup]
AppId={{6D1F4C2A-5B7E-4E2B-9C3A-8F1D2E4B6A70}
AppName=wmt-pdf
AppVersion={#AppVersion}
AppVerName=wmt-pdf {#AppVersion}
AppPublisher=WebMarkeThink
AppPublisherURL=https://webmarkethink.it
DefaultDirName={autopf}\wmt-pdf
DefaultGroupName=wmt-pdf
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=wmt-pdf-{#AppVersion}-windows-x64-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\wmt-pdf.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
CloseApplications=yes

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
it.AddToPath=Aggiungi il comando wpdf al PATH (per il terminale e per Claude Code)
en.AddToPath=Add the wpdf command to PATH (for the terminal and Claude Code)
it.Extra=Altre opzioni:
en.Extra=Other options:

[Tasks]
Name: "addtopath"; Description: "{cm:AddToPath}"; GroupDescription: "{cm:Extra}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\wmt-pdf"; Filename: "{app}\wmt-pdf.exe"
Name: "{group}\{cm:UninstallProgram,wmt-pdf}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\wmt-pdf"; Filename: "{app}\wmt-pdf.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\wmt-pdf.exe"; Description: "{cm:LaunchProgram,wmt-pdf}"; Flags: nowait postinstall skipifsilent

[Code]
const
  EnvKey = 'Environment';

function PathRoot: Integer;
begin
  if IsAdminInstallMode then
    Result := HKEY_LOCAL_MACHINE
  else
    Result := HKEY_CURRENT_USER;
end;

function PathKey: String;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := EnvKey;
end;

{ Il PATH con ; ai lati, per cercare la cartella senza falsi positivi. }
function HasDir(Paths, Dir: String): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0;
end;

procedure AddToPath(Dir: String);
var
  Paths: String;
begin
  if not RegQueryStringValue(PathRoot, PathKey, 'Path', Paths) then
    Paths := '';
  if HasDir(Paths, Dir) then
    exit;
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then
    Paths := Paths + ';';
  RegWriteExpandStringValue(PathRoot, PathKey, 'Path', Paths + Dir);
end;

procedure RemoveFromPath(Dir: String);
var
  Paths: String;
  P: Integer;
begin
  if not RegQueryStringValue(PathRoot, PathKey, 'Path', Paths) then
    exit;
  Paths := ';' + Paths + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Uppercase(Paths));
  if P = 0 then
    exit;
  Delete(Paths, P, Length(Dir) + 1);
  Paths := Copy(Paths, 2, Length(Paths) - 2);
  RegWriteExpandStringValue(PathRoot, PathKey, 'Path', Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    AddToPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
