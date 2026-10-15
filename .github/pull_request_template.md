## Mô tả

<!-- Thay đổi gì, vì sao. Liên kết issue nếu có. -->

## Danh sách kiểm tra

- [ ] Đã chạy đủ cổng kiểm tra và tất cả đều pass:
      `uv run ruff check .` · `uv run ruff format --check .` · `uv run mypy src tests` ·
      `uv run mypy scripts` · `uv run pytest` (có Chromium; `GDM_REQUIRE_BROWSER=1` nếu muốn bắt buộc)
- [ ] Chỉ sửa các file thuộc phạm vi của mình; không sửa file đóng băng (models, glue CLI/pipeline,
      `css_values`/`common`/`naming`, `pyproject.toml`, `uv.lock`, fixture dùng chung…)
- [ ] Mọi thay đổi cần ở file đóng băng được ghi thành **change request** bên dưới (file, thay đổi
      chính xác, lý do)
- [ ] Không commit file cục bộ: `CLAUDE.md`, `.env`, thư mục `output/`, `e2e-output/`
- [ ] Đã chạy thử thật (`gdm get https://stripe.com`, `gdm get https://linear.app`) và kiểm
      thư mục kết quả bằng `uv run python scripts/verify_output.py output/<domain>`
- [ ] Commit message tiếng Anh dạng Conventional Commits, không emoji
- [ ] Thông báo cho người dùng viết tiếng Việt có dấu đầy đủ

## Change request (nếu có)

| File | Thay đổi | Lý do |
|---|---|---|
| | | |

## Kết quả chạy thử thật

<!-- Dữ liệu thu được trên stripe.com / linear.app, kết quả verify_output, vấn đề còn tồn tại. -->
