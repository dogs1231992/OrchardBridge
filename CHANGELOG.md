# Changelog

This project currently uses a date-based first-version label. All items below are part of the same initial release candidate:

```text
v1.2026.06.23
```

The entries are written as release notes for the first public version, not as a record of internal back-and-forth development iterations.

---

## v1.2026.06.23 — Initial release candidate

### Core purpose

OrchardBridge is a Windows desktop utility for backing up photos, videos, and full-device data from supported USB-connected phones to local storage. It provides a graphical workflow for users who prefer not to use command-line tools.

### User-facing features

- Device dashboard with device name, model, operating-system version, storage usage, free space, and battery information when available.
- Photo/video scan from connected devices.
- Thumbnail grid with filename, file size, selection state, filtering, sorting, select-all, and deselect-all controls.
- Selective photo/video backup to a user-selected local folder.
- Optional HEIC/HEIF conversion after backup.
- Full-device backup workflow through `pymobiledevice3`.
- HEIC/HEIF converter in the Toolbox page.
- Duplicate-file cleaner in the Toolbox page.
- Settings page for language, theme, font size, backup folders, conversion options, cache behavior, conflict handling, and logs.
- About page with project, author, repository, sponsorship, and bug-report links.

### HEIC/HEIF converter

- Accepts multiple files and folders.
- Supports drag-and-drop and file/folder picker workflows.
- Recursively scans dropped folders.
- Only accepts `.heic` and `.heif` files.
- Normalizes and deduplicates real file paths so that dragging both a parent folder and its subfolders does not double-count the same files.
- Supports conversion to configured output formats such as JPEG or PNG.

### Duplicate-file cleaner

- Accepts files and folders of any format.
- Supports drag-and-drop and file/folder picker workflows.
- Recursively scans folders.
- Deduplicates normalized real file paths before analysis.
- Groups duplicate files by SHA-256 hash.
- Uses the Recycle Bin by default when deleting duplicate files.

### Device detection and USB bridge notes

- Uses `pymobiledevice3` to communicate with supported USB-connected devices.
- Windows File Explorer visibility and `pymobiledevice3` access are not always the same path. File Explorer may see a device through Windows Portable Device / MTP, while OrchardBridge needs the Apple Mobile Device / usbmux bridge path for the current backend.
- If the bridge is missing, the app records diagnostics in the runtime log and shows a localized status message.
- If a device was previously detected and the USB cable is unplugged, the app treats this as a normal disconnection rather than a missing bridge installation.

### Source launcher behavior: `run_conda.bat`

- The source launcher creates a project-local `.venv` and installs dependencies there.
- It avoids installing packages into Anaconda base, system Python, or user site-packages.
- On some systems, the first run may need **Run as administrator** because Windows may block creation of `.venv` or dependency installation in the selected folder.
- After `.venv` is installed successfully, users should close OrchardBridge and run `run_conda.bat` again in **normal user mode**.
- Normal user mode is recommended for daily testing because Windows can block drag-and-drop from normal File Explorer into an administrator-mode app.
- Running in administrator mode may also interfere with normal Print Screen / Snipping Tool behavior depending on Windows security and keyboard settings.
- `.venv` is reused on later runs. Dependencies are installed again only when `.venv` is missing, damaged, `requirements.txt` changes, or `ORCHARD_BRIDGE_REPAIR=1` is set.

### PyInstaller / EXE packaging

- Includes `OrchardBridge.spec` for one-file EXE builds.
- Includes `build_onefile_exe.ps1` for a clean portable EXE build workflow.
- Uses the custom application icon at:

```text
assets\orchardbridge_icon.ico
```

- Bundles language files and icon assets with the PyInstaller build.
- The final EXE is generated under:

```text
release\OrchardBridge.exe
```

### Custom icon and branding

- Replaces the default Python executable icon with a custom OrchardBridge icon.
- The icon combines orchard/leaf imagery, bridge imagery, and backup/sync symbolism.
- The icon is intentionally not based on Apple or iPhone branding.
- The app window also attempts to use the same icon when running from source or from a PyInstaller bundle.

### Layout and UI behavior

- Uses a responsive Tkinter layout to reduce text clipping in large-font and multilingual interfaces.
- Sidebar and buttons are allowed to expand based on translated text.
- About-page links are separated from labels so only actual URLs/emails behave as links.
- Short pages such as **My device** no longer scroll into a large empty top area when their content already fits inside the viewport.
- Scrollable tabs still scroll when content is taller than the visible area.

### Localization

- Default language is English.
- Includes draft language packs for English, Traditional Chinese, Simplified Chinese, Japanese, Korean, Spanish, French, German, Brazilian Portuguese, Russian, Thai, Indonesian, and Arabic.
- Warning, confirmation, progress, completion, device-detection, and log/cache messages use translation keys instead of hard-coded single-language strings where practical.

### Runtime data and logs

- Settings are saved under `%APPDATA%\OrchardBridge\settings.json`.
- Cache and logs are saved under `%LOCALAPPDATA%\OrchardBridge`.
- The app can open the latest log file and delete log files from the Settings page.
- Bug-report helper can create an email draft with diagnostic text and log content.

### Documentation

- README provides a user-focused overview, quick-start steps, feature list, build instructions, paths, language information, and feedback links.
- README includes screenshots from `doc/source/images` to show the device dashboard, photo backup, scan results, full backup, toolbox, and settings.
- Technical source-run details are kept here in the changelog so the README can stay focused on the user-facing project overview.

---

# 更新紀錄（中文）

本專案目前使用日期式的第一版版本號。以下內容全部屬於同一個初始釋出候選版本：

```text
v1.2026.06.23
```

這裡的內容是第一個公開版本的整理，不是內部來回修改過程的版本紀錄。

---

## v1.2026.06.23 — 初始釋出候選版本

### 核心目的

OrchardBridge 是一套 Windows 桌面工具，用來將支援的 USB 連線手機中的照片、影片與完整裝置資料備份到本機儲存空間。它提供圖形化流程，讓使用者不需要操作命令列工具。

### 使用者可見功能

- 裝置首頁可顯示裝置名稱、型號、系統版本、儲存空間、可用空間與電量資訊。
- 可掃描連接裝置中的照片與影片。
- 縮圖網格顯示檔名、大小、選取狀態，並提供篩選、排序、全選與全部取消功能。
- 可將選取的照片與影片備份到使用者指定的本機資料夾。
- 可在備份後選擇性轉換 HEIC/HEIF 檔案。
- 透過 `pymobiledevice3` 提供完整裝置備份流程。
- 工具箱內建 HEIC/HEIF 轉檔器。
- 工具箱內建重複檔案清理器。
- 設定頁可管理語言、主題、字體大小、備份資料夾、轉檔選項、快取行為、檔名衝突處理與 log。
- 關於頁提供專案、作者、GitHub、贊助與 Bug 回報資訊。

### HEIC/HEIF 轉檔器

- 支援多個檔案與多個資料夾。
- 支援拖曳與檔案/資料夾選擇器。
- 拖入資料夾時會遞迴掃描所有子資料夾。
- 只接受 `.heic` 與 `.heif` 檔。
- 會正規化並去除重複實際路徑，避免同時拖入父資料夾與子資料夾時重複計算同一批檔案。
- 可依照設定轉成 JPEG 或 PNG 等輸出格式。

### 重複檔案清理器

- 支援任何格式的檔案與資料夾。
- 支援拖曳與檔案/資料夾選擇器。
- 拖入資料夾時會遞迴掃描所有子資料夾。
- 分析前會先去除重複實際路徑。
- 使用 SHA-256 hash 分組重複檔案。
- 預設會把刪除項目移到資源回收筒。

### 裝置偵測與 USB bridge 注意事項

- 使用 `pymobiledevice3` 與支援的 USB 連線裝置溝通。
- Windows 檔案總管看得到裝置，不一定代表 `pymobiledevice3` 也能連線。檔案總管可能走 Windows Portable Device / MTP，而 OrchardBridge 目前後端需要 Apple Mobile Device / usbmux bridge 路徑。
- 如果缺少 bridge，程式會將診斷資訊寫入 runtime log，並顯示多語系狀態訊息。
- 如果裝置曾經成功連線，之後 USB 被拔掉，程式會把它視為一般斷線，而不是誤判成 bridge 沒安裝。

### 原始碼啟動器：`run_conda.bat`

- 原始碼啟動器會在專案資料夾內建立 `.venv`，並把依賴套件安裝到該隔離環境中。
- 它會避免把套件安裝到 Anaconda base、系統 Python 或 user site-packages。
- 某些系統第一次執行時可能需要 **系統管理員身分**，因為 Windows 可能阻擋 `.venv` 建立或套件安裝。
- `.venv` 安裝成功後，請關閉 OrchardBridge，再用 **一般模式** 重新啟動 `run_conda.bat`。
- 日常測試建議使用一般模式，因為 Windows 可能會阻擋從一般權限的檔案總管拖曳到系統管理員模式的程式。
- 以系統管理員模式執行時，也可能依 Windows 安全與鍵盤設定影響 Print Screen / 剪取工具的正常行為。
- 後續執行會重複使用 `.venv`。只有 `.venv` 不存在、損壞、`requirements.txt` 改變，或設定 `ORCHARD_BRIDGE_REPAIR=1` 時才會重新安裝依賴。

### PyInstaller / EXE 打包

- 內含 `OrchardBridge.spec` 作為 one-file EXE 的 PyInstaller 設定。
- 內含 `build_onefile_exe.ps1` 進行乾淨的可攜式 EXE 建置流程。
- 使用自訂圖示：

```text
assets\orchardbridge_icon.ico
```

- PyInstaller 打包時會包含語言檔與 icon assets。
- 最終 EXE 會產生於：

```text
release\OrchardBridge.exe
```

### 自訂圖示與品牌感

- 將預設 Python 執行檔圖示替換成 OrchardBridge 自訂圖示。
- 圖示結合果園/葉子、橋樑與備份同步意象。
- 圖示刻意不使用 Apple 或 iPhone 官方品牌元素。
- 從原始碼或 PyInstaller bundle 執行時，視窗也會嘗試套用相同圖示。

### 版面與 UI 行為

- 使用較彈性的 Tkinter layout，降低大字體與多語系介面下文字被切掉的情況。
- 側邊欄與按鈕會依照翻譯文字自動調整。
- 關於頁的標籤與連結分開，只有真正的網址與 Email 會有超連結行為。
- **My device / 我的裝置** 這類內容較短的頁面，如果內容已經能完整顯示，就不會再往下滾到上方出現大片空白。
- 如果頁面內容比可視區域高，仍然會保留捲動功能。

### 多語系

- 預設語言為英文。
- 內建英文、繁體中文、簡體中文、日文、韓文、西班牙文、法文、德文、巴西葡萄牙文、俄文、泰文、印尼文與阿拉伯文初稿語言包。
- 警告、確認、進度、完成、裝置偵測與 log/cache 相關訊息會盡量使用翻譯 key，而不是硬編碼成單一語言。

### Runtime 資料與 log

- 設定檔儲存在 `%APPDATA%\OrchardBridge\settings.json`。
- 快取與 log 儲存在 `%LOCALAPPDATA%\OrchardBridge`。
- 設定頁可開啟最新 log，也可刪除 log 檔。
- Bug report helper 可建立包含診斷文字與 log 內容的 email 草稿。

### 文件

- README 提供使用者導向的專案介紹、快速開始、功能列表、打包方式、資料位置、語言資訊與回饋連結。
- README 會使用 `doc/source/images` 中的截圖展示裝置首頁、照片備份、掃描結果、完整備份、工具箱與設定頁。
- 較技術性的原始碼執行細節集中放在 CHANGELOG，讓 README 維持比較綜觀的專案介紹。
