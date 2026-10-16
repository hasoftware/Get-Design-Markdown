"""Local web UI: `gdm ui`, `gdm-ui` and the Windows exe.

A stdlib HTTP server bound to 127.0.0.1 serves a static HTML/CSS/JS frontend (ui/static, no
build step, no CDN) and a small JSON API that runs the same pipeline as `gdm get`. The page is
shown in a browser tab or in a pywebview window.

- settings.py         UiSettings, config / log / output folders, load and save
- jobs.py             job models, the default runner (pipeline.run_get) and JobManager
- history.py          analysed sites under the output root
- render_md.py        safe Markdown rendering (markdown-it-py, raw HTML disabled)
- server.py           the HTTP API (its docstring is the contract) and UiServer
- browser_install.py  browser availability and the Chromium download
- app.py              run_ui() for `gdm ui`, main() for `gdm-ui` and the exe (self-test...)
"""
