Get Design Markdown {version} cho Windows
==========================================

Công cụ đọc một trang web và trích xuất ngôn ngữ thiết kế của nó (màu sắc, kiểu chữ, khoảng
cách, bo góc, đổ bóng, nút, thẻ, ô nhập...) thành DESIGN.md: tài liệu để AI coding agent
(Claude Code, Cursor, v0...) dựng giao diện theo phong cách tương tự.
Chỉ dùng để tham khảo và học hỏi phong cách; không sao chép logo hay tài sản có bản quyền.


Cách mở
-------
1. Giải nén cả thư mục ra máy (đừng chạy trực tiếp bên trong file .zip), vào một chỗ có
   đường dẫn ngắn, ví dụ Documents\Apps hoặc C:\Apps. Đường dẫn quá dài (khoảng 200 ký tự
   trở lên) khiến Windows không nạp được các thư viện bên trong và công cụ không mở được.
2. Mở Get-Design-Markdown.exe.
3. Lần đầu, Windows có thể hiện "Windows protected your PC" (SmartScreen) vì bản exe chưa
   được ký số: bấm "More info" (Thêm thông tin) rồi "Run anyway" (Vẫn chạy).
4. Dán địa chỉ trang web vào ô URL và bấm "Phân tích".


Trình duyệt
-----------
- Công cụ mở trang cần phân tích bằng Microsoft Edge (có sẵn trên Windows 10/11) hoặc
  Google Chrome, không cần cài thêm gì.
- Nếu máy không có cả hai, bấm nút "Tải Chromium" trong giao diện, một lần:
  tải về khoảng 125 MB, chiếm khoảng 300 MB trên ổ đĩa.
- Cửa sổ giao diện dùng Microsoft Edge WebView2 Runtime (có sẵn trên Windows 11). Nếu máy
  thiếu, giao diện tự mở trong trình duyệt; tải WebView2 tại
  https://go.microsoft.com/fwlink/p/?LinkId=2124703


Kết quả
-------
Mặc định nằm trong Documents\Get-Design-Markdown\<tên miền>\ : DESIGN.md, tokens.json,
preview.html, ảnh chụp màn hình. Có thể đổi thư mục trong "Tùy chọn nâng cao".


Khi gặp sự cố
-------------
- Nhật ký: %LOCALAPPDATA%\get-design-markdown\logs\ui.log
- Công cụ báo không khởi động được: chép cả thư mục Get-Design-Markdown sang chỗ có đường dẫn
  ngắn hơn (ví dụ C:\Apps) rồi mở lại.
- Tự kiểm tra bản cài: mở Command Prompt trong thư mục này và chạy
      start /wait Get-Design-Markdown.exe --self-test --self-test-report self-test.json
  rồi mở self-test.json ("ok": true là mọi thứ đều chạy được).
- Mở giao diện trong trình duyệt thay vì cửa sổ riêng:
      Get-Design-Markdown.exe --browser-tab


Mã nguồn và giấy phép
---------------------
Phần mềm tự do theo giấy phép GPL-3.0-or-later (xem LICENSE.txt).
Bản exe kèm theo các thành phần mã nguồn mở khác (Python, OpenSSL, pywebview, pythonnet,
Playwright, WebView2 SDK...): tên, giấy phép và thông báo bản quyền của từng thành phần nằm
trong THIRD-PARTY-NOTICES.txt.
Mã nguồn và hướng dẫn đầy đủ: https://github.com/hasoftware/Get-Design-Markdown
