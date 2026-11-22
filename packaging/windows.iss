; Native per-user installer. Application data lives outside {app} and is retained.
#ifndef AppVersion
  #error AppVersion must be supplied by build_desktop.py
#endif

[Setup]
AppId={{518D6680-1B5C-41E6-9E93-710B9127D827}
AppName=Quib27
AppVersion={#AppVersion}
AppPublisher=Arbaaz Khan
AppPublisherURL=https://github.com/arbi11/kindle_service
DefaultDirName={localappdata}\Programs\Quib27
DefaultGroupName=Quib27
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=quib27-{#AppVersion}-windows-x64-setup
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\Quib27.exe
LicenseFile={#SourceDir}\COPYRIGHT.txt
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
CloseApplications=no
RestartApplications=no

[Tasks]
Name: desktopicon; Description: Create a desktop shortcut; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Quib27"; Filename: "{app}\Quib27.exe"
Name: "{autodesktop}\Quib27"; Filename: "{app}\Quib27.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Quib27.exe"; Description: Open Quib27; Flags: nowait postinstall skipifsilent
