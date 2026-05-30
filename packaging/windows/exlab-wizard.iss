; exlab-wizard.iss -- Inno Setup script for the ExLab-Wizard Windows installer.
;
; Wraps the PyInstaller --onedir bundle (dist\ExLab-Wizard\*) into an installer
; under {autopf}\ExLab-Wizard, creates a Start-menu (and optional desktop)
; shortcut to the operator-facing CLI alias ExLab-Wizard.exe (NOT the Tray
; exe), and bundles the Microsoft Edge WebView2 Evergreen bootstrapper
; (pywebview requires the WebView2 runtime).
;
; The application manages its OWN autostart at runtime (in-app
; AutostartManager), so this installer deliberately writes NO autostart /
; Run-key entries -- registering autostart here would double-register.
;
; Build (from repo root):
;   iscc /DAppVersion=<version> packaging\windows\exlab-wizard.iss
; CI downloads MicrosoftEdgeWebview2Setup.exe next to this .iss beforehand.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B3F1C2A4-7E2D-4C6A-9F0B-AAAAEXLAB0001}}
AppName=ExLab-Wizard
AppVersion={#AppVersion}
AppPublisher=ExFab
DefaultDirName={autopf}\ExLab-Wizard
DefaultGroupName=ExLab-Wizard
DisableProgramGroupPage=yes
UninstallDisplayName=ExLab-Wizard
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Emit the installer at the repo root so the CI upload/release globs find it.
OutputDir=..\..
OutputBaseFilename=ExLab-Wizard-{#AppVersion}-win-x64-setup
WizardStyle=modern
; Use the app icon for the installer chrome only when it is actually present.
#if FileExists(AddBackslash(SourcePath) + "..\..\assets\icons\ExLabWizard.ico")
SetupIconFile=..\..\assets\icons\ExLabWizard.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire onedir bundle (all three executables + _internal).
Source: "..\..\dist\ExLab-Wizard\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; WebView2 Evergreen bootstrapper -- extracted to {tmp} only during install.
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
; Operator-facing launcher is the CLI alias ExLab-Wizard.exe (starts/focuses
; the tray); the long-lived Tray exe is an autostart target, not a shortcut.
Name: "{group}\ExLab-Wizard"; Filename: "{app}\ExLab-Wizard.exe"
Name: "{group}\{cm:UninstallProgram,ExLab-Wizard}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ExLab-Wizard"; Filename: "{app}\ExLab-Wizard.exe"; Tasks: desktopicon

[Run]
; Install the WebView2 runtime silently. Do NOT treat a nonzero exit as an
; install failure -- the runtime is frequently already present (returns
; nonzero); skipifdoesntexist guards against a missing bootstrapper.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; Flags: waituntilterminated runhidden skipifdoesntexist; StatusMsg: "Installing Microsoft Edge WebView2 runtime..."
