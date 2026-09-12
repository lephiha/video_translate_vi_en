; installer.iss — Inno Setup script cho Video Dub EN→VI
; Build: mở file này bằng Inno Setup Compiler (iscc.exe installer.iss),
; SAU KHI đã chạy `pyinstaller app.spec` để có sẵn dist/VideoDubEnVi/.

#define MyAppName "Video Dub EN-VI"
#define MyAppVersion "1.0.3"
#define MyAppPublisher "LPH"
#define MyAppExeName "VideoDubEnVi.exe"
#define MyBuildDir "dist\VideoDubEnVi"

[Setup]
AppId={{B4C2E1A0-7F3D-4E9B-9C1A-2D5F8E6A1B3C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
SetupIconFile=docs\app_icon.ico
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=VideoDubEnVi-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; App bundle nặng (torch/transformers/demucs) → dùng 64-bit installer
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableDirPage=no

[Languages]
Name: "vietnamese"; MessagesFile: "compiler:Languages\Vietnamese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo shortcut ngoài Desktop"; GroupDescription: "Shortcut bổ sung:"; Flags: unchecked

[Files]
; Toàn bộ thư mục PyInstaller build ra — bao gồm exe + mọi DLL/dependency
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Excludes: "debug.log"; Flags: ignoreversion recursesubdirs createallsubdirs
; .env chứa key mặc định của chủ app; không ghi đè file đã có khi nâng cấp.
Source: ".env"; DestDir: "{app}"; DestName: ".env"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Gỡ cài đặt {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Khởi chạy {#MyAppName} ngay"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Dọn output/uploads do app tạo ra lúc chạy — hỏi trước khi xoá.
; Model đã tải (VieNeu/sherpa/NLLB) nằm ở %LOCALAPPDATA%, KHÔNG xoá ở đây
; để lần cài sau không phải tải lại — muốn xoá sạch thì tự vào AppData xoá tay.
Type: filesandordirs; Name: "{app}\output"
Type: filesandordirs; Name: "{app}\uploads"
