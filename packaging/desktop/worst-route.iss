; The Windows installer of the desktop app (docs/reference/desktop.md, **Building it**), compiled
; by tools/build_desktop.sh with Inno Setup 6 (on GitHub's Windows runners):
;   iscc /DVersion=<x.y.z> /DSource=<the frozen app's folder> /DOutput=<dir> /DName=<file> worst-route.iss
; Installed for the user alone (no administrator), into %LOCALAPPDATA%\Programs\Worst Route, with
; a Start menu entry and an optional desktop one; uninstalling removes the app, not what it wrote
; into %LOCALAPPDATA%\Worst Route (routes, the downloaded basemap, the compiled kernels).

[Setup]
AppId={{6A4E1D53-2B8F-4C0E-9C77-3E1B5F0D9A21}
AppName=Worst Route
AppVersion={#Version}
AppPublisher=Worst Route
AppPublisherURL=https://github.com/tarnan3751/worst-route
DefaultDirName={autopf}\Worst Route
DefaultGroupName=Worst Route
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Output}
OutputBaseFilename={#Name}
Compression=lzma2/max
SolidCompression=yes
DiskSpanning=no
WizardStyle=modern
UninstallDisplayIcon={app}\worst-route-desktop.exe

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Worst Route"; Filename: "{app}\worst-route-desktop.exe"
Name: "{userdesktop}\Worst Route"; Filename: "{app}\worst-route-desktop.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\worst-route-desktop.exe"; Description: "Start Worst Route"; Flags: nowait postinstall skipifsilent
