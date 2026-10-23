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
ChangesAssociations=yes
CloseApplications=yes

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
it.AddToPath=Aggiungi il comando wpdf al PATH (per il terminale e per Claude Code)
en.AddToPath=Add the wpdf command to PATH (for the terminal and Claude Code)
it.Extra=Altre opzioni:
en.Extra=Other options:
it.Associate=Registra wmt-pdf per i file PDF e p7m (compare in "Apri con" e tra le app predefinite)
en.Associate=Register wmt-pdf for PDF and p7m files (shown in "Open with" and in default apps)
it.SetDefault=Scegli wmt-pdf come app predefinita per i PDF (apre le Impostazioni di Windows)
en.SetDefault=Choose wmt-pdf as the default app for PDFs (opens Windows Settings)
it.PdfName=Documento PDF
en.PdfName=PDF document
it.P7mName=Documento firmato (p7m)
en.P7mName=Signed document (p7m)
it.AppDescription=PDF e p7m: lettura, modifica, firma e verifica
en.AppDescription=PDF and p7m: read, edit, sign and verify

[Tasks]
Name: "addtopath"; Description: "{cm:AddToPath}"; GroupDescription: "{cm:Extra}"
Name: "associate"; Description: "{cm:Associate}"; GroupDescription: "{cm:Extra}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\wmt-pdf"; Filename: "{app}\wmt-pdf.exe"
Name: "{group}\{cm:UninstallProgram,wmt-pdf}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\wmt-pdf"; Filename: "{app}\wmt-pdf.exe"; Tasks: desktopicon

[Registry]
; Tipi di file di wmt-pdf. HKA = HKCU nell'installazione per l'utente (la normale, senza amministratore), HKLM in
; quella per tutti. L'app predefinita non si cambia: Windows la lascia scegliere solo all'utente; qui wmt-pdf diventa
; una delle scelte possibili.
Root: HKA; Subkey: "Software\Classes\wmt-pdf.pdf"; ValueType: string; ValueName: ""; ValueData: "{cm:PdfName}"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\wmt-pdf.pdf\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\wmt-pdf.exe,0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\wmt-pdf.pdf\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\wmt-pdf.exe"" ""%1"""; Tasks: associate
Root: HKA; Subkey: "Software\Classes\wmt-pdf.p7m"; ValueType: string; ValueName: ""; ValueData: "{cm:P7mName}"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\wmt-pdf.p7m\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\wmt-pdf.exe,0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\wmt-pdf.p7m\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\wmt-pdf.exe"" ""%1"""; Tasks: associate
; "Apri con": wmt-pdf tra le app proposte per .pdf e .p7m.
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "wmt-pdf.pdf"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.p7m\OpenWithProgids"; ValueType: string; ValueName: "wmt-pdf.p7m"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\wmt-pdf.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "wmt-pdf"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\wmt-pdf.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\wmt-pdf.exe\SupportedTypes"; ValueType: string; ValueName: ".p7m"; ValueData: ""; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\wmt-pdf.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\wmt-pdf.exe"" ""%1"""; Tasks: associate
; Impostazioni > App predefinite: wmt-pdf con i tipi di file che sa aprire.
Root: HKA; Subkey: "Software\WebMarkeThink"; Flags: uninsdeletekeyifempty; Tasks: associate
Root: HKA; Subkey: "Software\WebMarkeThink\wmt-pdf"; Flags: uninsdeletekeyifempty; Tasks: associate
Root: HKA; Subkey: "Software\WebMarkeThink\wmt-pdf\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "wmt-pdf"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\WebMarkeThink\wmt-pdf\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "{cm:AppDescription}"; Tasks: associate
Root: HKA; Subkey: "Software\WebMarkeThink\wmt-pdf\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "wmt-pdf.pdf"; Tasks: associate
Root: HKA; Subkey: "Software\WebMarkeThink\wmt-pdf\Capabilities\FileAssociations"; ValueType: string; ValueName: ".p7m"; ValueData: "wmt-pdf.p7m"; Tasks: associate
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "wmt-pdf"; ValueData: "Software\WebMarkeThink\wmt-pdf\Capabilities"; Flags: uninsdeletevalue; Tasks: associate

[UninstallDelete]
; Porta e token dell'ultima sessione (rimasti se wmt-pdf è stato chiuso bruscamente).
Type: files; Name: "{localappdata}\wmt-pdf\istanza.json"
Type: files; Name: "{localappdata}\wmt-pdf\istanza.avvio"

[Run]
Filename: "ms-settings:defaultapps?registeredAppUser=wmt-pdf"; Description: "{cm:SetDefault}"; Flags: shellexec postinstall skipifsilent unchecked nowait; Tasks: associate
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
