# TW-Stock-Widget

Windows 桌面股價小工具，顯示臺灣加權指數、上市與上櫃股票行情，並在達到自訂漲跌幅門檻時提醒。

## 功能

- 顯示 TAIEX、上市（TWSE）與上櫃（OTC）股票即時行情。
- 顯示加權指數下方的臺指期 TX 即時漲跌點數、最新價、參考價與當盤高低點。
- 支援正向與負向漲跌幅門檻，例如 `+5` 代表上漲 5% 提醒，`-3` 代表下跌 3% 提醒。
- 可設定更新間隔（10 至 600 秒）、監控代碼、門檻與視窗置頂。
- 支援深色模式；現價與漲跌幅採漲紅、跌綠、持平灰顯示。
- 視窗可拖曳；提醒會顯示右下角通知、使用 Windows 系統音效，並在狀態列保留最後通知。
- 同一股票、交易日與門檻只提醒一次，避免重複通知。
- 設定會安全清理並以暫存檔原子替換方式保存。

## 資料來源

行情來自臺灣證券交易所 MIS 官方 API：

`https://mis.twse.com.tw/stock/api/getStockInfo.jsp`

臺指期即時行情來自臺灣期貨交易所期貨交易資訊觀測站：

`https://www.taifex.com.tw/eventTaifexTradingCenter/cht/index.do`

程式會依目前交易時段查詢臺股期貨即時行情，顯示期交所回傳的最新成交價相對參考價之漲跌點數；日盤與夜盤使用不同的官方行情服務。

程式會依代碼查詢：

- TAIEX：`tse_t00.tw`
- 上市股票：`tse_<代碼>.tw`
- 上櫃股票：`otc_<代碼>.tw`

API 回傳的前一日收盤價與最新價用於計算漲跌幅。資料內容、交易時間與行情正確性以 TWSE MIS 官方服務為準；程式本身不提供交易、下單或投資建議功能。

Python 3.14 的 TLS 嚴格驗證會拒絕 TWSE 憑證鏈缺少的 SKI extension，因此程式只移除 `VERIFY_X509_STRICT` 旗標；憑證驗證（`CERT_REQUIRED`）與主機名稱驗證仍然保持啟用，不會使用 `CERT_NONE`。

## 程式內容

- `stock_widget.py`：Tkinter 介面、設定清理、TWSE/TAIFEX API 請求、行情解析、臺指期波動計算、漲跌幅判斷與通知。
- `test_stock_widget.py`：代碼正規化、行情解析、門檻、TLS 與設定保存測試。
- `start_widget.cmd`：以 `pythonw.exe` 啟動原始碼版本。
- `可攜版/`：PyInstaller 建立的 Windows 可攜版執行檔與說明。

本專案只使用 Python 標準庫，包括 Tkinter、urllib、ssl、json、threading 與 pathlib；原始碼不需要額外第三方套件。

## 設定檔位置

原始碼執行時：

`%LOCALAPPDATA%\\TWStockWidget\\settings.json`

可攜版執行時，啟動檔會設定 `TWSTOCKWIDGET_SETTINGS_PATH`，設定保存於 exe 同層的 `settings.json`，因此可將整個資料夾複製到其他 Windows 電腦。

## 使用原始碼

需要 Windows、Python 3 與 Tkinter：

```powershell
.\start_widget.cmd
```

或直接執行：

```powershell
python stock_widget.py
```

測試：

```powershell
python -m unittest -v
python -m py_compile stock_widget.py test_stock_widget.py
python stock_widget.py --smoke-test
```

## 可攜版

從 [Releases](https://github.com/HyDroGen2528/TW-Stock-Widget/releases) 下載 `TWStockWidget-portable.zip`，解壓縮後執行 `TWStockWidget.exe` 或 `start_widget.cmd`。可攜版已包含 Python 與 Tk runtime，不需要另外安裝 Python；行情查詢仍需要網路連線。

可由開發環境重新建立：

```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name TWStockWidget --distpath 可攜版 stock_widget.py
```

## 授權與限制

目前未附加特定開源授權。程式針對 Windows 設計，使用 TWSE MIS 服務時需遵守該服務的使用規範與可用性限制。
