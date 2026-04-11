; Inno Setup 6 — compile this script to produce a single installer with Add/Remove Programs entry.
; Install Inno Setup, then: ISCC.exe rootrecord.iss
; Adjust Source paths if your dist folder lives elsewhere.
;
; Release checklist: set MyAppVersion to match app_version.py (APP_VERSION) before compiling.
; Patch digit tracks latest DB schema migration (e.g. migration 5 -> 1.2.5).
;
; --- Trust / "Windows protected your PC" (SmartScreen) ---
; Unsigned installers show "Unknown publisher". That is normal for new builds until you use a
; code signing certificate (Authenticode). Fix: sign RootRecord.exe and the installer .exe with
; signtool (Windows SDK). EV certificates often get immediate SmartScreen trust; standard (OV)
; certs build reputation as users run the app. See sign_release.example.ps1 in this folder.
; Inno can also sign the installer via Sign Tool integration: https://jrsoftware.org/ishelp/topic_setup_signtool.htm

; Display name includes Beta. Install dir may differ from older builds — TryGetExistingInstallDir checks legacy paths.
#define MyAppName "RootRecord Business Manager (Beta)"
#define MyAppDirName "Root Record\RootRecord Business Manager (Beta)"
#define MyAppVersion "1.3.25"
#define MyAppPublisher "RootRecord"
#define MyAppCopyright "Copyright (C) 2026 RootRecord"
#define MyAppId "{{A7B2E9F1-4C3D-5E6F-8091-2B3C4D5E6F70}"
#define MyAppExeName "RootRecord.exe"
#define DistDir "..\\dist\\RootRecord"
#define MyAppInstallerIcon "..\\..\\..\\favicon.ico"
; Installer side art: Inno expects 164x314 (large) and 55x55 (small) — see build/branding/
#define MyWizardLargeBmp "branding\\wizard-large.bmp"
#define MyWizardSmallBmp "branding\\wizard-small.bmp"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright={#MyAppCopyright}
AppVerName={#MyAppName} {#MyAppVersion}
; Per-machine install (admin): installs under Program Files
DefaultDirName={autopf}\Root Record\Business Manager (Beta)
DefaultGroupName={#MyAppName}
OutputDir=.\output
OutputBaseFilename=RootRecordSetup-Beta-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=classic
WizardSizePercent=110
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile={#MyAppInstallerIcon}
WizardImageFile={#MyWizardLargeBmp}
WizardSmallImageFile={#MyWizardSmallBmp}
; Reuse last install folder from registry on upgrade (same AppId).
UsePreviousAppDir=yes
; Skip Select Destination when upgrading (Inno decides via previous install).
DisableDirPage=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyAppInstallerIcon}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyAppInstallerIcon}"; Flags: dontcopy

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\favicon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\favicon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
var
  InstallModePage: TInputOptionWizardPage;
  DataLocationPage: TInputOptionWizardPage;
  CustomDataDirPage: TInputDirWizardPage;
  PreLaunchConfigPage: TInputOptionWizardPage;
  TermsViewPage: TOutputMsgMemoWizardPage;
  PrivacyViewPage: TOutputMsgMemoWizardPage;
  TermsAgreementPage: TInputOptionWizardPage;
  StarterContentPage: TInputOptionWizardPage;
  ThemePage: TInputOptionWizardPage;
  CurrencyPage: TInputOptionWizardPage;
  DashboardMoneyPage: TInputOptionWizardPage;
  BusinessInfoPage: TInputQueryWizardPage;
  BusinessInfoPage2: TInputQueryWizardPage;
  PrefInputPage: TInputQueryWizardPage;
  ExistingInstallDir: string;
  ExistingUninstallCmd: string;
  ExistingDataRoot: string;
  IsReinstallMode: Boolean;
  OldVersionHandled: Boolean;
  ChosenDataDir: string;
  IncludeStarterContent: Boolean;
  SelectedTheme: string;
  SelectedCurrency: string;
  ShowMoneyOnDashboard: Boolean;
  PromptIntervalSec: string;
  PromptFirstDelaySec: string;
  BusinessName: string;
  BusinessLegalName: string;
  BusinessOwner: string;
  BusinessTaxId: string;
  BusinessEmail: string;
  BusinessPhone: string;
  BusinessWebsite: string;
  BusinessAddress: string;
  BusinessTimezone: string;
  BusinessInvoiceNotes: string;
  EnablePreLaunchConfig: Boolean;
  AcceptedTerms: Boolean;

function DirHasOurExe(const Dir: string): Boolean;
begin
  Result := (Trim(Dir) <> '') and DirExists(Dir) and FileExists(Dir + '\{#MyAppExeName}');
end;

function TryGetExistingInstallDir(out Dir: string): Boolean;
var
  KeyPath: string;
  UninstallCmd: string;
  Legacy1: string;
  Legacy2: string;
  Legacy3: string;
  Legacy4: string;
  Legacy5: string;
  Legacy6: string;
begin
  Result := False;
  Dir := '';
  KeyPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + '{#MyAppId}' + '_is1';
  if RegQueryStringValue(HKCU, KeyPath, 'InstallLocation', Dir) then
  begin
    Dir := Trim(Dir);
    if DirHasOurExe(Dir) then
      Result := True;
  end;
  if (not Result) and RegQueryStringValue(HKLM, KeyPath, 'InstallLocation', Dir) then
  begin
    Dir := Trim(Dir);
    if DirHasOurExe(Dir) then
      Result := True;
  end;
  if (not Result) and RegQueryStringValue(HKCU, KeyPath, 'UninstallString', UninstallCmd) then
  begin
    UninstallCmd := Trim(UninstallCmd);
    if (Pos('"', UninstallCmd) = 1) then
    begin
      Delete(UninstallCmd, 1, 1);
      if Pos('"', UninstallCmd) > 0 then
        SetLength(UninstallCmd, Pos('"', UninstallCmd) - 1);
    end
    else if Pos(' ', UninstallCmd) > 0 then
      SetLength(UninstallCmd, Pos(' ', UninstallCmd) - 1);
    Dir := ExtractFileDir(UninstallCmd);
    if DirHasOurExe(Dir) then
      Result := True;
  end;
  if (not Result) and RegQueryStringValue(HKLM, KeyPath, 'UninstallString', UninstallCmd) then
  begin
    UninstallCmd := Trim(UninstallCmd);
    if (Pos('"', UninstallCmd) = 1) then
    begin
      Delete(UninstallCmd, 1, 1);
      if Pos('"', UninstallCmd) > 0 then
        SetLength(UninstallCmd, Pos('"', UninstallCmd) - 1);
    end
    else if Pos(' ', UninstallCmd) > 0 then
      SetLength(UninstallCmd, Pos(' ', UninstallCmd) - 1);
    Dir := ExtractFileDir(UninstallCmd);
    if DirHasOurExe(Dir) then
      Result := True;
  end;
  { Portable / broken registry / older folder layouts (same AppId or copied tree) }
  if not Result then
  begin
    Legacy5 := ExpandConstant('{autopf}\Root Record\Business Manager (Beta)');
    Legacy6 := ExpandConstant('{autopf}\Root Record\RootRecord Business Manager (Beta)');
    Legacy1 := ExpandConstant('{localappdata}\{#MyAppDirName}');
    Legacy2 := ExpandConstant('{localappdata}\Root Record\RootRecord Business Manager');
    Legacy3 := ExpandConstant('{localappdata}\RootRecord Business Manager');
    Legacy4 := ExpandConstant('{localappdata}\RootRecord Business Manager (Beta)');
    if DirHasOurExe(Legacy5) then
    begin
      Dir := Legacy5;
      Result := True;
    end
    else if DirHasOurExe(Legacy6) then
    begin
      Dir := Legacy6;
      Result := True;
    end
    else if DirHasOurExe(Legacy1) then
    begin
      Dir := Legacy1;
      Result := True;
    end
    else if DirHasOurExe(Legacy2) then
    begin
      Dir := Legacy2;
      Result := True;
    end
    else if DirHasOurExe(Legacy3) then
    begin
      Dir := Legacy3;
      Result := True;
    end
    else if DirHasOurExe(Legacy4) then
    begin
      Dir := Legacy4;
      Result := True;
    end;
  end;
end;

function TryGetExistingUninstallCommand(out Cmd: string): Boolean;
var
  KeyPath: string;
begin
  Result := False;
  Cmd := '';
  KeyPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + '{#MyAppId}' + '_is1';
  if RegQueryStringValue(HKCU, KeyPath, 'UninstallString', Cmd) then
  begin
    Cmd := Trim(Cmd);
    Result := Cmd <> '';
  end;
  if (not Result) and RegQueryStringValue(HKLM, KeyPath, 'UninstallString', Cmd) then
  begin
    Cmd := Trim(Cmd);
    Result := Cmd <> '';
  end;
end;

function RunExistingUninstallerKeepData(const UninstallCmd: string): Boolean;
var
  CmdLine: string;
  ResultCode: Integer;
begin
  Result := False;
  CmdLine := UninstallCmd;
  if Pos('/KEEPDATA', Uppercase(CmdLine)) = 0 then
    CmdLine := CmdLine + ' /KEEPDATA /VERYSILENT /SUPPRESSMSGBOXES /NORESTART';
  Result := Exec(ExpandConstant('{cmd}'), '/C ' + CmdLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function GetExistingDataRoot(): string;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, 'Environment', 'ROOTRECORD_HOME', Result) then
    Result := ExpandConstant('{localappdata}\RootRecord');
  Result := Trim(Result);
  if Result = '' then
    Result := ExpandConstant('{localappdata}\RootRecord');
end;

function CopyDirTree(const SourceDir, DestDir: string): Boolean;
var
  FindRec: TFindRec;
  SrcPath, DstPath: string;
begin
  Result := True;
  ForceDirectories(DestDir);
  if FindFirst(SourceDir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name = '.') or (FindRec.Name = '..') then
          continue;
        SrcPath := SourceDir + '\' + FindRec.Name;
        DstPath := DestDir + '\' + FindRec.Name;
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          if not CopyDirTree(SrcPath, DstPath) then
          begin
            Result := False;
            Exit;
          end;
        end
        else if not CopyFile(SrcPath, DstPath, False) then
        begin
          Result := False;
          Exit;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function ResolveChosenDataDir(): string;
begin
  if DataLocationPage = nil then
  begin
    Result := ExpandConstant('{localappdata}\RootRecord');
    Exit;
  end;
  if DataLocationPage.SelectedValueIndex = 0 then
    Result := ExpandConstant('{localappdata}\RootRecord')
  else if DataLocationPage.SelectedValueIndex = 1 then
    Result := ExpandConstant('{userdocs}\RootRecord')
  else
    Result := Trim(CustomDataDirPage.Values[0]);
end;

function BuildTermsText(): string;
begin
  Result :=
    'Terms of Service' + #13#10 +
    'Last updated: 2026-03-09' + #13#10 + #13#10 +
    'By using RootRecord''s websites, dashboards, and services, you agree to these Terms. If you use the Services on behalf of an organization, you represent that you have authority to bind that organization.' + #13#10 + #13#10 +
    'Acceptance' + #13#10 +
    'By using RootRecord''s websites, dashboards, and services ("Services"), you agree to these Terms.' + #13#10 + #13#10 +
    'Description of services' + #13#10 +
    'RootRecord provides monitoring, analytics, reports, and automation (solar/power statistics, weather, system metrics, dashboards). You may create an account to access dashboards and reports. Some content is available without an account. Services are provided "as is" and may change over time.' + #13#10 + #13#10 +
    'Accounts and your use' + #13#10 +
    'You may register for an account. You are responsible for the accuracy of your information and for keeping your credentials secure. You agree to use the Services only for lawful purposes. You must not attempt to gain unauthorized access, disrupt the Services, or misuse other users'' data.' + #13#10 + #13#10 +
    'Subscriptions and billing' + #13#10 +
    'RootRecord offers a Free tier and a paid Supporter tier. All payments are processed by Stripe.' + #13#10 +
    'Free tier: Account, services, gaming link, charts, one project. No payment required.' + #13#10 +
    'Supporter: One membership for website and Minecraft (The Recorded Realm). Monthly, yearly, or lifetime.' + #13#10 +
    'Free trial: 7-day trial for new monthly/yearly subscribers. One trial per account.' + #13#10 +
    'Payment and renewal: Recurring subscriptions charged by Stripe. Cancellation takes effect at end of billing period.' + #13#10 +
    'Lifetime: One-time payment for permanent Supporter access. No recurring charges.' + #13#10 +
    'If you cancel or lapse, you return to Free. Billing records for lapsed accounts are stored for one year, then removed.' + #13#10 + #13#10 +
    'Data and privacy' + #13#10 +
    'Your use is also governed by our Privacy Policy. RootRecord can access and process all content you provide for operations.' + #13#10 + #13#10 +
    'Availability and disclaimers' + #13#10 +
    'We strive for reliability but do not guarantee uninterrupted service. Scheduled unavailability may occur.' + #13#10 + #13#10 +
    'Limitation of liability' + #13#10 +
    'To the fullest extent permitted by law, RootRecord is not liable for indirect, incidental, special, or consequential damages, or for loss of data or profits.' + #13#10 + #13#10 +
    'Changes' + #13#10 +
    'We may change these Terms. Continued use means you accept the new Terms.' + #13#10 + #13#10 +
    'Contact' + #13#10 +
    'For questions, see our home page or Contact page.';
end;

function BuildPrivacyText(): string;
begin
  Result :=
    'Privacy Policy' + #13#10 +
    'Last updated: 2026-03-09' + #13#10 + #13#10 +
    'RootRecord ("we", "our") provides monitoring, dashboards, and automation services. This policy describes what data we collect, how we use it, and your choices.' + #13#10 + #13#10 +
    'Data we collect' + #13#10 +
    '- Account data, authentication/session data, and optional integration identifiers where applicable' + #13#10 +
    '- Billing metadata from Stripe (not full card/CVV)' + #13#10 +
    '- Monitoring data from connected services/devices' + #13#10 +
    '- Page views/analytics (including visitor ID cookie), operation logs, and affiliate redirect logs' + #13#10 + #13#10 +
    'How we use it' + #13#10 +
    'We use data to provide dashboards, reports, alerts, automation, security, and service improvement. We do not sell your data.' + #13#10 + #13#10 +
    'AI report data and our own models' + #13#10 +
    'AI report inputs/outputs may be stored and used to create and improve RootRecord models. Not used to train third-party commercial models.' + #13#10 + #13#10 +
    'Sharing' + #13#10 +
    'Data is shared only as needed with providers and where required by law.' + #13#10 + #13#10 +
    'Retention and choices' + #13#10 +
    'Data is retained as needed for service/operations/legal purposes. You can manage account settings, download data, and request permanent deletion.' + #13#10 + #13#10 +
    'Changes' + #13#10 +
    'We may update this policy. Continued use after updates means acceptance.' + #13#10 + #13#10 +
    'Contact' + #13#10 +
    'For privacy questions, see our home page or Contact page.';
end;

function JsonEscape(const S: string): string;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
  StringChangeEx(Result, #13#10, '\n', True);
  StringChangeEx(Result, #10, '\n', True);
  StringChangeEx(Result, #13, '\n', True);
end;

function BoolToJson(B: Boolean): string;
begin
  if B then
    Result := 'true'
  else
    Result := 'false';
end;

function BuildInstallOptionsJson(): string;
begin
  Result :=
    '{' + #13#10 +
    '  "include_starter_content": ' + BoolToJson(IncludeStarterContent) + ',' + #13#10 +
    '  "theme": "' + JsonEscape(SelectedTheme) + '",' + #13#10 +
    '  "currency_default": "' + JsonEscape(SelectedCurrency) + '",' + #13#10 +
    '  "show_money_in_dashboard": ' + BoolToJson(ShowMoneyOnDashboard) + ',' + #13#10 +
    '  "prompt_interval_sec": ' + Trim(PromptIntervalSec) + ',' + #13#10 +
    '  "prompt_first_delay_sec": ' + Trim(PromptFirstDelaySec) + ',' + #13#10 +
    '  "business_name": "' + JsonEscape(BusinessName) + '",' + #13#10 +
    '  "business_legal_name": "' + JsonEscape(BusinessLegalName) + '",' + #13#10 +
    '  "business_owner": "' + JsonEscape(BusinessOwner) + '",' + #13#10 +
    '  "business_tax_id": "' + JsonEscape(BusinessTaxId) + '",' + #13#10 +
    '  "business_email": "' + JsonEscape(BusinessEmail) + '",' + #13#10 +
    '  "business_phone": "' + JsonEscape(BusinessPhone) + '",' + #13#10 +
    '  "business_website": "' + JsonEscape(BusinessWebsite) + '",' + #13#10 +
    '  "business_address": "' + JsonEscape(BusinessAddress) + '",' + #13#10 +
    '  "business_timezone": "' + JsonEscape(BusinessTimezone) + '",' + #13#10 +
    '  "business_invoice_notes": "' + JsonEscape(BusinessInvoiceNotes) + '"' + #13#10 +
    '}';
end;

procedure ApplyInstallerTheme();
begin
  { Keep default Inno Setup styling. }
end;

procedure InitializeWizard();
begin
  IsReinstallMode := False;
  OldVersionHandled := False;
  ExistingUninstallCmd := '';
  EnablePreLaunchConfig := True;
  AcceptedTerms := False;
  IncludeStarterContent := True;
  SelectedTheme := 'system';
  SelectedCurrency := 'USD';
  ShowMoneyOnDashboard := True;
  PromptIntervalSec := '900';
  PromptFirstDelaySec := '120';
  BusinessTimezone := 'system';
  ExistingDataRoot := GetExistingDataRoot();
  TryGetExistingUninstallCommand(ExistingUninstallCmd);
  if not TryGetExistingInstallDir(ExistingInstallDir) then
  begin
    if Trim(ExistingUninstallCmd) <> '' then
    begin
      if DirHasOurExe(ExpandConstant('{autopf}\Root Record\Business Manager (Beta)')) then
        ExistingInstallDir := ExpandConstant('{autopf}\Root Record\Business Manager (Beta)')
      else if DirHasOurExe(ExpandConstant('{autopf}\Root Record\RootRecord Business Manager (Beta)')) then
        ExistingInstallDir := ExpandConstant('{autopf}\Root Record\RootRecord Business Manager (Beta)');
    end;
  end;
  ApplyInstallerTheme();
  if Trim(ExistingInstallDir) <> '' then
  begin
    InstallModePage :=
      CreateInputOptionPage(
        wpWelcome,
        'Existing installation detected',
        'Choose how to proceed',
        'A previous version was found at:' + #13#10 + ExistingInstallDir + #13#10 + #13#10 +
        'Update uninstalls the previous app version first, then installs the new version (user data is preserved).' + #13#10 +
        'Re-install removes app files and installs fresh (user data is preserved unless manually deleted).',
        True,
        False
      );
    InstallModePage.Add('Update Current Version (recommended)');
    InstallModePage.Add('Re-install (clean app files)');
    InstallModePage.SelectedValueIndex := 0;
  end;

  DataLocationPage :=
    CreateInputOptionPage(
      wpSelectDir,
      'User data location',
      'Choose where RootRecord stores user data',
      'This controls where database, users, exports, and settings data are stored.',
      True,
      False
    );
  DataLocationPage.Add('Default (Local AppData)');
  DataLocationPage.Add('Documents\RootRecord');
  DataLocationPage.Add('Custom folder');

  if CompareText(ExistingDataRoot, ExpandConstant('{userdocs}\RootRecord')) = 0 then
    DataLocationPage.SelectedValueIndex := 1
  else if CompareText(ExistingDataRoot, ExpandConstant('{localappdata}\RootRecord')) = 0 then
    DataLocationPage.SelectedValueIndex := 0
  else
    DataLocationPage.SelectedValueIndex := 2;

  CustomDataDirPage :=
    CreateInputDirPage(
      DataLocationPage.ID,
      'Custom data folder',
      'Select custom user data location',
      'Choose a folder for RootRecord data.',
      False,
      ''
    );
  CustomDataDirPage.Add('');
  CustomDataDirPage.Values[0] := ExistingDataRoot;

  PreLaunchConfigPage :=
    CreateInputOptionPage(
      CustomDataDirPage.ID,
      'Pre-launch configuration',
      'Would you like to configure settings before first launch?',
      'You can always change these later in Settings.',
      True,
      False
    );
  PreLaunchConfigPage.Add('Yes, configure settings now');
  PreLaunchConfigPage.Add('No, use defaults for now');
  PreLaunchConfigPage.SelectedValueIndex := 0;

  TermsViewPage :=
    CreateOutputMsgMemoPage(
      PreLaunchConfigPage.ID,
      'Terms of Service',
      'Please review before continuing',
      'Read the Terms of Service below:',
      BuildTermsText()
    );

  PrivacyViewPage :=
    CreateOutputMsgMemoPage(
      TermsViewPage.ID,
      'Privacy Policy',
      'Please review before continuing',
      'Read the Privacy Policy below:',
      BuildPrivacyText()
    );

  TermsAgreementPage :=
    CreateInputOptionPage(
      PrivacyViewPage.ID,
      'Terms of Service & Privacy',
      'You must accept before installing',
      'Please confirm acceptance to continue installation.',
      True,
      False
    );
  TermsAgreementPage.Add('I agree to the Terms of Service and Privacy Policy');
  TermsAgreementPage.Add('I do not agree');
  TermsAgreementPage.SelectedValueIndex := 1;

  StarterContentPage :=
    CreateInputOptionPage(
      TermsAgreementPage.ID,
      'Starter content',
      'Choose starter defaults for this install/update',
      'Select whether RootRecord should include starter defaults (categories/topics/quick actions).' + #13#10 +
      'You can change and customize these later in Settings.',
      True,
      False
    );
  StarterContentPage.Add('Include starter defaults (recommended)');
  StarterContentPage.Add('Start blank (no starter categories/topics)');
  StarterContentPage.SelectedValueIndex := 0;

  ThemePage :=
    CreateInputOptionPage(
      StarterContentPage.ID,
      'Appearance preference',
      'Choose default theme',
      'You can change this later in Settings.',
      True,
      False
    );
  ThemePage.Add('System');
  ThemePage.Add('Light');
  ThemePage.Add('Dark');
  ThemePage.SelectedValueIndex := 0;

  CurrencyPage :=
    CreateInputOptionPage(
      ThemePage.ID,
      'Currency preference',
      'Choose default currency',
      'Used for income/expense defaults.',
      True,
      False
    );
  CurrencyPage.Add('USD');
  CurrencyPage.Add('EUR');
  CurrencyPage.Add('GBP');
  CurrencyPage.Add('CAD');
  CurrencyPage.SelectedValueIndex := 0;

  DashboardMoneyPage :=
    CreateInputOptionPage(
      CurrencyPage.ID,
      'Dashboard preference',
      'Show money metrics on dashboard?',
      'Choose whether income/expense amounts appear on dashboard by default.',
      True,
      False
    );
  DashboardMoneyPage.Add('Yes, show money on dashboard');
  DashboardMoneyPage.Add('No, hide money on dashboard');
  DashboardMoneyPage.SelectedValueIndex := 0;

  PrefInputPage :=
    CreateInputQueryPage(
      DashboardMoneyPage.ID,
      'Prompt preferences',
      'Configure check-in timing',
      'You can adjust these later in Settings.'
    );
  PrefInputPage.Add('Prompt interval (seconds):', False);
  PrefInputPage.Add('First prompt delay (seconds):', False);
  PrefInputPage.Values[0] := '900';
  PrefInputPage.Values[1] := '120';

  BusinessInfoPage :=
    CreateInputQueryPage(
      PrefInputPage.ID,
      'Business profile',
      'Set up your business profile',
      'Step 1 of 2. These details are used in reports and can be edited later.'
    );
  BusinessInfoPage.Add('Business name:', False);
  BusinessInfoPage.Add('Legal business name:', False);
  BusinessInfoPage.Add('Owner:', False);
  BusinessInfoPage.Add('Tax ID:', False);
  BusinessInfoPage.Values[0] := 'Root Record';
  BusinessInfoPage.Values[1] := 'Root Record';
  BusinessInfoPage.Values[2] := '';
  BusinessInfoPage.Values[3] := '';

  BusinessInfoPage2 :=
    CreateInputQueryPage(
      BusinessInfoPage.ID,
      'Business contact/details',
      'Set up contact and reporting details',
      'Step 2 of 2. You can edit these later in the Business tab.'
    );
  BusinessInfoPage2.Add('Email:', False);
  BusinessInfoPage2.Add('Phone:', False);
  BusinessInfoPage2.Add('Website:', False);
  BusinessInfoPage2.Add('Address:', False);
  BusinessInfoPage2.Add('Timezone (e.g. system, UTC, America/New_York):', False);
  BusinessInfoPage2.Add('Invoice/payment notes:', False);
  BusinessInfoPage2.Values[4] := 'system';
end;

procedure CurPageChanged(CurPageID: Integer);
var
  ContentLeft: Integer;
  ContentWidth: Integer;
begin
  { Large = left panel (wizard-large.bmp); small = top-right (wizard-small.bmp). }
  WizardForm.WizardBitmapImage.Visible := True;
  WizardForm.WizardBitmapImage.SendToBack();
  WizardForm.WizardSmallBitmapImage.Visible := True;
  WizardForm.WizardSmallBitmapImage.BringToFront();

  if CurPageID = wpFinished then
  begin
    { Prevent heading/body text clipping on finish page by forcing wrapping bounds. }
    ContentLeft := WizardForm.WizardBitmapImage.Left + WizardForm.WizardBitmapImage.Width + ScaleX(16);
    ContentWidth := WizardForm.ClientWidth - ContentLeft - ScaleX(20);
    if ContentWidth < ScaleX(280) then
      ContentWidth := ScaleX(280);

    WizardForm.FinishedHeadingLabel.AutoSize := False;
    WizardForm.FinishedHeadingLabel.WordWrap := True;
    WizardForm.FinishedHeadingLabel.Left := ContentLeft;
    WizardForm.FinishedHeadingLabel.Width := ContentWidth;

    WizardForm.FinishedLabel.AutoSize := False;
    WizardForm.FinishedLabel.WordWrap := True;
    WizardForm.FinishedLabel.Left := ContentLeft;
    WizardForm.FinishedLabel.Width := ContentWidth;

    WizardForm.RunList.Left := ContentLeft;
    WizardForm.RunList.Width := ContentWidth;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  MoveDataReply: Integer;
begin
  Result := True;
  if (InstallModePage <> nil) and (CurPageID = InstallModePage.ID) then
  begin
    IsReinstallMode := InstallModePage.SelectedValueIndex = 1;
    if not IsReinstallMode then
    begin
      EnablePreLaunchConfig := False;
      ChosenDataDir := ExistingDataRoot;
    end;
  end;
  if (DataLocationPage <> nil) and (CurPageID = DataLocationPage.ID) then
  begin
    ChosenDataDir := ResolveChosenDataDir();
    if (DataLocationPage.SelectedValueIndex = 2) and (ChosenDataDir = '') then
    begin
      MsgBox('Please choose a custom data folder.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  if (CustomDataDirPage <> nil) and (CurPageID = CustomDataDirPage.ID) then
  begin
    ChosenDataDir := ResolveChosenDataDir();
    if ChosenDataDir = '' then
    begin
      MsgBox('Please choose a custom data folder.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  if (PreLaunchConfigPage <> nil) and (CurPageID = PreLaunchConfigPage.ID) then
    EnablePreLaunchConfig := PreLaunchConfigPage.SelectedValueIndex = 0;
  if (TermsAgreementPage <> nil) and (CurPageID = TermsAgreementPage.ID) then
  begin
    AcceptedTerms := TermsAgreementPage.SelectedValueIndex = 0;
    if not AcceptedTerms then
    begin
      MsgBox('You must accept the Terms of Service and Privacy Policy to continue.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  if (StarterContentPage <> nil) and (CurPageID = StarterContentPage.ID) then
  begin
    IncludeStarterContent := StarterContentPage.SelectedValueIndex = 0;
  end;
  if (ThemePage <> nil) and (CurPageID = ThemePage.ID) then
  begin
    case ThemePage.SelectedValueIndex of
      1: SelectedTheme := 'light';
      2: SelectedTheme := 'dark';
    else
      SelectedTheme := 'system';
    end;
  end;
  if (CurrencyPage <> nil) and (CurPageID = CurrencyPage.ID) then
  begin
    case CurrencyPage.SelectedValueIndex of
      1: SelectedCurrency := 'EUR';
      2: SelectedCurrency := 'GBP';
      3: SelectedCurrency := 'CAD';
    else
      SelectedCurrency := 'USD';
    end;
  end;
  if (DashboardMoneyPage <> nil) and (CurPageID = DashboardMoneyPage.ID) then
    ShowMoneyOnDashboard := DashboardMoneyPage.SelectedValueIndex = 0;
  if (PrefInputPage <> nil) and (CurPageID = PrefInputPage.ID) then
  begin
    PromptIntervalSec := Trim(PrefInputPage.Values[0]);
    PromptFirstDelaySec := Trim(PrefInputPage.Values[1]);
    if (PromptIntervalSec = '') then PromptIntervalSec := '900';
    if (PromptFirstDelaySec = '') then PromptFirstDelaySec := '120';
  end;
  if (BusinessInfoPage <> nil) and (CurPageID = BusinessInfoPage.ID) then
  begin
    BusinessName := Trim(BusinessInfoPage.Values[0]);
    BusinessLegalName := Trim(BusinessInfoPage.Values[1]);
    BusinessOwner := Trim(BusinessInfoPage.Values[2]);
    BusinessTaxId := Trim(BusinessInfoPage.Values[3]);
  end;
  if (BusinessInfoPage2 <> nil) and (CurPageID = BusinessInfoPage2.ID) then
  begin
    BusinessEmail := Trim(BusinessInfoPage2.Values[0]);
    BusinessPhone := Trim(BusinessInfoPage2.Values[1]);
    BusinessWebsite := Trim(BusinessInfoPage2.Values[2]);
    BusinessAddress := Trim(BusinessInfoPage2.Values[3]);
    BusinessTimezone := Trim(BusinessInfoPage2.Values[4]);
    BusinessInvoiceNotes := Trim(BusinessInfoPage2.Values[5]);
    if BusinessTimezone = '' then
      BusinessTimezone := 'system';
  end;
  if (CurPageID = wpReady) then
  begin
    if ChosenDataDir = '' then
      ChosenDataDir := ResolveChosenDataDir();
    if (ExistingDataRoot <> '') and DirExists(ExistingDataRoot) and
       (CompareText(ExistingDataRoot, ChosenDataDir) <> 0) and
       (not DirExists(ChosenDataDir)) then
    begin
      MoveDataReply :=
        MsgBox(
          'Existing data was found at:' + #13#10 + ExistingDataRoot + #13#10 + #13#10 +
          'Move existing data to the new location now?' + #13#10 + ChosenDataDir,
          mbConfirmation,
          MB_YESNO
        );
      if MoveDataReply = IDYES then
      begin
        if not CopyDirTree(ExistingDataRoot, ChosenDataDir) then
        begin
          MsgBox('Could not copy existing data to the selected folder.', mbError, MB_OK);
          Result := False;
          Exit;
        end;
      end;
    end;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (CustomDataDirPage <> nil) and (PageID = CustomDataDirPage.ID) then
    Result := (DataLocationPage.SelectedValueIndex <> 2);
  if (InstallModePage <> nil) and (not IsReinstallMode) then
  begin
    if (DataLocationPage <> nil) and (PageID = DataLocationPage.ID) then Result := True;
    if (CustomDataDirPage <> nil) and (PageID = CustomDataDirPage.ID) then Result := True;
    if (PreLaunchConfigPage <> nil) and (PageID = PreLaunchConfigPage.ID) then Result := True;
    if (TermsViewPage <> nil) and (PageID = TermsViewPage.ID) then Result := True;
    if (PrivacyViewPage <> nil) and (PageID = PrivacyViewPage.ID) then Result := True;
    if (TermsAgreementPage <> nil) and (PageID = TermsAgreementPage.ID) then Result := True;
  end;
  if not EnablePreLaunchConfig then
  begin
    if (StarterContentPage <> nil) and (PageID = StarterContentPage.ID) then Result := True;
    if (ThemePage <> nil) and (PageID = ThemePage.ID) then Result := True;
    if (CurrencyPage <> nil) and (PageID = CurrencyPage.ID) then Result := True;
    if (DashboardMoneyPage <> nil) and (PageID = DashboardMoneyPage.ID) then Result := True;
    if (PrefInputPage <> nil) and (PageID = PrefInputPage.ID) then Result := True;
    if (BusinessInfoPage <> nil) and (PageID = BusinessInfoPage.ID) then Result := True;
    if (BusinessInfoPage2 <> nil) and (PageID = BusinessInfoPage2.ID) then Result := True;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssInstall) and (not OldVersionHandled) and (ExistingInstallDir <> '') and DirExists(ExistingInstallDir) then
  begin
    if (ExistingUninstallCmd <> '') and (not RunExistingUninstallerKeepData(ExistingUninstallCmd)) then
    begin
      MsgBox(
        'Could not uninstall the previous version automatically. ' +
        'Please close RootRecord and uninstall the old version, then run setup again.',
        mbError,
        MB_OK
      );
      Abort();
    end
    else if DirExists(ExistingInstallDir) then
    begin
      DelTree(ExistingInstallDir, True, True, True);
      ForceDirectories(ExistingInstallDir);
    end;
    OldVersionHandled := True;
  end;
  if CurStep = ssPostInstall then
  begin
    if ChosenDataDir = '' then
      ChosenDataDir := ResolveChosenDataDir();
    if ChosenDataDir <> '' then
    begin
      ForceDirectories(ChosenDataDir);
      ForceDirectories(ChosenDataDir + '\data');
      if not FileExists(ChosenDataDir + '\data\install_options.json') then
        SaveStringToFile(ChosenDataDir + '\data\install_options.json', BuildInstallOptionsJson() + #13#10, False);
      RegWriteStringValue(HKCU, 'Environment', 'ROOTRECORD_HOME', ChosenDataDir);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DeleteData: Integer;
  DataRoot: string;
begin
  if CurUninstallStep = usUninstall then
  begin
    if Pos('/KEEPDATA', Uppercase(GetCmdTail())) > 0 then
      Exit;
    DataRoot := '';
    RegQueryStringValue(HKCU, 'Environment', 'ROOTRECORD_HOME', DataRoot);
    DataRoot := Trim(DataRoot);
    if DataRoot = '' then
      DataRoot := ExpandConstant('{localappdata}\RootRecord');
    DeleteData :=
      MsgBox(
        'Do you also want to delete user data and directories?' + #13#10 + #13#10 +
        'This includes tracked data under:' + #13#10 +
        DataRoot,
        mbConfirmation,
        MB_YESNO
      );
    if DeleteData = IDYES then
    begin
      if DirExists(DataRoot) then
        DelTree(DataRoot, True, True, True);
      RegDeleteValue(HKCU, 'Environment', 'ROOTRECORD_HOME');
      if DirExists(ExpandConstant('{app}')) then
        DelTree(ExpandConstant('{app}'), True, True, True);
    end;
  end;
end;
