"""Application shell with sidebar navigation."""

from __future__ import annotations

import flet as ft

from app import auth
from app import database
from app.components import nav_button, app_footer
from app.pages import history, home, new_scan, settings
from app.paths import init_app_storage, logo_src
from app.theme import BG_MAIN, BG_SIDEBAR, FONT_FAMILY, PRIMARY, SIDEBAR_WIDTH, TEXT
from app.update_check import (
    GITHUB_REPO_URL,
    UpdateInfo,
    check_for_update_async,
    dismiss_update,
)


class ScannerApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.current_view = "home"
        self.history_session_id: int | None = None
        self.resume_session_id: int | None = None
        self.admin_username: str | None = None
        self.admin_role: str | None = None
        self.pending_admin_action = None
        self.history_filters: dict[str, str] = {
            "sales_order": "",
            "date_from": "",
            "date_to": "",
            "status": "all",
        }

        page.title = "Picking Barcode Scanner"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = ft.Theme(font_family=FONT_FAMILY)
        page.padding = 0
        page.spacing = 0
        page.bgcolor = BG_MAIN

        database.init_db()
        auth.ensure_admins_file()

        page._scanner_app = self
        stored_user = getattr(page, "_session_username", None)
        stored_role = getattr(page, "_session_role", None)
        if stored_user and not self.admin_username:
            self.admin_username = stored_user
            self.admin_role = stored_role

        self.file_picker = ft.FilePicker()
        self.url_launcher = ft.UrlLauncher()
        page.services.append(self.file_picker)
        page.services.append(self.url_launcher)

        self.page_body = ft.Container(expand=True)
        self.update_banner = ft.Container(visible=False)
        self.content_area = ft.Container(
            content=ft.Column(
                [
                    self.update_banner,
                    self.page_body,
                    app_footer(),
                ],
                expand=True,
                spacing=0,
            ),
            expand=True,
        )
        self.nav_buttons: dict[str, ft.Container] = {}

        self.layout = ft.Row(
            [
                self._build_sidebar(),
                ft.VerticalDivider(width=1, color="#D0D0D0"),
                self.content_area,
            ],
            expand=True,
            spacing=0,
        )
        page.add(self.layout)
        self.navigate("home")
        self._start_update_check()

    def _build_sidebar(self) -> ft.Container:
        self.nav_buttons = {
            "home": nav_button("Home", ft.Icons.HOME, True, lambda _: self.navigate("home")),
            "new_scan": nav_button("New Scan", ft.Icons.QR_CODE_SCANNER, False, lambda _: self.navigate("new_scan")),
            "history": nav_button("History", ft.Icons.LIST_ALT, False, lambda _: self.navigate("history")),
            "settings": nav_button("Settings", ft.Icons.SETTINGS, False, lambda _: self.navigate("settings")),
        }
        logo = ft.Container(
            content=ft.Image(
                src=logo_src(),
                width=SIDEBAR_WIDTH - 32,
                fit=ft.BoxFit.CONTAIN,
            ),
            bgcolor=ft.Colors.BLACK,
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=10),
            margin=ft.Margin.only(top=80),
            alignment=ft.Alignment(0, 0),
        )
        sidebar_column = ft.Column(
            [
                logo,
                self.nav_buttons["home"],
                self.nav_buttons["new_scan"],
                self.nav_buttons["history"],
                self.nav_buttons["settings"],
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        )
        sidebar_body = (
            ft.SafeArea(
                content=sidebar_column,
                minimum_padding=ft.Padding.only(top=12),
            )
            if self.page.platform.is_mobile()
            else sidebar_column
        )
        return ft.Container(
            content=sidebar_body,
            width=SIDEBAR_WIDTH,
            bgcolor=BG_SIDEBAR,
            padding=ft.Padding.only(left=16, right=16, bottom=12),
        )

    def show_snack(self, message: str, *, error: bool = False):
        self.page.show_dialog(
            ft.SnackBar(
                content=ft.Text(message, color=ft.Colors.WHITE),
                bgcolor="#E53935" if error else "#323232",
            )
        )

    def _start_update_check(self) -> None:
        def on_result(info: UpdateInfo | None):
            if not info:
                return

            def show_banner():
                try:
                    self._show_update_banner(info)
                except Exception:
                    pass

            try:
                self.page.run_thread(show_banner)
            except Exception:
                show_banner()

        check_for_update_async(on_result)

    def _show_update_banner(self, info: UpdateInfo) -> None:
        release_url = (info.release_url or GITHUB_REPO_URL).strip()
        # Prefer the releases landing page so tablets can download the APK.
        open_url = (
            f"{GITHUB_REPO_URL}/releases/tag/v{info.latest_version}"
            if info.latest_version
            else release_url
        )

        def on_dismiss(_=None):
            dismiss_update(info.latest_version)
            self.update_banner.visible = False
            self.update_banner.content = None
            self.page.update()

        async def on_open(_=None):
            await self._open_release_url(open_url)

        self.update_banner.content = ft.Container(
            bgcolor="#FFF8E1",
            border=ft.Border(bottom=ft.BorderSide(1, "#FFD54F")),
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.SYSTEM_UPDATE, color="#F9A825", size=22),
                    ft.Text(
                        f"New update available: v{info.latest_version} "
                        f"(you have v{info.current_version})",
                        size=13,
                        color=TEXT,
                        font_family=FONT_FAMILY,
                        expand=True,
                    ),
                    ft.FilledButton(
                        "View update",
                        style=ft.ButtonStyle(bgcolor=PRIMARY, color=ft.Colors.WHITE),
                        on_click=on_open,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        tooltip="Dismiss",
                        on_click=on_dismiss,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self.update_banner.visible = True
        self.page.update()

    async def _open_release_url(self, url: str) -> None:
        """Open the GitHub release page (in-app browser first, then system browser)."""
        target = (url or GITHUB_REPO_URL).strip()
        opened = await self._try_launch_url(target)
        if opened:
            return
        # Always give the user a tappable/copyable link if launch fails.
        self._show_update_link_dialog(target)

    async def _try_launch_url(self, target: str) -> bool:
        # In-app web view works on Android without package-visibility <queries>.
        # External browser needs those queries (patched into the APK at build time).
        modes = [
            ft.LaunchMode.IN_APP_WEB_VIEW,
            ft.LaunchMode.IN_APP_BROWSER_VIEW,
            ft.LaunchMode.EXTERNAL_APPLICATION,
            ft.LaunchMode.PLATFORM_DEFAULT,
        ]
        launcher = self.url_launcher
        for mode in modes:
            try:
                # Capture the Flutter plugin return value (False = failed to open).
                result = await launcher._invoke_method(
                    "launch_url",
                    {
                        "url": target,
                        "mode": mode,
                        "web_view_configuration": None,
                        "browser_configuration": None,
                        "web_only_window_name": None,
                    },
                )
                if result is False:
                    continue
                return True
            except Exception:
                continue

        # Desktop fallback
        try:
            import webbrowser

            if webbrowser.open(target):
                return True
        except Exception:
            pass

        try:
            import os
            import subprocess
            import sys

            if sys.platform.startswith("win"):
                os.startfile(target)  # type: ignore[attr-defined]
                return True
            if sys.platform == "darwin":
                subprocess.Popen(["open", target])
                return True
            subprocess.Popen(["xdg-open", target])
            return True
        except Exception:
            return False

    def _show_update_link_dialog(self, url: str) -> None:
        async def retry_open(_=None):
            opened = await self._try_launch_url(url)
            if opened:
                self.page.pop_dialog()
            else:
                self.show_snack(
                    "Could not open browser. Long-press the link to copy it.",
                    error=True,
                )

        link = ft.Text(
            spans=[
                ft.TextSpan(
                    url,
                    style=ft.TextStyle(
                        color=PRIMARY,
                        decoration=ft.TextDecoration.UNDERLINE,
                        size=13,
                    ),
                    url=url,
                    on_click=retry_open,
                )
            ],
            selectable=True,
            font_family=FONT_FAMILY,
        )

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Download update", font_family=FONT_FAMILY),
                content=ft.Column(
                    [
                        ft.Text(
                            "Open this page in your browser to download the latest APK:",
                            font_family=FONT_FAMILY,
                            size=13,
                        ),
                        link,
                    ],
                    tight=True,
                    spacing=12,
                    width=420,
                ),
                actions=[
                    ft.TextButton("Close", on_click=lambda _: self.page.pop_dialog()),
                    ft.TextButton("Open page", on_click=retry_open),
                ],
            )
        )

    def navigate(
        self,
        view: str,
        *,
        session_id: int | None = None,
        resume: bool = False,
    ):
        self.current_view = view
        if resume and session_id is not None:
            self.resume_session_id = session_id
            self.history_session_id = None
        elif view == "history_detail" and session_id is not None:
            self.history_session_id = session_id
            self.resume_session_id = None
        elif view == "history":
            self.history_session_id = session_id
            self.resume_session_id = None
        elif view not in ("history", "history_detail"):
            self.history_session_id = None
            if not resume:
                self.resume_session_id = None

        for key, btn in self.nav_buttons.items():
            active = key == view or (view == "history_detail" and key == "history")
            inner = btn.content
            inner.bgcolor = "#1E88E5" if active else "#42A5F5"

        if view == "home":
            self.page_body.content = home.build(
                self.page,
                self.navigate,
                self.show_snack,
                self.file_picker,
                admin_username=self.admin_username,
                admin_role=self.admin_role,
            )
        elif view == "settings":
            self.page_body.content = settings.build(
                self.page,
                self.navigate,
                self.show_snack,
                self.file_picker,
                admin_username=self.admin_username,
                admin_role=self.admin_role,
                login_admin=self.login_admin,
                logout_admin=self.logout_admin,
                create_user=self.create_user,
                set_user_password=self.set_user_password,
                set_user_role=self.set_user_role,
                delete_admin_user=self.delete_admin_user,
                list_admin_users=self.list_admin_users,
            )
        elif view == "new_scan":
            scan_focus: dict = {}
            self.page_body.content = new_scan.build(
                self.page,
                self.navigate,
                self.show_snack,
                self.file_picker,
                resume_session_id=self.resume_session_id,
                scan_focus=scan_focus,
                logged_in_username=self.admin_username,
                logged_in_role=self.admin_role,
                login_admin=self.login_admin,
                get_logged_in=self.get_logged_in,
            )
            self.resume_session_id = None
            self.page.update()
            post_build = scan_focus.get("post_build")
            if post_build:
                post_build()
            return
        elif view in ("history", "history_detail"):
            try:
                if view == "history_detail" and self.history_session_id is not None:
                    self.page_body.content = history.build_session_detail(
                        self.page,
                        self.navigate,
                        self.show_snack,
                        self.file_picker,
                        session_id=self.history_session_id,
                        admin_username=self.admin_username,
                        admin_role=self.admin_role,
                        login_admin=self.login_admin,
                        logout_admin=self.logout_admin,
                        queue_admin_action=lambda fn: setattr(self, "pending_admin_action", fn),
                    )
                else:
                    self.page_body.content = history.build(
                        self.page,
                        self.navigate,
                        self.show_snack,
                        self.file_picker,
                        admin_username=self.admin_username,
                        admin_role=self.admin_role,
                        login_admin=self.login_admin,
                        logout_admin=self.logout_admin,
                        queue_admin_action=lambda fn: setattr(self, "pending_admin_action", fn),
                        filters=self.history_filters,
                        on_filters_change=lambda f: setattr(self, "history_filters", f),
                    )
            except Exception as exc:
                import traceback

                traceback.print_exc()
                self.page_body.content = ft.Container(
                    content=ft.Column(
                        [
                            ft.Text("History failed to load", size=20, weight=ft.FontWeight.BOLD),
                            ft.Text(str(exc), color="#E53935"),
                        ],
                        spacing=12,
                    ),
                    padding=24,
                )
                self.show_snack(f"History error: {exc}", error=True)
            if self.pending_admin_action:
                action = self.pending_admin_action
                self.pending_admin_action = None
                action()

        self.page.update()

    @property
    def is_admin(self) -> bool:
        return self.admin_username is not None

    def login_admin(self, username: str, password: str) -> bool:
        account = auth.authenticate(username, password)
        if account:
            self.admin_username = account.username
            self.admin_role = account.role
            self.page._session_username = account.username
            self.page._session_role = account.role
            return True
        return False

    def get_logged_in(self) -> tuple[str | None, str | None]:
        if self.admin_username:
            return self.admin_username, self.admin_role
        stored_user = getattr(self.page, "_session_username", None)
        stored_role = getattr(self.page, "_session_role", None)
        if stored_user:
            self.admin_username = stored_user
            self.admin_role = stored_role
            return stored_user, stored_role
        return None, None

    def logout_admin(self) -> None:
        self.admin_username = None
        self.admin_role = None
        self.page._session_username = None
        self.page._session_role = None

    def create_user(
        self,
        username: str,
        password: str,
        *,
        role: str = auth.ROLE_ADMIN,
    ) -> auth.AdminAccount:
        if not self.admin_username:
            raise PermissionError("Admin login required.")
        return auth.create_user(self.admin_username, username, password, role=role)

    def delete_admin_user(self, username: str) -> None:
        if not self.admin_username:
            raise PermissionError("Admin login required.")
        auth.delete_admin(self.admin_username, username)

    def set_user_password(self, username: str, new_password: str) -> None:
        if not self.admin_username:
            raise PermissionError("Admin login required.")
        auth.set_user_password(self.admin_username, username, new_password)

    def set_user_role(self, username: str, role: str) -> auth.AdminAccount:
        if not self.admin_username:
            raise PermissionError("Admin login required.")
        return auth.set_user_role(self.admin_username, username, role)

    def list_admin_users(self) -> list[auth.AdminAccount]:
        if not self.admin_username:
            raise PermissionError("Admin login required.")
        return auth.list_admin_accounts(self.admin_username)


def main(page: ft.Page):
    async def bootstrap():
        await init_app_storage(page)
        if not page.web and not page.platform.is_mobile():
            page.window.width = 1280
            page.window.height = 800
            page.window.min_width = 900
            page.window.min_height = 600
            await page.window.center()
            await page.window.to_front()
        ScannerApp(page)

    page.run_task(bootstrap)
