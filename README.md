<p align="center"><img src="src/get_design_markdown/ui/static/favicon.svg" width="96" height="96" alt="Logo Get-Design-Markdown"></p>

# Get-Design-Markdown

Công cụ nhận vào URL một trang web, phân tích ngôn ngữ thiết kế của trang (màu sắc, typography,
spacing, bo góc, đổ bóng, breakpoint, component…) rồi xuất ra **`DESIGN.md`** theo
[spec DESIGN.md của Google Labs](https://github.com/google-labs-code/design.md): một file markdown
mô tả design system để AI coding agent (Claude Code, Cursor, v0, Gemini CLI…) đọc và dựng UI theo
phong cách tương tự.

Có ba cách dùng, cả ba chạy chung một bộ phân tích:

| Cách dùng | Phù hợp với | Cần chuẩn bị |
|---|---|---|
| **Bản exe cho Windows**: tải về, giải nén, bấm đúp | Designer, người không rành kỹ thuật | Windows 10/11 64-bit, không cần cài gì thêm |
| **Giao diện đồ họa** `gdm ui`: điền URL, bấm nút, xem kết quả | Ai thích bấm nút hơn gõ lệnh | Python ≥ 3.11 và mã nguồn hoặc file wheel |
| **Dòng lệnh** `gdm get <url>` | Lập trình viên, script, CI | Python ≥ 3.11 và mã nguồn hoặc file wheel |

```bash
gdm get https://stripe.com --open
```

> **Mục đích: tham khảo và học hỏi phong cách.** Công cụ chỉ đo thông số style (màu, cỡ chữ,
> khoảng cách…) và chụp ảnh màn hình để phân tích. Nó không tải về logo, hình ảnh hay file font
> của trang đích, và `DESIGN.md` luôn ghi rõ đây là tài liệu tham khảo, không phải design system
> chính thức của trang. Đừng dùng kết quả để sao chép thương hiệu hay tài sản có bản quyền.

## Mục lục

- [Bắt đầu nhanh](#bắt-đầu-nhanh)
- [Tính năng](#tính-năng)
- [Cách hoạt động](#cách-hoạt-động)
- [Tải bản exe cho Windows](#tải-bản-exe-cho-windows)
- [Giao diện đồ họa](#giao-diện-đồ-họa)
- [Trình duyệt](#trình-duyệt)
- [Nơi lưu dữ liệu](#nơi-lưu-dữ-liệu)
- [Yêu cầu](#yêu-cầu)
- [Cài đặt](#cài-đặt)
- [Dùng nhanh với dòng lệnh](#dùng-nhanh-với-dòng-lệnh)
- [Các lệnh](#các-lệnh)
- [Thư mục kết quả](#thư-mục-kết-quả)
- [Định dạng DESIGN.md](#định-dạng-designmd)
- [Dành cho lập trình viên](#dành-cho-lập-trình-viên)
- [Dùng với Claude Code / claude.ai: `--prompt-only`](#dùng-với-claude-code--claudeai---prompt-only)
- [tokens.json và preview.html](#tokensjson-và-previewhtml)
- [Biến môi trường](#biến-môi-trường)
- [Kiểm tra kết quả](#kiểm-tra-kết-quả)
- [Xử lý lỗi thường gặp](#xử-lý-lỗi-thường-gặp)
- [Đạo đức và robots.txt](#đạo-đức-và-robotstxt)
- [Phát triển](#phát-triển)
- [Thay đổi](#thay-đổi)
- [Giấy phép](#giấy-phép)

## Bắt đầu nhanh

### Designer và người không rành kỹ thuật (Windows)

1. Vào trang [Releases](https://github.com/hasoftware/Get-Design-Markdown/releases), tải file
   `Get-Design-Markdown-<phiên-bản>-windows-x64.zip` của bản mới nhất.
2. Chuột phải file zip → **Extract All…** (**Giải nén tất cả…**).
3. Mở thư mục vừa giải nén, bấm đúp **`Get-Design-Markdown.exe`**. Nếu Windows hiện "Windows
   protected your PC", bấm **More info** → **Run anyway** (xem
   [Tải bản exe cho Windows](#tải-bản-exe-cho-windows)).
4. Dán địa chỉ trang web cần học hỏi (ví dụ `stripe.com`) rồi bấm **Phân tích**. Thường mất
   khoảng một đến vài phút.
5. Xem kết quả theo từng tab: **Xem trước** (bảng màu, kiểu chữ, nút, thẻ…), **DESIGN.md** (sao
   chép hoặc tải về), **Dùng với AI** (cách đưa cho Claude, Cursor, v0…), **Ảnh chụp**, **Tệp**.

Không cần cài Python, không cần API key. Công cụ chỉ mở trang web bạn chọn và lưu kết quả trên
máy của bạn.

### Lập trình viên (vibe code với AI)

```bash
git clone https://github.com/hasoftware/Get-Design-Markdown.git
cd Get-Design-Markdown
uv sync
uv run gdm get https://stripe.com        # → output/stripe.com/DESIGN.md
```

Sau đó đưa `DESIGN.md` cho agent (xem [Dành cho lập trình viên](#dành-cho-lập-trình-viên)). Muốn
bấm nút thay vì gõ lệnh thì chạy `uv run gdm ui`. Máy chưa có Microsoft Edge hay Google Chrome thì
chạy thêm `uv run playwright install chromium` một lần (xem [Trình duyệt](#trình-duyệt)).

## Tính năng

- **Ba cách dùng chung một lõi**: bản exe cho Windows (cửa sổ riêng, không cần cài), giao diện đồ
  họa `gdm ui` chạy trong trình duyệt, và dòng lệnh `gdm`. Giao diện chạy đúng pipeline của
  `gdm get`: cùng tùy chọn và cùng trình duyệt thì cho cùng `DESIGN.md`.
- **Giao diện tiếng Việt** cho người không rành kỹ thuật: mỗi tùy chọn có chú thích dễ hiểu, theo
  dõi tiến trình từng bước và hủy được giữa chừng, xem trước kết quả, sao chép DESIGN.md bằng một
  nút, lịch sử các trang đã phân tích; dùng được bằng bàn phím, theo theme sáng/tối của hệ thống.
- **Thu thập bằng trình duyệt thật** (nhân Chromium chạy ẩn: Chromium của Playwright, hoặc
  Microsoft Edge / Google Chrome có sẵn trên máy; viewport 1440×900): chờ trang tải xong,
  cuộn hết trang để kích hoạt lazy-load, đọc CSS custom properties, computed style của tối đa 3000
  phần tử hiển thị kèm ngữ cảnh (thẻ, role, class, kích thước, vị trí, đoạn text ngắn).
- **Đọc cả stylesheet khác domain**: bắt response mọi file CSS và phân tích bằng tinycss2 để lấy
  `@media` breakpoint, `@font-face` và biến CSS.
- **Trạng thái hover** của tối đa 10 nút và 10 link nổi bật; **ảnh chụp** ở 3 viewport (1440,
  768, 390) kèm số đo layout để suy ra hành vi responsive; **dark mode** với `--dark`.
- **Phân tích thuần code, không dùng AI**: chuẩn hóa màu về hex + alpha, gom cụm bằng ΔE
  CIEDE2000 theo từng kênh (chữ / nền / viền), tách màu trang trí, đặt tên token theo vai trò
  (`primary`, `canvas`, `surface`, `ink`, `muted`, `hairline`…), tính tương phản WCAG; type scale
  với tên cấp ngữ nghĩa; spacing scale và đơn vị cơ sở; các cấp bo góc và đổ bóng; component
  (button, link, input, card, navigation, badge, footer) cùng biến thể và trạng thái hover.
- **DESIGN.md dựng bằng luật**: mọi con số và mã màu lấy từ dữ liệu đo được, mọi tham chiếu
  `{colors.primary}` đều được tự kiểm; các cặp màu dưới chuẩn WCAG AA được ghi rõ kèm tỉ lệ.
  Viết bằng tiếng Anh (mặc định) hoặc tiếng Việt (`--lang vi`).
- **Không gọi API, không cần API key**: muốn văn phong hay hơn thì dùng `--prompt-only` để tự dán
  vào Claude Code hoặc claude.ai.
- **Xuất thêm** `tokens.json` (W3C Design Tokens) và `preview.html` (trang xem trước ô màu,
  typography, component, nút chuyển light/dark).
- **`--pages N`**: tự tìm thêm N trang con quan trọng từ navigation và gộp dữ liệu.
- Mỗi tầng lưu kết quả ra JSON nên có thể **chạy lại từng tầng độc lập** (ví dụ chỉnh ngưỡng gom
  màu rồi phân tích lại mà không phải mở lại trình duyệt).

## Cách hoạt động

```
URL ──collect──▶ raw.json + screenshots/ ──analyze──▶ analysis.json ──synthesize──▶ DESIGN.md
     (Tầng 1)                              (Tầng 2)        │          (Tầng 3)      └─ --prompt-only ─▶ prompt.md + ảnh
                                                           └──export──▶ tokens.json, preview.html
```

| Tầng | Lệnh | Việc làm | Kết quả |
|---|---|---|---|
| 1 — Collector | `gdm collect <url>` | Kiểm tra robots.txt, mở trang bằng trình duyệt chạy ẩn, thu thập style, CSS, hover, ảnh chụp | `raw.json`, `screenshots/` |
| 2 — Analyzer | `gdm analyze <thư-mục>` | Gom cụm và phân loại màu, typography, spacing, bo góc, đổ bóng, breakpoint, layout, component | `analysis.json` + bảng tóm tắt |
| 3 — Synthesizer | `gdm synthesize <thư-mục>` | Dựng DESIGN.md bằng luật suy diễn + template, tự kiểm trước khi ghi | `DESIGN.md`, `synthesis.json` |
| Xuất | `gdm export <thư-mục>` | Design tokens và trang xem trước | `tokens.json`, `preview.html` |

`gdm get <url>` chạy cả bốn bước liền nhau. Dữ liệu giữa các tầng là các model Pydantic lưu
thành JSON, mỗi file có `schema_version`. Giao diện đồ họa (`gdm ui` và bản exe) là một máy chủ web
nhỏ chạy trên chính máy bạn: nó gọi cùng pipeline đó và hiển thị tiến trình, kết quả.

## Tải bản exe cho Windows

Mỗi bản phát hành trên [GitHub Releases](https://github.com/hasoftware/Get-Design-Markdown/releases)
có file **`Get-Design-Markdown-<phiên-bản>-windows-x64.zip`** cho Windows 10/11 64-bit. Bản exe đã
gói sẵn mọi thứ cần thiết: không cần cài đặt, không cần Python, không cần quyền quản trị. File
zip được build tự động trên GitHub Actions từ đúng mã nguồn của phiên bản đó, và chỉ được đính kèm
vào bản phát hành sau khi bài tự kiểm tra của exe đạt.

1. Ở bản phát hành mới nhất, mở mục **Assets** và bấm vào file
   `Get-Design-Markdown-<phiên-bản>-windows-x64.zip` để tải về.
2. Chuột phải file zip → **Extract All…** (**Giải nén tất cả…**) → chọn nơi đặt chương trình, ví
   dụ `Documents\Apps`. Đừng chạy exe ngay bên trong file zip: chương trình cần cả thư mục
   `_internal` nằm cạnh nó. Chọn chỗ có đường dẫn ngắn: đường dẫn quá dài (khoảng 200 ký tự trở
   lên) khiến Windows không nạp được thư viện và chương trình báo không khởi động được.
3. Mở thư mục `Get-Design-Markdown` vừa giải nén và bấm đúp **`Get-Design-Markdown.exe`**. Giao
   diện mở trong một cửa sổ riêng; đóng cửa sổ là thoát chương trình.

### Windows báo "Windows protected your PC"

Bản exe **chưa được ký số** (chứng chỉ ký mã tốn phí hằng năm), nên Microsoft Defender SmartScreen
có thể chặn ở lần chạy đầu:

1. Bấm **More info** (Windows tiếng Việt: **Thông tin thêm**).
2. Bấm **Run anyway** (**Vẫn chạy**).

Windows ghi nhớ lựa chọn này cho file exe đó; mỗi phiên bản mới có thể hỏi lại một lần.

### Phần mềm diệt virus cảnh báo

Chương trình đóng gói bằng PyInstaller và chưa ký số đôi khi bị phần mềm diệt virus nhận nhầm là
mã độc (false positive). Khi gặp cảnh báo:

- chỉ tải file từ trang Releases của dự án, không tải bản do người khác gửi lại;
- có thể kiểm tra file zip trên [VirusTotal](https://www.virustotal.com/);
- mã nguồn công khai, bạn có thể tự build exe (xem [Đóng gói bản exe](#đóng-gói-bản-exe));
- nếu đã chắc chắn file đúng nguồn, thêm thư mục chương trình vào danh sách loại trừ của phần mềm
  diệt virus thay vì tắt hẳn phần mềm đó.

### Lần chạy đầu tiên

- **Cửa sổ giao diện** dùng Microsoft Edge WebView2, có sẵn trên Windows 10/11 được cập nhật. Nếu
  máy thiếu WebView2, chương trình tự mở giao diện trong trình duyệt mặc định.
- **Trình duyệt để mở trang cần phân tích**: chương trình tự dùng Microsoft Edge (có sẵn trên
  Windows) hoặc Google Chrome, chạy ẩn. Nếu máy không có cả hai, giao diện báo "Chưa có trình
  duyệt để mở trang" kèm nút **Tải Chromium** (một lần: tải về khoảng 125 MB, chiếm khoảng 300 MB
  trên ổ đĩa). Xem [Trình duyệt](#trình-duyệt).
- **Kết quả** được lưu ở `Documents\Get-Design-Markdown\<tên-miền>\`. Đổi thư mục này trong
  **Tùy chọn nâng cao** → **Thư mục lưu kết quả**; nút **Mở thư mục** ở tab **Tệp** mở thẳng thư
  mục kết quả trong File Explorer.

### Cập nhật và gỡ bỏ

- **Cập nhật**: tải zip của bản mới và giải nén ra một thư mục mới (hoặc xóa thư mục cũ rồi giải
  nén vào đó). Cấu hình và kết quả cũ vẫn được giữ.
- **Gỡ bỏ**: xóa thư mục chương trình. Muốn xóa sạch thì xóa thêm các thư mục ở mục
  [Nơi lưu dữ liệu](#nơi-lưu-dữ-liệu).

### Tùy chọn dòng lệnh của exe

Thường chỉ cần bấm đúp. Khi cần, có thể chạy exe kèm tùy chọn trong PowerShell hoặc Command Prompt:

| Lệnh | Ý nghĩa |
|---|---|
| `Get-Design-Markdown.exe` | Mở giao diện trong cửa sổ riêng (thiếu WebView2 thì mở tab trình duyệt). |
| `Get-Design-Markdown.exe --browser-tab` | Mở giao diện trong trình duyệt mặc định thay vì cửa sổ riêng. |
| `Get-Design-Markdown.exe --port 8765` | Dùng cổng cố định cho máy chủ cục bộ (mặc định chọn một cổng trống). |
| `Get-Design-Markdown.exe --self-test` | Tự kiểm tra rồi thoát, không mở cửa sổ nào; ghi báo cáo `self-test.json` vào thư mục nhật ký (đổi nơi ghi bằng `--self-test-report <đường-dẫn>`). |

Exe không có cửa sổ console nên không in gì ra màn hình. Muốn biết kết quả tự kiểm tra (mã thoát
`0` là mọi bước đều đạt), chạy trong PowerShell từ thư mục chương trình:

```powershell
$p = Start-Process -FilePath .\Get-Design-Markdown.exe -ArgumentList '--self-test' -Wait -PassThru
$p.ExitCode
Get-Content "$env:LOCALAPPDATA\get-design-markdown\logs\self-test.json"
```

Bài tự kiểm tra lần lượt: nạp mọi module, đọc mọi tài nguyên đóng gói, khởi động máy chủ giao diện
và gọi thử, mở trình duyệt (`auto`) trên một trang trống, hiển thị thử một DESIGN.md mẫu. Khi báo
lỗi, hãy gửi kèm file này và `ui.log` (xem [Nơi lưu dữ liệu](#nơi-lưu-dữ-liệu)).

## Giao diện đồ họa

Giao diện là một trang web chạy trên chính máy bạn, viết tiếng Việt, hiển thị tốt từ màn hình
rộng 1024px, theo theme sáng/tối của hệ thống và dùng được hoàn toàn bằng bàn phím.

| Cách mở | Lệnh |
|---|---|
| Bản exe | Bấm đúp `Get-Design-Markdown.exe` (cửa sổ riêng) |
| Từ mã nguồn hoặc wheel, trong tab trình duyệt | `gdm ui` |
| Từ mã nguồn, trong cửa sổ riêng | `uv run --extra gui gdm ui --window` |

### Lệnh `gdm ui`

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--port <cổng>` | `0` | Cổng của máy chủ cục bộ `127.0.0.1`; `0` là tự chọn một cổng trống. |
| `--window` | tắt | Mở trong cửa sổ riêng thay vì tab trình duyệt. Cần extra `gui` (gói pywebview): từ mã nguồn dùng `uv run --extra gui gdm ui --window`, từ wheel cài `pip install "get_design_markdown-<phiên-bản>-py3-none-any.whl[gui]"`. Thiếu pywebview hoặc WebView2 thì tự quay về tab trình duyệt. |
| `--no-open` | tắt | Không tự mở gì, chỉ in địa chỉ (đã kèm mã phiên) để bạn tự mở; dừng bằng `Ctrl+C`. Không dùng cùng `--window`. |
| `--out`, `-o <thư-mục>` | theo cấu hình | Thư mục gốc chứa kết quả cho phiên này. Khi có `--out`, tùy chọn **Thư mục lưu kết quả** trong giao diện bị khóa. |

`gdm ui` in địa chỉ trên terminal và cách dừng. Máy chủ tự tắt khoảng 2 phút sau khi bạn đóng tab
giao diện (khi không còn phân tích hay tải Chromium nào đang chạy); `Ctrl+C` dừng ngay. Với cửa sổ
riêng (bản exe, `--window`), đóng cửa sổ là dừng; một lượt phân tích đang chạy sẽ được hủy.

### Các màn hình

- **Hướng dẫn lần đầu**: 3 bước (dán địa chỉ trang web → bấm **Phân tích** → xem và sao chép
  DESIGN.md cho AI) kèm lời nhắc chỉ dùng để tham khảo; mở lại được bằng nút **Hướng dẫn**.
- **Màn hình chính**: ô địa chỉ (gõ `stripe.com` là đủ), nút **Phân tích** (hoặc phím Enter), ba
  tùy chọn nhanh và phần **Tùy chọn nâng cao** gập lại. Lựa chọn của bạn được lưu cho lần sau.
- **Tiến trình**: 4 bước *Thu thập → Phân tích → DESIGN.md → Xuất file*, nhật ký từng việc đang
  làm, cảnh báo robots.txt hiện rõ, nút **Hủy phân tích**. Khi lỗi, giao diện hiện lý do kèm gợi ý cách xử
  lý (ví dụ tăng thời gian chờ, chọn trang khác, tải Chromium).
- **Kết quả** theo tab:
  - **Xem trước**: `preview.html` với ô màu, kiểu chữ, nút, thẻ, ô nhập dựng lại từ số đo;
  - **DESIGN.md**: bản hiển thị đẹp, nút **Sao chép DESIGN.md** và **Tải về**;
  - **Dùng với AI**: hướng dẫn ngắn cho Claude Code, Cursor, v0…, prompt mẫu và `prompt.md` nếu
    có;
  - **Ảnh chụp**: ảnh từng trang ở 1440, 768, 390px (và bản tối nếu có);
  - **Tệp**: danh sách file kèm dung lượng, nút **Mở thư mục**.
- **Lịch sử** (thanh bên): mọi trang đã phân tích trong thư mục kết quả, bấm để mở lại; nút
  **Phân tích trang mới** quay về màn hình chính.

### Tùy chọn trong giao diện

| Tùy chọn | Giới hạn | Tương đương dòng lệnh |
|---|---|---|
| Số trang con | 0–10 | `--pages` |
| Chụp thêm bản tối | bật/tắt | `--dark` |
| Ngôn ngữ của DESIGN.md | English, Tiếng Việt | `--lang en\|vi` |
| Xuất thêm prompt.md | bật/tắt | `--prompt-only` |
| Thêm khối ví dụ (ex-\*) | bật/tắt | `--examples` |
| Tự mở kết quả khi xong | bật/tắt | Chỉ có trong giao diện: tắt thì ở lại màn hình tiến trình, bấm **Xem kết quả** khi muốn |
| Số phần tử đo trên mỗi trang | 100–20000 | `--max-elements`, `GDM_MAX_ELEMENTS` |
| Thời gian chờ tải trang (giây) | 5–600 | `GDM_TIMEOUT` |
| Ngưỡng gom màu (ΔE) | 0.5–20 | `gdm analyze --delta-e` |
| Trình duyệt dùng để mở trang | Tự động, Chromium, Edge, Chrome | `--browser`, `GDM_BROWSER` |
| Thư mục lưu kết quả | — | `--out`, `GDM_OUTPUT_DIR` |

Các tùy chọn được lưu tự động cho lần sau; nút **Đặt lại tùy chọn về mặc định** ở cuối phần
**Tùy chọn nâng cao** đưa chúng về giá trị ban đầu (thư mục lưu kết quả giữ nguyên).

### An toàn

- Máy chủ chỉ nghe ở `127.0.0.1`: máy khác trong mạng không truy cập được.
- Mỗi lần mở có một mã phiên ngẫu nhiên; trang web khác đang mở trong trình duyệt không gọi được
  giao diện. Nếu giao diện báo "Phiên làm việc đã hết hạn", hãy chạy lại `gdm ui` hoặc mở lại exe.
- `preview.html` và DESIGN.md được hiển thị trong khung cách ly (sandbox), nên nội dung lấy từ
  trang web khác không chạy được trong giao diện.
- Bản thân giao diện không tải script hay font nào từ Internet. Ra mạng chỉ có trang bạn phân tích
  và, khi xem trước, stylesheet Google Fonts tùy chọn của `preview.html` (offline thì dùng font dự
  phòng).

## Trình duyệt

Công cụ mở trang cần phân tích bằng một trình duyệt nhân Chromium chạy ẩn (headless):

| Lựa chọn | Ý nghĩa |
|---|---|
| `auto` (mặc định) | Thử lần lượt Chromium của Playwright → Microsoft Edge → Google Chrome, dùng cái đầu tiên có trên máy. |
| `chromium` | Chỉ dùng Chromium do Playwright tải về. |
| `msedge` | Chỉ dùng Microsoft Edge đã cài trên máy. |
| `chrome` | Chỉ dùng Google Chrome đã cài trên máy. |

Chọn bằng tùy chọn **Trình duyệt** trong giao diện, cờ `--browser` của `gdm collect` / `gdm get`,
hoặc biến môi trường `GDM_BROWSER`:

```bash
gdm get https://stripe.com --browser msedge
GDM_BROWSER=chrome gdm get https://stripe.com
```

- Windows 10/11 có sẵn Edge nên thường không phải tải gì thêm.
- Máy không có trình duyệt nào phù hợp: bấm **Tải Chromium** trong **Tùy chọn nâng cao** của giao
  diện, hoặc chạy `uv run playwright install chromium` (Linux: thêm `--with-deps`). Chromium được
  tải một lần vào thư mục người dùng (xem [Nơi lưu dữ liệu](#nơi-lưu-dữ-liệu)).
- `raw.json` ghi tên và phiên bản trình duyệt thực sự đã dùng (trường `browser`, ví dụ
  `msedge 141.0.3537.71`) để chạy lại cho giống. Edge, Chrome và Chromium cùng nhân nên kết quả gần
  như giống nhau; chênh lệch nhỏ có thể đến từ phiên bản trình duyệt hoặc font có sẵn trên máy.

## Nơi lưu dữ liệu

| Dữ liệu | Windows | macOS | Linux |
|---|---|---|---|
| Kết quả của bản exe | `Documents\Get-Design-Markdown\<tên-miền>\` | — | — |
| Kết quả của `gdm` và `gdm ui` | `GDM_OUTPUT_DIR`, mặc định `output\` trong thư mục chạy lệnh (với `gdm ui`, đổi được trong giao diện hoặc bằng `--out`) | như Windows | như Windows |
| Cấu hình giao diện | `%APPDATA%\get-design-markdown\ui-settings.json` | `~/Library/Application Support/get-design-markdown/ui-settings.json` | `~/.config/get-design-markdown/ui-settings.json` |
| Dữ liệu của cửa sổ giao diện (lựa chọn sáng/tối, đã đọc hướng dẫn) | `%APPDATA%\get-design-markdown\webview\` | `~/Library/Application Support/get-design-markdown/webview/` | `~/.config/get-design-markdown/webview/` |
| Nhật ký `ui.log` của bản exe, báo cáo `self-test.json` | `%LOCALAPPDATA%\get-design-markdown\logs\` | `~/Library/Logs/get-design-markdown/` | `~/.local/state/get-design-markdown/logs/` |
| Chromium tải bằng nút **Tải Chromium** | `%LOCALAPPDATA%\ms-playwright\` | `~/Library/Caches/ms-playwright/` | `~/.cache/ms-playwright/` |

- Thư mục `Documents` là thư mục Tài liệu thật của bạn (kể cả khi đã chuyển vào OneDrive).
- Trên Linux, `$XDG_CONFIG_HOME` và `$XDG_STATE_HOME` được tôn trọng nếu có đặt.
- Có thể chuyển cấu hình và nhật ký sang nơi khác bằng `GDM_UI_CONFIG_DIR` và `GDM_UI_LOG_DIR`
  (xem [Biến môi trường](#biến-môi-trường)).

## Yêu cầu

**Bản exe**: Windows 10/11 64-bit; Microsoft Edge (có sẵn) hoặc Google Chrome, nếu không có thì
tải Chromium ngay trong giao diện; kết nối Internet tới trang cần phân tích.

**Từ mã nguồn hoặc wheel** (`gdm`, `gdm ui`):

- **Python ≥ 3.11** (CI kiểm thử 3.11, 3.12, 3.13 trên Linux và Windows).
- **[uv](https://docs.astral.sh/uv/)** để quản lý môi trường và dependency.
- **Một trình duyệt nhân Chromium**: Microsoft Edge hoặc Google Chrome đã cài, hoặc Chromium của
  Playwright (tải một lần bằng lệnh `playwright install chromium`).
- Kết nối Internet tới trang cần phân tích.
- Tùy chọn: **Node.js ≥ 18** để chạy linter chính thức của spec DESIGN.md; extra `gui` (pywebview)
  để mở giao diện trong cửa sổ riêng.

## Cài đặt

### Từ mã nguồn (khuyến nghị)

```bash
git clone https://github.com/hasoftware/Get-Design-Markdown.git
cd Get-Design-Markdown
uv sync                                  # tạo .venv và cài dependency theo uv.lock
uv run playwright install chromium       # tải Chromium cho Playwright (bỏ qua nếu dùng Edge/Chrome)
uv run gdm --version
```

Trên Linux, nếu Chromium báo thiếu thư viện hệ thống, chạy
`uv run playwright install --with-deps chromium` (cần quyền sudo). Muốn mở giao diện trong cửa sổ
riêng (`gdm ui --window`, `gdm-ui`), cài thêm extra `gui`: `uv sync --extra gui`.

> **Ghi chú Windows:** nếu cài uv bằng `pip install --user uv` thì lệnh `uv` có thể không nằm
> trên PATH. Khi đó thay `uv` bằng `python -m uv` trong mọi lệnh, ví dụ
> `python -m uv sync`, `python -m uv run gdm get https://stripe.com`.

Mọi lệnh bên dưới viết ngắn là `gdm …`; nếu chạy trong thư mục mã nguồn thì dùng `uv run gdm …`
(hoặc kích hoạt `.venv`). Lệnh đầy đủ `get-design-markdown` và `python -m get_design_markdown`
tương đương với `gdm`.

### Từ bản phát hành

Mỗi bản phát hành trên [GitHub Releases](https://github.com/hasoftware/Get-Design-Markdown/releases)
có file wheel, sdist và bản exe cho Windows (xem
[Tải bản exe cho Windows](#tải-bản-exe-cho-windows)). Gói không được đăng lên PyPI; cài wheel vào
một môi trường ảo riêng:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate   ·   Windows: .venv\Scripts\activate
pip install get_design_markdown-<phiên-bản>-py3-none-any.whl
python -m playwright install chromium     # bỏ qua nếu dùng Edge/Chrome có sẵn
gdm --version
```

Muốn mở giao diện trong cửa sổ riêng (`gdm ui --window`), cài wheel kèm extra `gui`:
`pip install "get_design_markdown-<phiên-bản>-py3-none-any.whl[gui]"`.

## Dùng nhanh với dòng lệnh

```bash
gdm get https://stripe.com --open
```

Lệnh này thu thập trang, phân tích, dựng `output/stripe.com/DESIGN.md`, xuất `tokens.json` và
`preview.html` rồi mở trang xem trước trong trình duyệt. Một vài biến thể thường dùng:

```bash
gdm get https://linear.app --dark --pages 2          # thêm bản dark mode và 2 trang con
gdm get https://stripe.com --lang vi                 # phần mô tả bằng tiếng Việt
gdm get https://stripe.com --prompt-only             # thêm prompt.md + ảnh để tự dán vào Claude
gdm get https://stripe.com --out ~/design-refs       # đổi thư mục gốc chứa kết quả
```

Sau đó đưa `DESIGN.md` cho AI coding agent, ví dụ trong Claude Code:
*"Đọc output/stripe.com/DESIGN.md và dựng trang pricing theo đúng các token trong đó."* Cách làm
với từng công cụ AI nằm ở mục [Dành cho lập trình viên](#dành-cho-lập-trình-viên).

## Các lệnh

Tùy chọn chung đặt **trước** tên lệnh:

| Tùy chọn | Ý nghĩa |
|---|---|
| `--debug` | Hiện traceback đầy đủ khi gặp lỗi (mặc định chỉ in thông báo lỗi tiếng Việt kèm gợi ý). |
| `--version` | In phiên bản rồi thoát. |
| `--help` | Trợ giúp của chương trình hoặc của từng lệnh (`gdm get --help`). |

Ví dụ: `gdm --debug get https://stripe.com`.

Các lệnh `analyze`, `synthesize` và `export` nhận **thư mục kết quả** (`output/stripe.com`) hoặc
**URL / tên miền đã thu thập** (`stripe.com`, được tìm trong thư mục gốc `GDM_OUTPUT_DIR`).

### `gdm collect <url>` — Tầng 1: thu thập

Kiểm tra robots.txt, mở trang bằng trình duyệt chạy ẩn, thu thập style và chụp ảnh →
`output/<domain>/raw.json` và `output/<domain>/screenshots/`.

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--out`, `-o <thư-mục>` | `output/` | Thư mục gốc chứa kết quả. |
| `--dark` | tắt | Thu thêm bản dark mode (`prefers-color-scheme: dark`). |
| `--pages <0–10>` | `0` | Số trang con quan trọng (lấy từ navigation) thu thập thêm. |
| `--max-elements <n>` | `3000` | Số phần tử tối đa lấy computed style trên mỗi trang. |
| `--browser auto\|chromium\|msedge\|chrome` | `GDM_BROWSER`, nếu không có thì `auto` | Trình duyệt dùng để mở trang (xem [Trình duyệt](#trình-duyệt)). |

URL thiếu scheme được hiểu là `https://` (`gdm collect stripe.com`).

### `gdm analyze <thư-mục>` — Tầng 2: phân tích

Đọc `raw.json`, phân tích thuần code → `analysis.json` và in bảng tóm tắt trên terminal.

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--delta-e <0.5–20>` | `3` | Ngưỡng gom cụm màu CIEDE2000; nhỏ hơn thì giữ nhiều sắc độ hơn. |

### `gdm synthesize <thư-mục>` — Tầng 3: dựng DESIGN.md

Đọc `analysis.json`, dựng `DESIGN.md` bằng luật suy diễn và template, tự kiểm (tham chiếu token,
thứ tự section, tương phản) rồi ghi `DESIGN.md` và `synthesis.json`. Nếu bước tự kiểm phát hiện
lỗi, **file không được ghi** và lỗi được in ra.

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--lang en\|vi` | `en` | Ngôn ngữ phần mô tả. Tên section và tên token luôn giữ tiếng Anh theo spec. |
| `--prompt-only` | tắt | Ghi thêm `prompt.md` và ảnh đã thu nhỏ để tự dán vào Claude Code / claude.ai. |
| `--examples` | tắt | Thêm các surface minh họa `ex-*` vào DESIGN.md (chỉ dùng token có thật, đã kiểm tương phản). |

### `gdm export <thư-mục>` — Xuất tokens.json và preview.html

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--open` | tắt | Mở `preview.html` (hoặc `DESIGN.md` nếu chưa có preview) trong trình duyệt. |

### `gdm get <url>` — Trọn gói

Chạy `collect → analyze → synthesize → export`, cuối cùng in bảng các file đã tạo.

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--out`, `-o <thư-mục>` | `output/` | Thư mục gốc chứa kết quả. |
| `--dark` | tắt | Thu thêm bản dark mode. |
| `--open` | tắt | Mở `preview.html` (hoặc `DESIGN.md`) khi xong. |
| `--pages <0–10>` | `0` | Số trang con quan trọng phân tích thêm. |
| `--lang en\|vi` | `en` | Ngôn ngữ phần mô tả trong DESIGN.md. |
| `--prompt-only` | tắt | Ghi thêm `prompt.md` + ảnh cho Claude Code / claude.ai. |
| `--examples` | tắt | Thêm surface minh họa `ex-*`. |
| `--max-elements <n>` | `3000` | Số phần tử tối đa lấy style trên mỗi trang. |
| `--browser auto\|chromium\|msedge\|chrome` | `GDM_BROWSER`, nếu không có thì `auto` | Trình duyệt dùng để mở trang. |

### `gdm ui` — Giao diện đồ họa

Mở giao diện cục bộ để nhập URL, chỉnh tùy chọn và xem kết quả: `gdm ui` (tab trình duyệt),
`gdm ui --window` (cửa sổ riêng), `gdm ui --no-open --port 8765` (chỉ in địa chỉ). Bảng tùy chọn
đầy đủ ở mục [Giao diện đồ họa](#giao-diện-đồ-họa).

### Chạy lại từng tầng

Vì mỗi tầng chỉ đọc file JSON của tầng trước, bạn có thể thử nhiều cấu hình mà không phải thu
thập lại:

```bash
gdm collect https://stripe.com --dark
gdm analyze output/stripe.com --delta-e 2      # gom màu chặt hơn
gdm synthesize output/stripe.com --lang vi
gdm export output/stripe.com --open
```

## Thư mục kết quả

```
output/<domain>/                 # ví dụ output/stripe.com, output/127.0.0.1_8000
├── raw.json                     # Tầng 1: mọi trang đã thu thập (+ bản dark nếu có)
├── screenshots/                 # home-1440.png, home-768.png, home-390.png, home-dark-1440.png…
├── analysis.json                # Tầng 2: token màu, cấp chữ, scale, breakpoint, component…
├── synthesis.json               # Tầng 3: chế độ, ngôn ngữ, kết quả tự kiểm, danh sách section
├── DESIGN.md                    # tài liệu chính cho AI agent
├── prompt.md                    # chỉ khi dùng --prompt-only
├── prompt-images/               # ảnh desktop/mobile đã thu nhỏ cho prompt.md (--prompt-only)
├── tokens.json                  # W3C Design Tokens
└── preview.html                 # trang xem trước tự chứa
```

Tên thư mục là tên miền viết thường, bỏ `www.`; cổng (nếu có) được nối bằng `_`. Thư mục
`output/` đã nằm trong `.gitignore`.

## Định dạng DESIGN.md

File theo spec DESIGN.md (`version: alpha`) của Google Labs, gồm hai phần.

**1. Front matter YAML** giữa hai dòng `---` ở đầu file, chứa token máy đọc được:

```yaml
---
version: alpha
name: Acme Cloud Analysis
description: Bright white canvas, one indigo action color, Space Grotesk display type over Inter.
colors:
  primary: "#4f46e5"
  on-primary: "#ffffff"
  canvas: "#ffffff"
  ink: "#0f172a"
typography:
  display: { fontFamily: Space Grotesk, fontSize: 64px, fontWeight: 700, lineHeight: 1.05 }
  body: { fontFamily: Inter, fontSize: 16px, fontWeight: 400, lineHeight: 1.6 }
  ui: { fontFamily: Inter, fontSize: 15px, fontWeight: 500, lineHeight: 1.33 }
rounded: { sm: 6px, md: 8px, full: 9999px }
spacing: { xs: 8px, md: 16px, xl: 24px }
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 12px 20px
  button-primary-hover:
    backgroundColor: "#4338ca"
---
```

- Nhóm token: `colors`, `typography`, `rounded`, `spacing`, `components`. Màu viết dạng
  `#rrggbb` (hoặc `#rrggbbaa` khi có độ trong suốt).
- Tham chiếu token viết `{nhóm.tên}`, ví dụ `{colors.primary}`, `{rounded.md}`,
  `{typography.ui}`. Mọi tham chiếu đều phải trỏ tới token có thật.
- Trạng thái hover là một component riêng có hậu tố `-hover` (`button-primary-hover`).
- Bảng màu gọn (khoảng 8–14 token) đặt tên theo vai trò; toàn bộ cụm màu đo được chỉ nằm trong
  `analysis.json`.

**2. Phần thân markdown**: tiêu đề `# <Tên site> — Design Reference`, một đoạn ghi chú nêu URL
nguồn, ngày trích xuất, các trang đã phân tích và lời nhắc đây là tài liệu tham khảo, rồi các
section `##` theo đúng thứ tự (được phép thiếu, không được trùng):

| # | Section | Nội dung |
|---|---|---|
| 1 | `## Overview` | Phong cách tổng thể và các đặc điểm chính |
| 2 | `## Colors` | Vai trò từng màu, bảng tương phản WCAG |
| 3 | `## Typography` | Font, các cấp chữ, font thay thế miễn phí |
| 4 | `## Layout` | Container, spacing, breakpoint đo từ `@media`, `### Responsive Strategy` |
| 5 | `## Elevation & Depth` | Các mức đổ bóng / cách tạo chiều sâu |
| 6 | `## Shapes` | Bo góc |
| 7 | `## Components` | Component, biến thể và trạng thái hover |
| 8 | `## Do's and Don'ts` | Hướng dẫn và các cặp màu dưới chuẩn AA kèm tỉ lệ đo được |
| 9 | `## Agent Prompt Guide` | Hướng dẫn ngắn cho AI agent (luôn là section cuối) |

Phần thân nhắc token kèm giá trị, ví dụ "`{colors.canvas}` — #ffffff", để agent hiểu cả tên lẫn
giá trị. Spec đầy đủ: `npx --yes --package @google/design.md@0.4.0 designmd spec`.

## Dành cho lập trình viên

### Đưa DESIGN.md cho AI coding agent

`DESIGN.md` là tài liệu cho agent đọc, không phải code: chép nó vào dự án của bạn rồi yêu cầu
agent đọc trước khi dựng giao diện. Tab **Dùng với AI** trong giao diện có cùng hướng dẫn này kèm
nút sao chép.

| Công cụ | Cách đưa DESIGN.md |
|---|---|
| **Claude Code** | Đặt `DESIGN.md` ở thư mục gốc dự án và thêm vào `CLAUDE.md` một dòng như *"Mọi giao diện phải theo DESIGN.md: chỉ dùng token màu, chữ, bo góc, spacing trong đó."* |
| **Cursor** | Thêm `DESIGN.md` vào dự án, nhắc tới bằng `@DESIGN.md` trong khung chat, hoặc tạo một rule trong `.cursor/rules/` yêu cầu làm theo file này. |
| **Agent khác** (Codex, Gemini CLI, GitHub Copilot…) | Ghi một dòng *"Follow DESIGN.md for every UI change."* vào file hướng dẫn mà agent đọc (`AGENTS.md`, `GEMINI.md`…). |
| **v0, Lovable, bolt.new** | Dán nội dung `DESIGN.md` vào đầu prompt hoặc vào phần hướng dẫn/kiến thức của dự án, rồi mô tả trang cần dựng. |
| **ChatGPT, claude.ai** | Đính kèm `DESIGN.md` (thêm ảnh trong `screenshots/` nếu muốn AI thấy bố cục). |

Prompt mẫu (DESIGN.md mặc định viết tiếng Anh nên prompt tiếng Anh thường cho kết quả sát nhất):

```text
Read DESIGN.md first. Build a responsive pricing section with three plans.
Use only the tokens in its front matter (colors, typography, rounded, spacing, components);
do not invent new colors or font sizes. Follow its Do's and Don'ts and its contrast notes.
```

- `tokens.json` (W3C Design Tokens) nạp được vào Style Dictionary hoặc plugin Tokens Studio cho
  Figma; `preview.html` giúp xem nhanh bảng màu và component trước khi đưa cho AI.
- Muốn văn phong DESIGN.md mượt hơn thì dùng `--prompt-only` (mục bên dưới).
- Hãy dùng kết quả để học phong cách và dựng thương hiệu của riêng bạn, không sao chép logo hay
  nhận diện của trang gốc.

### Chạy giao diện từ mã nguồn

```bash
uv run gdm ui                              # giao diện trong tab trình duyệt
uv run --extra gui gdm ui --window         # trong cửa sổ riêng (pywebview)
uv run gdm ui --no-open --port 8765        # chỉ in địa chỉ để tự mở
uv run --extra gui gdm-ui                  # giống bản exe: cửa sổ riêng, nếu không được thì mở tab
uv run gdm-ui --self-test                  # bài tự kiểm tra của bản exe, báo cáo ở thư mục nhật ký
```

`gdm-ui` là điểm vào của bản exe (`get_design_markdown.ui.app:main`) và nhận đúng các tùy chọn ở
[Tùy chọn dòng lệnh của exe](#tùy-chọn-dòng-lệnh-của-exe). Giao diện web nằm trong
`src/get_design_markdown/ui/static/` (HTML/CSS/JS thuần, không có bước build, không dùng CDN); hợp
đồng API HTTP giữa trang và máy chủ được mô tả trong docstring của
`src/get_design_markdown/ui/server.py`.

### Đóng gói bản exe

Chỉ chạy trên Windows, từ thư mục mã nguồn:

```bash
uv run --group build --extra gui python scripts/build_exe.py --zip
```

Lệnh dùng PyInstaller với `packaging/gdm-ui.spec` (dạng thư mục, không có cửa sổ console, không
gói kèm Chromium) để tạo `dist\Get-Design-Markdown\Get-Design-Markdown.exe`, chạy bài tự kiểm tra
của exe vừa build (lỗi thì dừng) và nén thành
`dist\Get-Design-Markdown-<phiên-bản>-windows-x64.zip`.
Kiểm tra lại giống job `exe` của CI (trong PowerShell):

```powershell
$p = Start-Process -FilePath dist\Get-Design-Markdown\Get-Design-Markdown.exe -ArgumentList '--self-test','--self-test-report','self-test.json' -Wait -PassThru
$p.ExitCode
Get-Content self-test.json
```

Exe là ứng dụng cửa sổ: thiếu `-Wait` thì PowerShell trả về ngay và không có mã thoát thật.

## Dùng với Claude Code / claude.ai: `--prompt-only`

Công cụ **không gọi bất kỳ dịch vụ AI nào** và không cần API key: DESIGN.md luôn được dựng bằng
luật từ số liệu đo được. Nếu muốn phần mô tả mượt mà hơn, bạn có thể nhờ Claude viết lại văn phong
bằng gói Claude bạn đang dùng:

```bash
gdm get https://stripe.com --prompt-only
```

Lệnh ghi thêm `prompt.md` (chỉ dẫn "chỉ viết lại văn phong, không đổi con số, tên token hay tham
chiếu" + bản DESIGN.md dựng bằng luật + số liệu rút gọn) và thư mục `prompt-images/` chứa ảnh
desktop và mobile đã thu nhỏ (cạnh dài tối đa 1568px).

- **Claude Code**: mở thư mục dự án và yêu cầu
  *"Làm theo output/stripe.com/prompt.md, xem hai ảnh trong prompt-images/ và ghi đè DESIGN.md."*
- **claude.ai**: dán nội dung `prompt.md` vào cuộc trò chuyện và đính kèm hai ảnh trong
  `prompt-images/`, rồi lưu kết quả thành `DESIGN.md`.

Sau khi AI viết lại, nên chạy `scripts/verify_output.py` (xem bên dưới). Bộ kiểm tra đọc cả phần
thân chứ không chỉ front matter: giá trị viết cạnh tham chiếu (`` `{colors.canvas}` — #ffffff ``,
`` `{rounded.md}` (8px) ``, dòng bảng bắt đầu bằng một token) phải đúng giá trị trong front matter,
và mọi màu (hex 3, 4, 6 hoặc 8 chữ số, `rgb()`, `hsl()`, `oklch()`…), độ dài `px` hay `rem` (kể
cả viết tách như "16 px") và tỉ lệ tương phản `N:1` trong văn bản phải có trong `analysis.json`.
Văn bản trong ngoặc kép cong “…” chỉ được bỏ qua khi đúng là chữ trên trang đã thu (`raw.json`).
Nó **không** phát hiện được các trường hợp sau, nên vẫn cần đọc lại bản viết lại:

- một con số trong câu văn tự do bị đổi thành một số đo **khác cũng có thật** (ví dụ 16px thành
  24px khi cả hai đều đo được);
- số không có đơn vị (font weight như "300, 400, 700", line-height như 1.5), giá trị `em` hay `%`,
  và tên màu (`white`, `navy`…).

## tokens.json và preview.html

- **`tokens.json`** theo định dạng [W3C Design Tokens (DTCG)](https://www.designtokens.org/):
  nhóm màu, typography, kích thước (spacing, bo góc), đổ bóng, breakpoint, alias cho component
  (`{color.primary}`) và nhóm `dark` khi có dữ liệu dark mode. Mỗi token có `$type` và `$value`, có
  thể nạp vào Style Dictionary, Tokens Studio hay các công cụ hỗ trợ DTCG.
- **`preview.html`** là trang tĩnh tự chứa (CSS/JS viết inline; tài nguyên ngoài duy nhất là một
  stylesheet Google Fonts tùy chọn cho font của trang, khi offline trang dùng font dự phòng): ô
  màu kèm mã hex, vai trò và độ tương phản; mẫu từng cấp chữ; button, input, card, badge,
  navigation dựng lại từ thông số (kể cả hover); thang spacing, bo góc, đổ bóng; nút chuyển
  light/dark nếu có dữ liệu dark mode. Mở bằng `gdm export <thư-mục> --open` hoặc `gdm get <url> --open`.

## Biến môi trường

Đặt trong môi trường hoặc trong file `.env` ở thư mục chạy lệnh (sao chép từ `.env.example`).
Giá trị đã có trong môi trường được ưu tiên hơn `.env`.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `GDM_OUTPUT_DIR` | `output` | Thư mục gốc chứa kết quả `output/<domain>/` (cờ `--out` ghi đè). |
| `GDM_MAX_ELEMENTS` | `3000` | Số phần tử tối đa lấy computed style trên mỗi trang (cờ `--max-elements` ghi đè). |
| `GDM_TIMEOUT` | `45` | Thời gian chờ tải trang, tính bằng giây. |
| `GDM_BROWSER` | `auto` | Trình duyệt mở trang: `auto`, `chromium`, `msedge` hoặc `chrome` (cờ `--browser` ghi đè; xem [Trình duyệt](#trình-duyệt)). |
| `GDM_UI_CONFIG_DIR` | theo hệ điều hành | Thư mục chứa `ui-settings.json` của giao diện (xem [Nơi lưu dữ liệu](#nơi-lưu-dữ-liệu)). |
| `GDM_UI_LOG_DIR` | theo hệ điều hành | Thư mục chứa `ui.log` và `self-test.json`. |

Giá trị không hợp lệ (không phải số, nhỏ hơn hoặc bằng 0, trình duyệt không có trong danh sách)
sẽ bị báo lỗi kèm ví dụ cách sửa. Giao diện đồ họa lấy `GDM_MAX_ELEMENTS`, `GDM_TIMEOUT`,
`GDM_BROWSER` (và `GDM_OUTPUT_DIR` với `gdm ui`) làm giá trị mặc định; lựa chọn bạn đã lưu trong
giao diện được ưu tiên hơn.

## Kiểm tra kết quả

Dự án có một bộ kiểm tra **độc lập** với Tầng 3 và phần xuất, dùng để soát lại một thư mục kết
quả bất kỳ:

```bash
uv run python scripts/verify_output.py output/stripe.com            # bảng kết quả tiếng Việt
uv run python scripts/verify_output.py output/stripe.com --json     # JSON cho máy đọc
uv run python scripts/verify_output.py output/stripe.com --strict   # coi cảnh báo là lỗi
uv run python scripts/verify_output.py output/stripe.com --partial  # chấp nhận thư mục chưa chạy hết các tầng
```

Bộ kiểm tra xác nhận:

- `raw.json`, `analysis.json`, `synthesis.json` đúng schema; ảnh chụp tồn tại và đúng kích thước;
  tỉ lệ tương phản trong `analysis.json` tính lại khớp; tham chiếu token của component có thật;
  breakpoint đều đo được từ `@media`.
- `DESIGN.md`: front matter hợp lệ (`version: alpha`, có `name`), mọi `{nhóm.tên}` trỏ tới token có
  thật, thứ tự section đúng, có ghi chú nguồn; **không có con số bịa**: mọi màu, bo góc, spacing,
  cỡ chữ của front matter đều có trong `analysis.json`; trong phần thân (ngoài khối code), giá trị
  viết cạnh tham chiếu token và các dòng bảng bắt đầu bằng token khớp front matter, còn mọi màu
  (hex 3/4/6/8 chữ số hoặc hàm màu CSS như `rgb()`, `hsl()`), độ dài `px` hoặc `rem` (có hay không
  có khoảng trắng trước đơn vị) và tỉ lệ `N:1` đều có trong `analysis.json` (hoặc là mốc chuẩn như
  4.5:1, 44px, hoặc suy ra trực tiếp từ số đo: màu đặc mà một màu trong suốt đo được hiện ra trên
  nền canvas, tỉ lệ mà công cụ bỏ qua alpha báo cho màu đó). Chỉ URL, tên/selector lấy từ trang và
  câu trích trong ngoặc kép cong “…” khớp chữ trên trang (`raw.json`) được bỏ qua; số không đơn vị
  (font weight, line-height), `em`, `%` và tên màu thì không được kiểm. Cặp màu component dưới
  4.5:1 được nhắc trong Do's and Don'ts; breakpoint đo được có trong mục Layout; trạng thái hover
  có biến thể `-hover`.
- Mọi tầng đều đã chạy đủ: `analysis.json` không có phần phân tích bị bỏ qua và `synthesis.json`
  không ghi bước nào "chưa được xây dựng" (với `--partial` đây chỉ là ghi chú).
- `tokens.json` (mọi token có `$type`, alias trỏ đúng, giá trị màu/kích thước hợp lệ) và
  `preview.html` (đủ mã màu của token, không nạp script ngoài).

Mã thoát: `0` khi không có lỗi, `1` khi có lỗi (hoặc có cảnh báo với `--strict`).

Có thể đối chiếu thêm với linter chính thức của spec (cần Node.js và quyền tải gói từ npm):

```bash
npx --yes --package @google/design.md@0.4.0 designmd lint output/stripe.com/DESIGN.md
```

Linter in kết quả dạng JSON và trả mã thoát `1` khi có lỗi. Trên Windows hãy dùng đúng tên
lệnh `designmd` như trên: lệnh còn lại của gói tên là `design.md`, Windows sẽ coi đó là một
file Markdown nên không chạy linter. Dự án ghim phiên bản `0.4.0` (spec còn ở bản alpha, bản mới
có thể đổi luật); CI dùng cùng phiên bản đó.

## Xử lý lỗi thường gặp

Lỗi được in bằng tiếng Việt kèm gợi ý; thêm `--debug` (`gdm --debug get …`) để xem traceback.

| Tình huống | Cách xử lý |
|---|---|
| **Hết thời gian chờ** khi tải trang | Trang nặng hoặc mạng chậm: tăng `GDM_TIMEOUT` (ví dụ `GDM_TIMEOUT=90`). Trang không bao giờ "idle" (polling, analytics) vẫn được thu thập sau khi hết thời gian chờ networkidle. |
| **Trang chặn bot** (Cloudflare, captcha, HTTP 403/429) | Công cụ dừng và báo rõ. Thử lại sau, thử trang con khác, hoặc chọn trang không có cơ chế chống bot. Công cụ không tìm cách vượt qua cơ chế này. |
| **Trang yêu cầu đăng nhập** | Chỉ phân tích được trang công khai; hãy chọn landing page, trang pricing hay docs công khai. |
| **Không tìm thấy trình duyệt** để mở trang (Chromium, Edge hoặc Chrome) | Cài Microsoft Edge hoặc Google Chrome, hoặc bấm **Tải Chromium** trong giao diện, hoặc chạy `uv run playwright install chromium` (Linux: thêm `--with-deps`). Nếu đã chọn hẳn một trình duyệt (`--browser msedge`…), kiểm tra trình duyệt đó đã được cài hoặc chọn `auto`. |
| Windows báo **"Windows protected your PC"** khi mở exe | Bấm **More info** → **Run anyway** (**Thông tin thêm** → **Vẫn chạy**); xem [Tải bản exe cho Windows](#tải-bản-exe-cho-windows). |
| Exe mở **tab trình duyệt** thay vì cửa sổ riêng | Máy thiếu Microsoft Edge WebView2: cài "WebView2 Runtime" từ trang của Microsoft, hoặc cứ dùng giao diện trong tab. |
| Giao diện báo **"Phiên làm việc đã hết hạn"** | Máy chủ giao diện đã dừng hoặc đã khởi động lại: chạy lại `gdm ui` hoặc mở lại exe. |
| **Lỗi không mong muốn** trong giao diện | Thử lại; nếu lặp lại, gửi kèm `ui.log` (bản exe) hoặc kết quả `gdm --debug get <url>` khi báo lỗi. |
| Cảnh báo **robots.txt không cho phép** | Xem mục [Đạo đức và robots.txt](#đạo-đức-và-robotstxt). |
| **Không tìm thấy thư mục dữ liệu** | `analyze`/`synthesize`/`export` cần thư mục đã có `raw.json` / `analysis.json`; chạy các tầng trước, hoặc kiểm tra `GDM_OUTPUT_DIR` khi truyền tên miền. |
| **File không đúng định dạng** (`raw.json`, `analysis.json`) | File do phiên bản cũ tạo ra: chạy lại bước tạo ra file đó. |
| **DESIGN.md không qua bước tự kiểm** | Không có file nào bị ghi; nếu `analysis.json` đã cũ, chạy lại `gdm analyze`. |
| Chữ tiếng Việt bị lỗi trên terminal Windows | Dùng Windows Terminal hoặc đặt `PYTHONUTF8=1`. |

## Đạo đức và robots.txt

- Trước khi mở trang, công cụ đọc `robots.txt` (theo RFC 9309) với tên bot `get-design-markdown`.
  Nếu trang không cho phép, công cụ **in cảnh báo** và vẫn tiếp tục vì đây là một lần truy cập phục
  vụ tham khảo, giống một người dùng mở trang. Hãy tôn trọng điều khoản sử dụng của trang và dừng
  lại nếu trang không muốn bị phân tích. Trong giao diện đồ họa, kết quả của trang đó luôn kèm một
  ghi chú nhắc lại điều này, kể cả khi mở lại từ lịch sử. Khi không tải được `robots.txt` (mạng
  chậm, máy chủ không trả lời), công cụ chỉ ghi một dòng thông tin rồi tiếp tục.
- Công cụ chỉ tải các trang bạn chỉ định (và tối đa 10 trang con với `--pages`), không crawl hàng
  loạt và không vượt qua cơ chế chống bot hay trang đăng nhập.
- Kết quả chỉ chứa thông số style và ảnh chụp màn hình phục vụ phân tích, không chứa logo, hình ảnh
  hay file font của trang. Dùng DESIGN.md để học hỏi phong cách và dựng giao diện của riêng bạn, không
  dùng để sao chép thương hiệu, tài sản có bản quyền hay giả mạo trang gốc.

## Phát triển

### Chuẩn bị

```bash
uv sync
uv run playwright install chromium
```

### Lint, kiểu và test

```bash
uv run ruff check .                 # lint
uv run ruff format --check .        # định dạng (bỏ --check để tự sửa)
uv run mypy src tests               # kiểu (strict)
uv run mypy scripts
uv run pytest                       # toàn bộ test (xem ghi chú về mạng bên dưới)
```

Marker của pytest:

| Marker | Ý nghĩa | Chạy |
|---|---|---|
| `browser` | Cần Chromium, chạy trên trang fixture cục bộ `tests/fixtures/site/` | Có trong `uv run pytest`; tự bỏ qua nếu thiếu Chromium, trừ khi đặt `GDM_REQUIRE_BROWSER=1` (CI đặt sẵn) |
| `e2e` | Trọn pipeline trên trang fixture, chạy bằng `gdm get` và qua máy chủ giao diện (API HTTP và trang web thật) | `uv run pytest -m e2e`; thất bại khi một tầng chưa được xây dựng, bị bỏ qua hoặc không ghi file |
| `network` | Dành cho test cần Internet | Bị loại khỏi lần chạy mặc định (`addopts` trong `pyproject.toml`). Trang thật (stripe.com, linear.app) được kiểm bằng `gdm get` và `scripts/verify_output.py` chứ không bằng pytest |

Mạng: các test không cần Internet, trừ `tests/test_synthesizer_linter.py`. Test này cố ý không
mang marker `network`, để `uv run pytest` trên máy và mọi job test của CI đều chạy nó: nó chạy
linter chính thức qua `npx`, nên cần Node.js và lần đầu phải tải gói `@google/design.md` từ npm
(sau đó dùng bộ nhớ đệm của npm). Khi không có `npx` hoặc không tải được gói, test tự bỏ qua kèm
lý do thay vì thất bại.

Chạy trọn pipeline trên trang fixture và kiểm tra kết quả giống job e2e của CI:

```bash
uv run python scripts/run_e2e.py --out e2e-output
```

Lệnh thất bại nếu thiếu trang fixture hoặc có tầng lỗi hay chưa được xây dựng (`gdm` dừng ngay
ở tầng đó, không bỏ qua kèm cảnh báo). Khi đang dựng lại một tầng trên máy cá nhân, thêm `--allow-partial` để
chỉ ghi chú các tầng đó và kiểm phần đã có ở chế độ `--partial`; CI không bao giờ dùng cờ này.

### Kiến trúc mã nguồn

```
src/get_design_markdown/
├── cli.py, pipeline.py        # lệnh Typer và phần nối các tầng
├── config.py, errors.py       # cấu hình (.env) và lỗi có thông điệp tiếng Việt
├── console.py, storage.py     # Rich console; thư mục output, đọc/ghi JSON
├── models/                    # Pydantic: raw.py (Tầng 1), analysis.py (Tầng 2), synthesis.py (Tầng 3)
├── collector/                 # Playwright async: trình duyệt, DOM, stylesheet, hover, viewport, robots, discovery
├── analyzer/                  # colors, typography, fonts, spacing, shapes, breakpoints, layout, components, report
├── synthesizer/               # render, frontmatter, narrative, validate, prompt_export, phrases/en|vi.yaml
├── exporters/                 # tokens.py (DTCG), preview.py
├── templates/                 # design.md.j2, prompt.md.j2, preview.html.j2
└── ui/                        # giao diện: server.py (API HTTP), jobs.py, history.py, settings.py,
                               #   render_md.py, browser_install.py, app.py (gdm ui, gdm-ui, exe), static/
packaging/                     # gdm-ui.spec (PyInstaller) cho bản exe
scripts/                       # verify_output.py, run_e2e.py, build_exe.py
tests/                         # pytest; fixtures/ (raw_sample, analysis_sample, site_contract, site/)
```

Nguyên tắc: Collector dùng Playwright async; Analyzer và Synthesizer là hàm thuần (model vào, model
hoặc chuỗi ra, không I/O). Mọi phép đọc giá trị CSS dùng chung `analyzer/css_values.py`; tên token
chuẩn nằm ở `analyzer/naming.py`. Định danh và docstring viết tiếng Anh; thông báo cho người dùng
viết tiếng Việt có dấu.

### CI/CD

- **`.github/workflows/ci.yml`** chạy khi push lên `main`, khi mở pull request,
  khi chạy tay và khi được workflow khác gọi:
  - `lint`: ruff, ruff format, mypy (`src tests` và `scripts`);
  - `test`: pytest trên Ubuntu và Windows × Python 3.11, 3.12, 3.13, có Chromium
    (`GDM_REQUIRE_BROWSER=1`), lưu báo cáo JUnit;
  - `e2e`: `pytest -m e2e`, `scripts/run_e2e.py` (pipeline trên trang fixture + `verify_output.py`;
    thất bại khi có tầng chưa được xây dựng) và linter chính thức `@google/design.md@0.4.0` (phiên
    bản ghim trong biến `DESIGN_MD_LINTER`, nâng bằng tay) khi đã có DESIGN.md;
  - `build`: `uv build`, kiểm tra wheel có đủ mọi file tài nguyên của gói (`collector/js/*.js`,
    `synthesizer/phrases/*.yaml`, `templates/*.j2` và mọi file trong `ui/static/`, liệt kê từ git),
    `twine check`, cài thử wheel, chạy `gdm ui --help` / `gdm-ui --help` và đọc lại từng file tài
    nguyên (kể cả trang `ui/static/index.html`) từ bản cài;
  - `exe` (Windows): cài dependency kèm extra `gui` và nhóm `build`, build exe bằng
    `scripts/build_exe.py --zip` **không** tải Chromium (bài tự kiểm tra phải tự tìm thấy Edge như
    trên máy người dùng), chạy `Get-Design-Markdown.exe --self-test` qua
    `Start-Process -Wait -PassThru` (thất bại khi mã thoát khác 0) rồi lưu file zip và báo cáo
    `self-test.json` làm artifact.
- **`.github/workflows/release.yml`** (CD): khi push tag `v*`, chạy lại toàn bộ CI, kiểm tra tag
  khớp version trong `pyproject.toml` và `__version__`, build wheel và sdist, build lại bản exe trên
  Windows (kèm bài tự kiểm tra) rồi tạo GitHub Release đính kèm wheel, sdist và
  `Get-Design-Markdown-<phiên-bản>-windows-x64.zip`. Không publish lên PyPI.
- Các GitHub Action được ghim theo phiên bản và cập nhật thủ công.

Phát hành phiên bản mới:

```bash
# 1. Cập nhật version trong pyproject.toml và src/get_design_markdown/__init__.py, rồi:
uv lock
git commit -am "chore: release v0.2.0"
git tag v0.2.0
git push origin main v0.2.0
```

### Đóng góp

1. Fork, tạo nhánh mới từ `main`.
2. Viết code kèm test; chạy đủ các lệnh lint/kiểu/test ở trên.
3. Commit message tiếng Anh theo [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat(analyzer): …`, `fix(collector): …`, `docs: …`), không emoji.
4. Mở pull request và điền danh sách kiểm tra trong mẫu PR, gồm cả kết quả chạy thử thật và
   `scripts/verify_output.py`.

**Phần giao diện đóng băng và change request.** Các model Pydantic (`models/`), phần nối CLI /
pipeline, các module dùng chung (`css_values`, `common`, `naming`), `pyproject.toml`, `uv.lock` và
fixture dùng chung là hợp đồng giữa các tầng. Nếu thay đổi của bạn cần sửa những file này, đừng sửa
lẫn vào PR tính năng: hãy ghi một **change request** trong PR (file, thay đổi chính xác, lý do) để
người duy trì áp dụng riêng, sinh lại fixture nếu cần
(`uv run python -m tests.fixtures.build_samples`), rồi rebase nhánh của bạn.

Mọi dependency mới phải có giấy phép tương thích GPLv3 (MIT, BSD, Apache-2.0, LGPL…).

## Thay đổi

### 0.2.0

- **Giao diện đồ họa tiếng Việt**: lệnh `gdm ui` (mở trong trình duyệt) và bản exe cho Windows
  (cửa sổ riêng), xem [Giao diện đồ họa](#giao-diện-đồ-họa) và
  [Tải bản exe cho Windows](#tải-bản-exe-cho-windows).
- **Chọn trình duyệt**: `--browser auto|chromium|msedge|chrome` hoặc `GDM_BROWSER`. `auto` (mặc
  định) dùng Chromium của Playwright, nếu chưa có thì Microsoft Edge, rồi Google Chrome. `raw.json`
  ghi thêm trình duyệt đã dùng (trường `browser`).
- **Lời nhắn trên terminal dễ hiểu hơn**: bỏ hậu tố "(Tầng N)" ở các bước, viết rõ "trạng thái
  khi rê chuột (hover)", "Bản giao diện tối (dark mode)", "Tệp CSS (stylesheet)", "Kiểu chữ",
  "Khoảng cách", "Bố cục"… Script nào đọc chữ in ra của `gdm collect` / `gdm get` cần cập nhật;
  định dạng các tệp kết quả (`raw.json`, `analysis.json`, `DESIGN.md`, `tokens.json`,
  `preview.html`) không đổi.
- Không tải được `robots.txt` (mạng chậm, máy chủ không trả lời) giờ chỉ là một dòng thông tin,
  không còn là cảnh báo; trang thật sự không cho phép vẫn được cảnh báo như trước.
- URL có tên miền chứa khoảng trắng, ký hiệu lạ hoặc số cổng không hợp lệ bị từ chối ngay, kèm gợi ý.
- Mở trình duyệt có giới hạn thời gian: trình duyệt khởi động được nhưng không mở được trang nào
  trong 60 giây sẽ báo lỗi thay vì chờ mãi.

### 0.1.0

- Bản đầu tiên: dòng lệnh `gdm` (`collect`, `analyze`, `synthesize`, `export`, `get`), DESIGN.md
  theo định dạng Google Labs, `tokens.json` (W3C Design Tokens) và `preview.html`.

## Giấy phép

Phát hành theo [GNU General Public License v3.0 hoặc mới hơn](LICENSE) (`GPL-3.0-or-later`).
Bạn được tự do dùng, sửa và phân phối lại; bản phân phối có chỉnh sửa phải giữ cùng giấy phép và
công khai mã nguồn.

Bản exe cho Windows kèm `THIRD-PARTY-NOTICES.txt`: tên, giấy phép và thông báo bản quyền của mọi
thành phần mã nguồn mở được đóng gói (Python, OpenSSL, pywebview, pythonnet, Playwright, WebView2
SDK…).

"DESIGN.md" là định dạng do Google Labs công bố; các tên thương hiệu nhắc trong tài liệu này
(Stripe, Linear…) chỉ dùng làm ví dụ và thuộc về chủ sở hữu tương ứng.
