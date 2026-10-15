---
version: alpha
name: Acme Cloud Analysis
description: 'Ngôn ngữ thiết kế đo được của Acme Cloud: giao diện sáng trên nền trắng tinh, một màu nhấn chàm duy nhất (#4f46e5), tiêu đề Space Grotesk kết hợp chữ Inter và góc bo vừa phải (bán kính phổ biến nhất 6px).'
colors:
  primary: "#4f46e5"
  on-primary: "#ffffff"
  canvas: "#ffffff"
  surface: "#f8fafc"
  surface-inverse: "#0b1020"
  hairline: "#e2e8f0"
  ink: "#0f172a"
  muted: "#475569"
  faint: "#94a3b8"
  success: "#16a34a"
typography:
  display:
    fontFamily: Space Grotesk
    fontSize: 64px
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: -1.5px
  heading:
    fontFamily: Space Grotesk
    fontSize: 40px
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: -0.8px
  title:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: 600
    lineHeight: 1.33
  body-lg:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: 400
    lineHeight: 1.6
  body:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.6
  ui:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: 500
    lineHeight: 1.33
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.43
  code:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
  caption:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: 6px
  md: 8px
  lg: 12px
  xl: 16px
  full: 9999px
spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 20px
  xl: 24px
  xxl: 32px
  xxxl: 48px
  4xl: 64px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 12px 20px
    height: 44px
  button-primary-hover:
    backgroundColor: "#4338ca"
  button-secondary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.on-primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 12px 20px
    height: 44px
  button-secondary-hover:
    backgroundColor: "#1e293b"
  button-outline:
    textColor: "{colors.ink}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 11px 19px
    height: 44px
  button-outline-hover:
    backgroundColor: "{colors.surface}"
  button-ghost:
    textColor: "{colors.primary}"
    typography: "{typography.ui}"
    rounded: "{rounded.md}"
    padding: 8px 12px
    height: 36px
  button-ghost-hover:
    backgroundColor: "#eef2ff"
  text-link:
    textColor: "{colors.primary}"
  text-link-hover:
    textColor: "#4338ca"
  nav-link:
    textColor: "{colors.muted}"
    typography: "{typography.ui}"
    rounded: "{rounded.sm}"
    padding: 8px 12px
    height: 36px
  nav-link-hover:
    textColor: "{colors.ink}"
  text-input:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: 10px 14px
    height: 44px
  card:
    backgroundColor: "{colors.canvas}"
    rounded: "{rounded.xl}"
    padding: "{spacing.xxl}"
  badge:
    backgroundColor: "#eef2ff"
    textColor: "{colors.primary}"
    rounded: "{rounded.full}"
    padding: 4px 12px
    height: 28px
  top-nav:
    backgroundColor: "#ffffffcc"
    padding: 0px 24px
    height: 64px
  footer:
    backgroundColor: "{colors.surface-inverse}"
    textColor: "{colors.faint}"
    typography: "{typography.body-sm}"
    padding: 64px 0px 48px
---

# Acme Cloud — Design Reference

> **Nguồn:** <https://acme.example/> · **Ngày trích xuất:** 2026-10-15 · **Các trang đã phân tích:** home
>
> Được tạo bởi get-design-markdown 0.1.0 từ style đo được — dựng bằng luật, không dùng AI; mọi con số và mã màu đều lấy từ analysis.json.
>
> **Chỉ để tham khảo.** Tài liệu này được phân tích ngược từ website công khai của Acme Cloud nhằm mục đích học hỏi. Đây không phải design system chính thức của Acme Cloud và không chứa logo, hình ảnh hay tài sản có bản quyền nào.

## Overview

Acme Cloud có giao diện sáng, dựng trên nền trắng tinh (`{colors.canvas}` — #ffffff). Chữ chính có màu gần như đen ngả lạnh (`{colors.ink}` — #0f172a).

Các hành động dùng một màu nhấn duy nhất: chàm `{colors.primary}` — #4f46e5 trên button-primary. Kiểu chữ: tiêu đề Space Grotesk kết hợp chữ Inter. Độ đậm trải từ 400 đến 700. Ngôn ngữ hình khối dùng góc bo vừa phải (bán kính phổ biến nhất 6px). Độ nổi dựa vào đổ bóng nhẹ (số mức: 2).

### Đặc điểm chính

- Nền: trắng tinh `{colors.canvas}` — #ffffff; chữ `{colors.ink}` — #0f172a
- Hành động: một màu nhấn chàm duy nhất (#4f46e5)
- Typography: tiêu đề Space Grotesk kết hợp chữ Inter; JetBrains Mono cho code; độ đậm 400, 500, 600 và 700
- Hình khối: góc bo vừa phải (bán kính phổ biến nhất 6px)
- Chiều sâu: đổ bóng nhẹ (số mức: 2)
- Khoảng cách: đơn vị cơ sở 4px; container rộng tối đa 1200px
- Responsive: thanh điều hướng trên cùng thu gọn thành nút hamburger dưới 1024px
- Giao diện tối: đã thu thập (nền tối #0b1020)

## Colors

Token màu ngữ nghĩa: 10 (2 màu có sắc độ, 8 màu trung tính); bảng màu thuộc tông sáng. Các cụm màu đo được (20) nằm trong analysis.json — ở đây chỉ liệt kê token theo vai trò, kèm giá trị đo được.

### Thương hiệu & hành động

- **primary** `{colors.primary}` — #4f46e5: chàm — màu nhấn thương hiệu / hành động chính; bằng chứng đo được: background of button-primary; text of links; biến CSS `--color-primary`; được dùng bởi button-primary, button-ghost, text-link và badge.
- **on-primary** `{colors.on-primary}` — #ffffff: trắng — chữ và biểu tượng đặt trên primary; bằng chứng đo được: text on button-primary; được dùng bởi button-primary và button-secondary.

### Bề mặt

- **canvas** `{colors.canvas}` — #ffffff: trắng — nền trang (vùng nền lớn nhất); bằng chứng đo được: body background, largest area; được dùng bởi text-input và card.
- **surface** `{colors.surface}` — #f8fafc: gần như trắng — bề mặt / nền section; bằng chứng đo được: alternate section background; biến CSS `--color-surface`; được dùng bởi button-outline.
- **surface-inverse** `{colors.surface-inverse}` — #0b1020: gần như đen ngả lạnh — bề mặt / nền section; bằng chứng đo được: footer background; được dùng bởi footer.

### Chữ

- **ink** `{colors.ink}` — #0f172a: gần như đen ngả lạnh — màu chữ; bằng chứng đo được: headings and body text; biến CSS `--color-ink`; được dùng bởi button-secondary, button-outline, nav-link và text-input.
- **muted** `{colors.muted}` — #475569: xám đậm ngả lạnh — chữ phụ; bằng chứng đo được: secondary paragraphs and nav links; biến CSS `--color-muted`; được dùng bởi nav-link.
- **faint** `{colors.faint}` — #94a3b8: xám nhạt ngả lạnh — chữ phụ; bằng chứng đo được: captions and footer links; được dùng bởi text-input và footer.

### Viền

- **hairline** `{colors.hairline}` — #e2e8f0: xám nhạt ngả lạnh — viền / đường phân cách; 1.23:1 so với nền; bằng chứng đo được: 1px borders of cards, header, outline button; biến CSS `--color-border`; được dùng bởi button-outline, card và top-nav.

### Trạng thái

- **success** `{colors.success}` — #16a34a: xanh lá — trạng thái thành công; bằng chứng đo được: positive inline note; biến CSS `--color-success`.

### Độ tương phản

Độ tương phản WCAG 2.x của các cặp chữ/nền đo được (chữ cỡ thường cần 4.5:1 để đạt AA và 7:1 để đạt AAA):

| Chữ | Nền | Tỉ lệ | WCAG | Vị trí |
| --- | --- | --- | --- | --- |
| `{colors.muted}` — #475569 | `{colors.canvas}` — #ffffff | 7.58:1 | AA ✓ · AAA ✓ | secondary text |
| `{colors.ink}` — #0f172a | `{colors.canvas}` — #ffffff | 17.85:1 | AA ✓ · AAA ✓ | body text |
| `{colors.faint}` — #94a3b8 | `{colors.surface-inverse}` — #0b1020 | 7.38:1 | AA ✓ · AAA ✓ | footer links |
| `{colors.on-primary}` — #ffffff | `{colors.surface-inverse}` — #0b1020 | 18.93:1 | AA ✓ · AAA ✓ | footer headings |
| `{colors.primary}` — #4f46e5 | `{colors.canvas}` — #ffffff | 6.29:1 | AA ✓ · AAA ✗ | links |
| `{colors.on-primary}` — #ffffff | `{colors.primary}` — #4f46e5 | 6.29:1 | AA ✓ · AAA ✗ | button-primary |
| `{colors.ink}` — #0f172a | `{colors.surface}` — #f8fafc | 17.06:1 | AA ✓ · AAA ✓ | headings on surface |
| `{colors.success}` — #16a34a | `{colors.canvas}` — #ffffff | 3.30:1 | ✗ không đạt AA (chỉ dùng cho chữ lớn) | inline success note |
| `{colors.muted}` — #475569 | `{colors.surface}` — #f8fafc | 7.24:1 | AA ✓ · AAA ✓ | secondary text on surface |
| `{colors.primary}` — #4f46e5 | #eef2ff | 5.62:1 | AA ✓ · AAA ✗ | badge |
| #64748b | `{colors.surface-inverse}` — #0b1020 | 3.98:1 | ✗ không đạt AA (chỉ dùng cho chữ lớn) | footer copyright |
| `{colors.faint}` — #94a3b8 | `{colors.canvas}` — #ffffff | 2.56:1 | ✗ không đạt AA | caption |
| #e2e8f0 | `{colors.ink}` — #0f172a | 14.48:1 | AA ✓ · AAA ✓ | code sample |
| `{colors.on-primary}` — #ffffff | `{colors.ink}` — #0f172a | 17.85:1 | AA ✓ · AAA ✓ | button-secondary |

Số cặp dưới mức AA: 3. Các cặp này được nhắc lại trong Do's and Don'ts và không được dùng cho chữ cỡ thân bài.

### Màu trang trí

Các màu sau chỉ xuất hiện trong gradient hoặc phần tử trang trí. Chúng không phải token cấu trúc — đừng dùng cho chữ, nút bấm hay bề mặt:

- #6366f12e — chàm; trong suốt một phần (alpha 18%); gradient; thấy ở `section.hero`.
- #ec48991f — hồng; trong suốt một phần (alpha 12%); gradient; thấy ở `section.hero`.

### Giao diện tối

Đã thu thập bản giao diện tối (prefers-color-scheme: dark). Token trong front matter mô tả giao diện sáng; giá trị tối đo được cho cùng vai trò (số token: 10):

| Token | Tối | Front matter |
| --- | --- | --- |
| `primary` | #818cf8 | `{colors.primary}` — #4f46e5 |
| `on-primary` | #0b1020 | `{colors.on-primary}` — #ffffff |
| `canvas` | #0b1020 | `{colors.canvas}` — #ffffff |
| `surface` | #111827 | `{colors.surface}` — #f8fafc |
| `surface-inverse` | #05070f | `{colors.surface-inverse}` — #0b1020 |
| `hairline` | #1e293b | `{colors.hairline}` — #e2e8f0 |
| `ink` | #f8fafc | `{colors.ink}` — #0f172a |
| `muted` | #94a3b8 | `{colors.muted}` — #475569 |
| `faint` | #64748b | `{colors.faint}` — #94a3b8 |
| `success` | #4ade80 | `{colors.success}` — #16a34a |

- Giao diện tối: `faint` #64748b trên `surface-inverse` #05070f chỉ đạt 4.23:1 (footer copyright) — không đạt WCAG AA.

## Typography

Số họ font đo được: 3 — tiêu đề Space Grotesk kết hợp chữ Inter.

- **Inter** — họ font chính (thân bài và UI); sans-serif; stack `Inter, system-ui, -apple-system, Segoe UI, sans-serif`; độ đậm 400, 500 và 600; có trên Google Fonts.
- **Space Grotesk** — tiêu đề; sans-serif; stack `Space Grotesk, Inter, system-ui, sans-serif`; độ đậm 700; có trên Google Fonts.
- **JetBrains Mono** — code / monospace; monospace; stack `JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace`; độ đậm 400; có trên Google Fonts.

### Thang chữ

Các cấp chữ ngữ nghĩa, từ lớn đến nhỏ (số cấp: 9). Cột thẻ HTML cho biết mỗi cấp được đo ở đâu:

| Token | Font | Cỡ chữ | Độ đậm | Chiều cao dòng | Khoảng cách chữ | Thẻ HTML |
| --- | --- | --- | --- | --- | --- | --- |
| `{typography.display}` | Space Grotesk | 64px | 700 | 1.05 (67.2px) | -1.5px | h1 |
| `{typography.heading}` | Space Grotesk | 40px | 700 | 1.15 (46px) | -0.8px | h2, p |
| `{typography.title}` | Inter | 24px | 600 | 1.33 (32px) | 0 | h3 |
| `{typography.body-lg}` | Inter | 20px | 400 | 1.6 (32px) | 0 | p |
| `{typography.body}` | Inter | 16px | 400 | 1.6 (25.6px) | 0 | p, a |
| `{typography.ui}` | Inter | 15px | 500 | 1.33 (20px) | 0 | a, button |
| `{typography.body-sm}` | Inter | 14px | 400 | 1.43 (20px) | 0 | a, h4 |
| `{typography.code}` | JetBrains Mono | 14px | 400 | 1.6 (22.4px) | 0 | code |
| `{typography.caption}` | Inter | 13px | 400 | 1.5 (19.5px) | 0 | p, span |

- Cỡ chữ tăng theo tỉ lệ ≈1.25 (major third) từ cỡ cơ sở 16px.
- Độ đậm đang dùng: 400, 500, 600 và 700.
- Cỡ chữ lớn được siết bằng letter-spacing âm: `{typography.display}` (-1.5px) và `{typography.heading}` (-0.8px).
- Chữ thân bài có khoảng cách dòng rộng rãi: `{typography.body}` — Inter 16px / 400 / 1.6.
- Mẫu code dùng JetBrains Mono: `{typography.code}` — JetBrains Mono 14px / 400 / 1.6.

## Layout

Nội dung nằm trong container căn giữa, rộng tối đa 1200px, lề hai bên 24px ở bản desktop. Khung trang gồm thanh điều hướng trên cùng phía trên nội dung.

Khoảng cách theo đơn vị cơ sở 4px; bảng dưới liệt kê mọi bậc đo được.

| Token | Giá trị | Đo được ở |
| --- | --- | --- |
| `{spacing.xxs}` | 4px | badge |
| `{spacing.xs}` | 8px | nav-link, button-ghost |
| `{spacing.sm}` | 12px | button-primary, gap |
| `{spacing.md}` | 16px | p, code-sample |
| `{spacing.lg}` | 20px | button-primary |
| `{spacing.xl}` | 24px | container, grid gap |
| `{spacing.xxl}` | 32px | card, section gap |
| `{spacing.xxxl}` | 48px | section content gap |
| `{spacing.4xl}` | 64px | footer |

- Nhịp dọc: các section cách nhau khoảng 96px.
- Padding phổ biến nhất: `12px 20px`, `32px`, `8px 12px` và `0px 24px`.

### Chiến lược responsive

Các breakpoint theo chiều rộng đo từ quy tắc @media trong stylesheet đã thu thập (số lượng: 4):

| Tên | Khoảng | Số quy tắc @media |
| --- | --- | --- |
| sm | ≥ 640px | 14 |
| md | ≥ 768px | 40 |
| lg | ≥ 1024px | 44 |
| xl | ≥ 1280px | 9 |

Thay đổi bố cục đo được khi so sánh các bản chụp ở những chiều rộng viewport khác nhau:

- Thanh điều hướng trên cùng thu gọn thành nút hamburger dưới 1024px (thấy ở 768px).
- Lưới chính giảm từ 3 xuống 2 cột trong khoảng 1440px đến 768px.
- Tiêu đề lớn nhất thu nhỏ từ 64px xuống 48px trong khoảng 1440px đến 768px.
- Container 1200px chuyển sang co giãn (toàn chiều rộng) ở 768px.
- Lưới chính giảm từ 2 xuống 1 cột trong khoảng 768px đến 390px.
- Tiêu đề lớn nhất thu nhỏ từ 48px xuống 40px trong khoảng 768px đến 390px.
- Lề hai bên thu hẹp từ 24px xuống 20px ở 390px.

Ảnh chụp bố cục theo từng viewport:

| Viewport | Điều hướng | Số cột chính | Container | Lề | Tiêu đề lớn nhất | Vùng chạm nhỏ nhất |
| --- | --- | --- | --- | --- | --- | --- |
| 1440px | số liên kết: 5 | 3 | 1200px | 24px | 64px | 36px |
| 768px | nút hamburger | 2 | 768px | 24px | 48px | 40px |
| 390px | nút hamburger | 1 | 390px | 20px | 40px | 40px |

## Elevation & Depth

Độ nổi dùng đổ bóng nhẹ (số mức: 2; alpha đổ bóng mạnh nhất 0.12), từ nhẹ đến mạnh:

- **sm** — `0px 1px 2px 0px rgba(15, 23, 42, 0.06)`; số lớp: 1; dùng cho button-primary và input
- **md** — `0px 8px 24px -4px rgba(15, 23, 42, 0.12), 0px 2px 6px 0px rgba(15, 23, 42, 0.06)`; số lớp: 2; dùng cho card

Spec DESIGN.md không có nhóm token cho đổ bóng, nên các mức đổ bóng được ghi ở đây (và xuất ra tokens.json); hãy dùng đúng các giá trị này.

Các bậc bề mặt tính từ nền ra ngoài: `{colors.canvas}` — #ffffff → `{colors.surface}` — #f8fafc → `{colors.surface-inverse}` — #0b1020.

Viền dùng `{colors.hairline}` — #e2e8f0.

`top-nav` có vị trí sticky (bám khi cuộn); nền là #ffffffcc — trong suốt một phần (alpha 80%), kèm backdrop filter `blur(12px)`.

## Shapes

Góc bo vừa phải: bán kính phổ biến nhất là `{rounded.sm}` — 6px.

| Token | Giá trị | Đo được ở |
| --- | --- | --- |
| `{rounded.sm}` | 6px | nav-link, text-input |
| `{rounded.md}` | 8px | button-primary, button-secondary, button-outline, button-ghost |
| `{rounded.lg}` | 12px | card-icon, code-sample |
| `{rounded.xl}` | 16px | card |
| `{rounded.full}` | 9999px | badge |

Các component dùng nhiều bậc bán kính khác nhau (số bậc đang dùng: 4) — giữ mỗi component ở đúng bậc đo được thay vì đồng nhất mọi góc.

## Components

Các mẫu component phát hiện được (số lượng: 11). Mỗi tiêu đề bên dưới là một khóa trong nhóm `components` của front matter; trạng thái hover đo được là mục riêng `<name>-hover`.

### button-primary

Nút kêu gọi hành động chính. Số lần xuất hiện: 2, ví dụ “Start building free” và “Get started”.

- Nền: `{colors.primary}` — #4f46e5
- Chữ: `{colors.on-primary}` — #ffffff
- Typography: `{typography.ui}` — Inter 15px / 500 / 1.33
- Bo góc: `{rounded.md}` — 8px
- Padding: 12px 20px
- Chiều cao: 44px
- Đổ bóng: mức sm — `0px 1px 2px 0px rgba(15, 23, 42, 0.06)`
- Hover: nền → #4338ca
- Tương phản chữ: `{colors.on-primary}` — #ffffff trên `{colors.primary}` — #4f46e5: 6.29:1 (đạt WCAG AA ✓).
- Front matter: `button-primary` và `button-primary-hover`.

### button-secondary

Nút hành động phụ. Số lần xuất hiện: 1, ví dụ “Get updates”.

- Nền: `{colors.ink}` — #0f172a
- Chữ: `{colors.on-primary}` — #ffffff
- Typography: `{typography.ui}` — Inter 15px / 500 / 1.33
- Bo góc: `{rounded.md}` — 8px
- Padding: 12px 20px
- Chiều cao: 44px
- Hover: nền → #1e293b
- Tương phản chữ: `{colors.on-primary}` — #ffffff trên `{colors.ink}` — #0f172a: 17.85:1 (đạt WCAG AA ✓).
- Front matter: `button-secondary` và `button-secondary-hover`.

### button-outline

Nút viền cho hành động phụ. Số lần xuất hiện: 1, ví dụ “Book a demo”.

- Nền: trong suốt (để lộ bề mặt phía sau)
- Chữ: `{colors.ink}` — #0f172a
- Viền: 1px · `{colors.hairline}` — #e2e8f0
- Typography: `{typography.ui}` — Inter 15px / 500 / 1.33
- Bo góc: `{rounded.md}` — 8px
- Padding: 11px 19px
- Chiều cao: 44px
- Hover: nền → `{colors.surface}` — #f8fafc; màu viền → #cbd5e1
- Tương phản chữ: `{colors.ink}` — #0f172a trên `{colors.surface}` — #f8fafc: 17.06:1 (đạt WCAG AA ✓); `{colors.ink}` — #0f172a trên `{colors.canvas}` — #ffffff: 17.85:1 (đạt WCAG AA ✓).
- Front matter: `button-outline` và `button-outline-hover`.

### button-ghost

Nút ghost với nền trong suốt. Số lần xuất hiện: 1, ví dụ “Watch video”.

- Nền: trong suốt (để lộ bề mặt phía sau)
- Chữ: `{colors.primary}` — #4f46e5
- Typography: `{typography.ui}` — Inter 15px / 500 / 1.33
- Bo góc: `{rounded.md}` — 8px
- Padding: 8px 12px
- Chiều cao: 36px
- Hover: nền → #eef2ff
- Tương phản chữ: `{colors.primary}` — #4f46e5 trên `{colors.canvas}` — #ffffff: 6.29:1 (đạt WCAG AA ✓).
- Front matter: `button-ghost` và `button-ghost-hover`.

### text-link

Liên kết trong dòng chữ. Số lần xuất hiện: 1, ví dụ “See full pricing →”.

- Chữ: `{colors.primary}` — #4f46e5
- Typography: Inter 16px / 500 / 25.6px
- Trang trí chữ: none
- Hover: chữ → #4338ca; trang trí chữ → underline
- Tương phản chữ: `{colors.primary}` — #4f46e5 trên `{colors.canvas}` — #ffffff: 6.29:1 (đạt WCAG AA ✓).
- Front matter: `text-link` và `text-link-hover`.

### nav-link

Liên kết điều hướng. Số lần xuất hiện: 5, ví dụ “Product”, “Solutions” và “Pricing”.

- Chữ: `{colors.muted}` — #475569
- Typography: `{typography.ui}` — Inter 15px / 500 / 1.33
- Bo góc: `{rounded.sm}` — 6px
- Padding: 8px 12px
- Chiều cao: 36px
- Trang trí chữ: none
- Hover: chữ → `{colors.ink}` — #0f172a
- Tương phản chữ: `{colors.muted}` — #475569 trên `{colors.surface}` — #f8fafc: 7.24:1 (đạt WCAG AA ✓); `{colors.muted}` — #475569 trên `{colors.canvas}` — #ffffff: 7.58:1 (đạt WCAG AA ✓).
- Front matter: `nav-link` và `nav-link-hover`.

### text-input

Ô nhập liệu văn bản. Số lần xuất hiện: 1.

- Nền: `{colors.canvas}` — #ffffff
- Chữ: `{colors.ink}` — #0f172a
- Viền: 1px · #cbd5e1
- Typography: Inter 15px / 400 / 22px
- Bo góc: `{rounded.sm}` — 6px
- Padding: 10px 14px
- Chiều cao: 44px
- Đổ bóng: mức sm — `0px 1px 2px 0px rgba(15, 23, 42, 0.06)`
- Placeholder: `{colors.faint}` — #94a3b8 — tương phản chỉ 2.56:1, dưới 4.5:1
- Tương phản chữ: `{colors.ink}` — #0f172a trên `{colors.canvas}` — #ffffff: 17.85:1 (đạt WCAG AA ✓).
- Front matter: `text-input`.

### card

Thẻ nội dung. Số lần xuất hiện: 3.

- Nền: `{colors.canvas}` — #ffffff
- Viền: 1px · `{colors.hairline}` — #e2e8f0
- Bo góc: `{rounded.xl}` — 16px
- Padding: `{spacing.xxl}` — 32px
- Đổ bóng: mức md — `0px 8px 24px -4px rgba(15, 23, 42, 0.12), 0px 2px 6px 0px rgba(15, 23, 42, 0.06)`
- Front matter: `card`.

### badge

Nhãn (badge) dạng viên thuốc. Số lần xuất hiện: 1, ví dụ “New · Acme Workflows 2.0”.

- Nền: #eef2ff
- Chữ: `{colors.primary}` — #4f46e5
- Typography: Inter 13px / 600 / 20px
- Bo góc: `{rounded.full}` — 9999px
- Padding: 4px 12px
- Chiều cao: 28px
- Tương phản chữ: `{colors.primary}` — #4f46e5 trên #eef2ff: 5.62:1 (đạt WCAG AA ✓).
- Front matter: `badge`.

### top-nav

Thanh điều hướng trên cùng. Số lần xuất hiện: 1.

- Nền: #ffffffcc (≈ #ffffff khi phủ lên nền canvas)
- Viền: dưới 1px · `{colors.hairline}` — #e2e8f0
- Padding: 0px 24px
- Chiều cao: 64px
- Backdrop filter: `blur(12px)`
- Vị trí: sticky
- Front matter: `top-nav`.

### footer

Chân trang. Số lần xuất hiện: 1.

- Nền: `{colors.surface-inverse}` — #0b1020
- Chữ: `{colors.faint}` — #94a3b8
- Typography: `{typography.body-sm}` — Inter 14px / 400 / 1.43
- Padding: 64px 0px 48px
- Tương phản chữ: `{colors.faint}` — #94a3b8 trên `{colors.surface-inverse}` — #0b1020: 7.38:1 (đạt WCAG AA ✓).
- Front matter: `footer`.

## Do's and Don'ts

- Nên dành `{colors.primary}` — #4f46e5 cho nút kêu gọi hành động chính và các điểm nhấn (button-primary, button-ghost, text-link và badge).
- Nên đặt chữ nội dung bằng `{colors.ink}` — #0f172a trên `{colors.canvas}` — #ffffff (17.85:1).
- Nên đặt tiêu đề bằng `{typography.display}` — Space Grotesk 64px / 700 / 1.05 và chữ thân bài bằng `{typography.body}` — Inter 16px / 400 / 1.6.
- Nên giữ khoảng cách theo lưới 4px, ví dụ `{spacing.xs}` — 8px, `{spacing.sm}` — 12px, `{spacing.xl}` — 24px và `{spacing.xxl}` — 32px.
- Nên giữ mỗi component ở đúng bán kính đo được: `{rounded.md}` — 8px cho button-primary, button-secondary, button-outline và button-ghost; `{rounded.sm}` — 6px cho nav-link và text-input; `{rounded.xl}` — 16px cho card.
- Nên tái tạo đúng trạng thái hover đo được — button-primary: nền → #4338ca; button-secondary: nền → #1e293b.
- Nên giới hạn độ nổi trong các mức đổ bóng đo được: sm cho button-primary và input; md cho card.
- Nên thu gọn thanh điều hướng trên cùng thành menu hamburger dưới 1024px.
- Không nên đặt chữ `{colors.faint}` — #94a3b8 trên nền `{colors.canvas}` — #ffffff: cặp này chỉ đạt 2.56:1 (caption), dưới mức WCAG AA 4.5:1. Với chữ nhỏ, hãy dùng `{colors.muted}` — #475569 (7.58:1) thay thế.
- Không nên đặt chữ `{colors.success}` — #16a34a trên nền `{colors.canvas}` — #ffffff: cặp này chỉ đạt 3.30:1 (inline success note), dưới mức WCAG AA 4.5:1. Chỉ dùng cho biểu tượng, viền hoặc chữ lớn, hoặc chỉnh độ sáng đến khi đạt 4.5:1.
- Không nên dùng `faint` #64748b trên `surface-inverse` #05070f ở giao diện tối: cặp này chỉ đạt 4.23:1 (footer copyright), dưới mức WCAG AA 4.5:1. Chỉ chấp nhận được cho chữ lớn (≥ 24px, hoặc ≥ 18.66px khi in đậm).
- Không nên dùng lại các màu chữ lẻ đo được dưới mức WCAG AA 4.5:1 (chúng không phải token của bảng màu): #64748b trên `{colors.surface-inverse}` — #0b1020 (3.98:1, footer copyright).
- Không nên thêm màu, cỡ chữ, độ đậm hay bán kính bo góc không phải token trong file này.
- Không nên dùng độ đậm ngoài 400, 500, 600 và 700.
- Không nên thêm đổ bóng mạnh hơn mức md.
- Không nên thêm màu nhấn thứ hai cho hành động — hệ thống đo được chỉ có một màu nhấn.
- Không nên dùng các màu gradient trang trí (#6366f12e và #ec48991f) cho chữ, nút bấm hay bề mặt.

## Agent Prompt Guide

Bản tóm tắt dùng ngay cho AI coding agent. Mọi giá trị bên dưới đều là token định nghĩa trong front matter: hãy tham chiếu token, không chép giá trị thô.

### Tra cứu nhanh

- Hành động chính: nền `{colors.primary}` — #4f46e5, chữ `{colors.on-primary}` — #ffffff
- Màu trung tính: `{colors.canvas}` — #ffffff, `{colors.surface}` — #f8fafc, `{colors.ink}` — #0f172a, `{colors.muted}` — #475569 và `{colors.hairline}` — #e2e8f0
- Chữ: tiêu đề `{typography.display}` — Space Grotesk 64px / 700 / 1.05; thân bài `{typography.body}` — Inter 16px / 400 / 1.6
- Bo góc: `{rounded.md}` — 8px cho button-primary, button-secondary, button-outline và button-ghost; `{rounded.sm}` — 6px cho nav-link và text-input; `{rounded.xl}` — 16px cho card
- Khoảng cách: đơn vị cơ sở 4px
- Container: tối đa 1200px
- Breakpoint (min-width): 640, 768, 1024, 1280px

### Prompt mẫu

1. “Dựng section hero: nền `{colors.canvas}` — #ffffff; tiêu đề dùng `{typography.display}` — Space Grotesk 64px / 700 / 1.05, màu `{colors.ink}` — #0f172a; đoạn mô tả dùng `{typography.body-lg}` — Inter 20px / 400 / 1.6, màu `{colors.muted}` — #475569; nút kêu gọi hành động kiểu button-primary (nền `{colors.primary}` — #4f46e5, chữ `{colors.on-primary}` — #ffffff, typography `{typography.ui}` — Inter 15px / 500 / 1.33, bo góc `{rounded.md}` — 8px, padding 12px 20px, chiều cao 44px).”
2. “Tạo lưới tính năng 3 cột: mỗi mục là một card (nền `{colors.canvas}` — #ffffff, bo góc `{rounded.xl}` — 16px, padding `{spacing.xxl}` — 32px); tiêu đề thẻ dùng `{typography.title}` — Inter 24px / 600 / 1.33; mô tả dùng `{typography.body}` — Inter 16px / 400 / 1.6.”
3. “Thiết kế khối đăng ký: ô email theo kiểu text-input (nền `{colors.canvas}` — #ffffff, chữ `{colors.ink}` — #0f172a, bo góc `{rounded.sm}` — 6px, padding 10px 14px, chiều cao 44px); nút gửi theo kiểu button-primary (nền `{colors.primary}` — #4f46e5, chữ `{colors.on-primary}` — #ffffff, typography `{typography.ui}` — Inter 15px / 500 / 1.33, bo góc `{rounded.md}` — 8px, padding 12px 20px, chiều cao 44px).”

### Biến CSS

Biến CSS gợi ý, mỗi token trong front matter một biến:

| Token | Biến CSS | Giá trị |
| --- | --- | --- |
| `{colors.primary}` | `--color-primary` | #4f46e5 |
| `{colors.on-primary}` | `--color-on-primary` | #ffffff |
| `{colors.canvas}` | `--color-canvas` | #ffffff |
| `{colors.surface}` | `--color-surface` | #f8fafc |
| `{colors.surface-inverse}` | `--color-surface-inverse` | #0b1020 |
| `{colors.hairline}` | `--color-hairline` | #e2e8f0 |
| `{colors.ink}` | `--color-ink` | #0f172a |
| `{colors.muted}` | `--color-muted` | #475569 |
| `{colors.faint}` | `--color-faint` | #94a3b8 |
| `{colors.success}` | `--color-success` | #16a34a |
| `{typography.display}` | `--font-display` | `700 64px/1.05 "Space Grotesk", Inter, sans-serif` |
| `{typography.heading}` | `--font-heading` | `700 40px/1.15 "Space Grotesk", Inter, sans-serif` |
| `{typography.title}` | `--font-title` | `600 24px/1.33 Inter, -apple-system, "Segoe UI", sans-serif` |
| `{typography.body-lg}` | `--font-body-lg` | `400 20px/1.6 Inter, -apple-system, "Segoe UI", sans-serif` |
| `{typography.body}` | `--font-body` | `400 16px/1.6 Inter, -apple-system, "Segoe UI", sans-serif` |
| `{typography.ui}` | `--font-ui` | `500 15px/1.33 Inter, -apple-system, "Segoe UI", sans-serif` |
| `{typography.body-sm}` | `--font-body-sm` | `400 14px/1.43 Inter, -apple-system, "Segoe UI", sans-serif` |
| `{typography.code}` | `--font-code` | `400 14px/1.6 "JetBrains Mono", SFMono-Regular, Menlo, monospace` |
| `{typography.caption}` | `--font-caption` | `400 13px/1.5 Inter, -apple-system, "Segoe UI", sans-serif` |
| `{rounded.sm}` | `--radius-sm` | 6px |
| `{rounded.md}` | `--radius-md` | 8px |
| `{rounded.lg}` | `--radius-lg` | 12px |
| `{rounded.xl}` | `--radius-xl` | 16px |
| `{rounded.full}` | `--radius-full` | 9999px |
| `{spacing.xxs}` | `--space-xxs` | 4px |
| `{spacing.xs}` | `--space-xs` | 8px |
| `{spacing.sm}` | `--space-sm` | 12px |
| `{spacing.md}` | `--space-md` | 16px |
| `{spacing.lg}` | `--space-lg` | 20px |
| `{spacing.xl}` | `--space-xl` | 24px |
| `{spacing.xxl}` | `--space-xxl` | 32px |
| `{spacing.xxxl}` | `--space-xxxl` | 48px |
| `{spacing.4xl}` | `--space-4xl` | 64px |
