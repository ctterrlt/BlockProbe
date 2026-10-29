; SPDX-License-Identifier: AGPL-3.0-or-later
; Purpose: Build an unsigned per-user Windows installer from the Flet bundle.

#define AppName "BlockProbe"
#ifndef AppVersion
  #define AppVersion GetEnv("BLOCKPROBE_VERSION")
#endif
#define AppPublisher "BlockProbe contributors"
#define AppExeName "BlockProbe.exe"
#ifndef BuildDir
  #define BuildDir GetEnv("BLOCKPROBE_BUILD_DIR")
#endif
#ifndef OutputDir
  #define OutputDir GetEnv("BLOCKPROBE_OUTPUT_DIR")
#endif

[Setup]
AppId={{2F646D59-01BA-4B12-A4E6-DF1F17F14513}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\BlockProbe
DefaultGroupName=BlockProbe
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=BlockProbe-Setup-{#AppVersion}
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\BlockProbe"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\BlockProbe"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch BlockProbe"; Flags: postinstall nowait skipifsilent
