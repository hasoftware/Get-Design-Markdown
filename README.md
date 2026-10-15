# Get-Design-Markdown

Công cụ dòng lệnh nhận vào URL một trang web, phân tích ngôn ngữ thiết kế của trang (màu sắc,
typography, spacing, bo góc, đổ bóng, breakpoint, component…) rồi xuất ra **`DESIGN.md`** theo
[spec DESIGN.md của Google Labs](https://github.com/google-labs-code/design.md): một file markdown
mô tả design system để AI coding agent (Claude Code, Cursor, Gemini CLI…) đọc và dựng UI theo
phong cách tương tự.

```bash
gdm get https://stripe.com --open
```

> **Mục đích: tham khảo và học hỏi phong cách.** Công cụ chỉ đo thông số style (màu, cỡ chữ,
> khoảng cách…) và chụp ảnh màn hình để phân tích. Nó không tải về logo, hình ảnh hay file font
> của trang đích, và `DESIGN.md` luôn ghi rõ đây là tài liệu tham khảo, không phải design system
> chính thức của trang. Đừng dùng kết quả để sao chép thương hiệu hay tài sản có bản quyền.

## Mục lục

- [Tính năng](#tính-năng)
- [Cách hoạt động](#cách-hoạt-động)
- [Yêu cầu](#yêu-cầu)
- [Cài đặt](#cài-đặt)
- [Dùng nhanh](#dùng-nhanh)
- [Các lệnh](#các-lệnh)
- [Thư mục kết quả](#thư-mục-kết-quả)
- [Định dạng DESIGN.md](#định-dạng-designmd)
- [Dùng với Claude Code / claude.ai: `--prompt-only`](#dùng-với-claude-code--claudeai---prompt-only)
- [tokens.json và preview.html](#tokensjson-và-previewhtml)
- [Biến môi trường](#biến-môi-trường)
- [Kiểm tra kết quả](#kiểm-tra-kết-quả)
- [Xử lý lỗi thường gặp](#xử-lý-lỗi-thường-gặp)
- [Đạo đức và robots.txt](#đạo-đức-và-robotstxt)
- [Phát triển](#phát-triển)
- [Giấy phép](#giấy-phép)

## Tính năng

- **Thu thập bằng trình duyệt thật** (Playwright Chromium, viewport 1440×900): chờ trang tải xong,
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
| 1 — Collector | `gdm collect <url>` | Kiểm tra robots.txt, mở trang bằng Chromium, thu thập style, CSS, hover, ảnh chụp | `raw.json`, `screenshots/` |
| 2 — Analyzer | `gdm analyze <thư-mục>` | Gom cụm và phân loại màu, typography, spacing, bo góc, đổ bóng, breakpoint, layout, component | `analysis.json` + bảng tóm tắt |
| 3 — Synthesizer | `gdm synthesize <thư-mục>` | Dựng DESIGN.md bằng luật suy diễn + template, tự kiểm trước khi ghi | `DESIGN.md`, `synthesis.json` |
| Xuất | `gdm export <thư-mục>` | Design tokens và trang xem trước | `tokens.json`, `preview.html` |

`gdm get <url>` chạy cả bốn bước liền nhau. Dữ liệu giữa các tầng là các model Pydantic lưu
thành JSON, mỗi file có `schema_version`.

## Yêu cầu

- **Python ≥ 3.11** (CI kiểm thử 3.11, 3.12, 3.13 trên Linux và Windows).
- **[uv](https://docs.astral.sh/uv/)** để quản lý môi trường và dependency.
- **Chromium của Playwright** (tải một lần bằng lệnh `playwright install chromium`).
- Kết nối Internet tới trang cần phân tích.
- Tùy chọn: **Node.js ≥ 18** để chạy linter chính thức của spec DESIGN.md.

## Cài đặt

### Từ mã nguồn (khuyến nghị)

```bash
git clone https://github.com/hasoftware/Get-Design-Markdown.git
cd Get-Design-Markdown
uv sync                                  # tạo .venv và cài dependency theo uv.lock
uv run playwright install chromium       # tải Chromium cho Playwright (một lần)
uv run gdm --version
```

Trên Linux, nếu Chromium báo thiếu thư viện hệ thống, chạy
`uv run playwright install --with-deps chromium` (cần quyền sudo).

> **Ghi chú Windows:** nếu cài uv bằng `pip install --user uv` thì lệnh `uv` có thể không nằm
> trên PATH. Khi đó thay `uv` bằng `python -m uv` trong mọi lệnh, ví dụ
> `python -m uv sync`, `python -m uv run gdm get https://stripe.com`.

Mọi lệnh bên dưới viết ngắn là `gdm …`; nếu chạy trong thư mục mã nguồn thì dùng `uv run gdm …`
(hoặc kích hoạt `.venv`). Lệnh đầy đủ `get-design-markdown` và `python -m get_design_markdown`
tương đương với `gdm`.

### Từ bản phát hành

Mỗi bản phát hành trên [GitHub Releases](https://github.com/hasoftware/Get-Design-Markdown/releases)
có file wheel. Cài vào một môi trường ảo riêng:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate   ·   Windows: .venv\Scripts\activate
pip install get_design_markdown-<phiên-bản>-py3-none-any.whl
python -m playwright install chromium
gdm --version
```

## Dùng nhanh

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
*"Đọc output/stripe.com/DESIGN.md và dựng trang pricing theo đúng các token trong đó."*

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

Kiểm tra robots.txt, mở trang bằng Chromium, thu thập style và chụp ảnh → `output/<domain>/raw.json`
và `output/<domain>/screenshots/`.

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--out`, `-o <thư-mục>` | `output/` | Thư mục gốc chứa kết quả. |
| `--dark` | tắt | Thu thêm bản dark mode (`prefers-color-scheme: dark`). |
| `--pages <0–10>` | `0` | Số trang con quan trọng (lấy từ navigation) thu thập thêm. |
| `--max-elements <n>` | `3000` | Số phần tử tối đa lấy computed style trên mỗi trang. |

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
và mọi mã màu hex, độ dài `px`, tỉ lệ tương phản `N:1` trong văn bản phải có trong `analysis.json`.
Nó không phát hiện được trường hợp một con số trong câu văn tự do bị đổi thành một số đo **khác
cũng có thật** (ví dụ 16px thành 24px khi cả hai đều đo được), nên vẫn nên đọc lại bản viết lại.

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

Giá trị không hợp lệ (không phải số, nhỏ hơn hoặc bằng 0) sẽ bị báo lỗi kèm ví dụ cách sửa.

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
  viết cạnh tham chiếu token và các dòng bảng bắt đầu bằng token khớp front matter, còn mọi mã
  hex, độ dài `px` và tỉ lệ `N:1` đều có trong `analysis.json` (hoặc là mốc chuẩn như 4.5:1,
  44px); cặp màu component dưới 4.5:1 được nhắc trong Do's and Don'ts; breakpoint đo được có trong
  mục Layout; trạng thái hover có biến thể `-hover`.
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
| **Thiếu Chromium** (`Executable doesn't exist…`) | Chạy `uv run playwright install chromium` (Linux: thêm `--with-deps`). |
| Cảnh báo **robots.txt không cho phép** | Xem mục [Đạo đức và robots.txt](#đạo-đức-và-robotstxt). |
| **Không tìm thấy thư mục dữ liệu** | `analyze`/`synthesize`/`export` cần thư mục đã có `raw.json` / `analysis.json`; chạy các tầng trước, hoặc kiểm tra `GDM_OUTPUT_DIR` khi truyền tên miền. |
| **File không đúng định dạng** (`raw.json`, `analysis.json`) | File do phiên bản cũ tạo ra: chạy lại bước tạo ra file đó. |
| **DESIGN.md không qua bước tự kiểm** | Không có file nào bị ghi; nếu `analysis.json` đã cũ, chạy lại `gdm analyze`. |
| Chữ tiếng Việt bị lỗi trên terminal Windows | Dùng Windows Terminal hoặc đặt `PYTHONUTF8=1`. |

## Đạo đức và robots.txt

- Trước khi mở trang, công cụ đọc `robots.txt` (theo RFC 9309) với tên bot `get-design-markdown`.
  Nếu trang không cho phép, công cụ **in cảnh báo** và vẫn tiếp tục vì đây là một lần truy cập phục
  vụ tham khảo, giống một người dùng mở trang. Hãy tôn trọng điều khoản sử dụng của trang và dừng
  lại nếu trang không muốn bị phân tích.
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
| `e2e` | Trọn pipeline trên trang fixture | `uv run pytest -m e2e`; thất bại khi một tầng chưa được xây dựng, bị bỏ qua hoặc không ghi file |
| `network` | Cần Internet | Không chạy mặc định; `uv run pytest -m network` |

Mạng: các test không cần Internet, trừ `tests/test_synthesizer_linter.py`. Test này chạy linter
chính thức qua `npx`, nên cần Node.js và lần đầu phải tải gói `@google/design.md` từ npm (sau đó
dùng bộ nhớ đệm của npm). Khi không có `npx` hoặc không tải được gói, test tự bỏ qua kèm lý do
thay vì thất bại.

Chạy trọn pipeline trên trang fixture và kiểm tra kết quả giống job e2e của CI:

```bash
uv run python scripts/run_e2e.py --out e2e-output
```

Lệnh thất bại nếu thiếu trang fixture hoặc có tầng chưa được xây dựng (kể cả tầng mà `gdm get`
chỉ bỏ qua kèm cảnh báo). Khi đang dựng lại một tầng trên máy cá nhân, thêm `--allow-partial` để
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
└── templates/                 # design.md.j2, prompt.md.j2, preview.html.j2
scripts/                       # verify_output.py, run_e2e.py
tests/                         # pytest; fixtures/ (raw_sample, analysis_sample, site_contract, site/)
```

Nguyên tắc: Collector dùng Playwright async; Analyzer và Synthesizer là hàm thuần (model vào, model
hoặc chuỗi ra, không I/O). Mọi phép đọc giá trị CSS dùng chung `analyzer/css_values.py`; tên token
chuẩn nằm ở `analyzer/naming.py`. Định danh và docstring viết tiếng Anh; thông báo cho người dùng
viết tiếng Việt có dấu.

### CI/CD

- **`.github/workflows/ci.yml`** chạy khi push lên `main` hoặc `stream/**`, khi mở pull request,
  khi chạy tay và khi được workflow khác gọi:
  - `lint`: ruff, ruff format, mypy (`src tests` và `scripts`);
  - `test`: pytest trên Ubuntu và Windows × Python 3.11, 3.12, 3.13, có Chromium
    (`GDM_REQUIRE_BROWSER=1`), lưu báo cáo JUnit;
  - `e2e`: `pytest -m e2e`, `scripts/run_e2e.py` (pipeline trên trang fixture + `verify_output.py`;
    thất bại khi có tầng chưa được xây dựng) và linter chính thức `@google/design.md@0.4.0` (phiên
    bản ghim trong biến `DESIGN_MD_LINTER`, nâng bằng tay) khi đã có DESIGN.md;
  - `build`: `uv build`, kiểm tra wheel có đủ mọi file tài nguyên của gói (`collector/js/*.js`,
    `synthesizer/phrases/*.yaml`, `templates/*.j2`, liệt kê từ git), `twine check`, cài thử wheel
    và đọc lại từng file tài nguyên từ bản cài.
- **`.github/workflows/release.yml`** (CD): khi push tag `v*`, chạy lại toàn bộ CI, kiểm tra tag
  khớp version trong `pyproject.toml` và `__version__`, build rồi tạo GitHub Release kèm wheel và
  sdist. Không publish lên PyPI.
- Dependabot cập nhật các GitHub Action hằng tuần.

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

## Giấy phép

Phát hành theo [GNU General Public License v3.0 hoặc mới hơn](LICENSE) (`GPL-3.0-or-later`).
Bạn được tự do dùng, sửa và phân phối lại; bản phân phối có chỉnh sửa phải giữ cùng giấy phép và
công khai mã nguồn.

"DESIGN.md" là định dạng do Google Labs công bố; các tên thương hiệu nhắc trong tài liệu này
(Stripe, Linear…) chỉ dùng làm ví dụ và thuộc về chủ sở hữu tương ứng.
