[Setup]
AppName=MotionQ
AppVersion=1.0.0
DefaultDirName={autopf}\MotionQ
DefaultGroupName=MotionQ
OutputDir=installer
OutputBaseFilename=MotionQ-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
Uninstallable=yes

[Files]
Source: "dist\MotionQ\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MotionQ"; Filename: "{app}\MotionQ.exe"
Name: "{commondesktop}\MotionQ"; Filename: "{app}\MotionQ.exe"

[Run]
Filename: "{app}\MotionQ.exe"; Description: "Launch MotionQ"; Flags: nowait postinstall skipifsilent