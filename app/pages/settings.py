"""Settings page — accounts, cloud sync, barcode master list."""

from __future__ import annotations

from pathlib import Path

import flet as ft

from app import auth
from app import barcode_catalog
from app import cloud_sync
from app.components import action_button, muted, section_title
from app.theme import BG_MAIN, FONT_FAMILY, MIN_TOUCH, PRIMARY, TEXT


def _card(title: str, subtitle: str, *controls: ft.Control) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    title,
                    size=16,
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                muted(subtitle),
                *controls,
            ],
            spacing=10,
        ),
        bgcolor=ft.Colors.WHITE,
        border_radius=8,
        padding=16,
    )


def build(
    page: ft.Page,
    navigate,
    show_snack,
    file_picker: ft.FilePicker,
    *,
    admin_username: str | None,
    admin_role: str | None,
    login_admin,
    logout_admin,
    create_user,
    set_user_password,
    set_user_role,
    delete_admin_user,
    list_admin_users,
) -> ft.Control:
    is_admin = bool(admin_username)
    is_super_admin = auth.is_super_admin(admin_username)
    role_label = auth.ROLE_LABELS.get(admin_role or "", "Admin")
    catalog_label = muted(barcode_catalog.catalog_status_text())
    master_path_label = muted(str(barcode_catalog.get_master_path()))

    # --- Login / logout ---------------------------------------------------------

    def open_login_dialog(_=None):
        username_field = ft.TextField(label="Username", autofocus=True)
        password_field = ft.TextField(
            label="Password",
            password=True,
            can_reveal_password=True,
        )

        def close_dialog(_=None):
            page.pop_dialog()

        def submit_login(_=None):
            name = (username_field.value or "").strip()
            if login_admin(name, password_field.value or ""):
                page.pop_dialog()
                show_snack(f"Signed in — {name}")
                navigate("settings")
            else:
                show_snack("Invalid username or password.", error=True)

        password_field.on_submit = submit_login
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Sign In"),
                content=ft.Column(
                    [
                        muted("Sign in to manage users, barcode list, and cloud sync."),
                        username_field,
                        password_field,
                    ],
                    tight=True,
                    spacing=12,
                    width=320,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Login", on_click=submit_login),
                ],
            )
        )

    def on_logout(_=None):
        logout_admin()
        show_snack("Signed out.")
        navigate("settings")

    admin_status = (
        ft.Row(
            [
                ft.Icon(ft.Icons.ADMIN_PANEL_SETTINGS, color="#43A047", size=18),
                muted(f"Logged in as {admin_username} ({role_label})"),
                ft.TextButton("Logout", on_click=on_logout),
            ],
            spacing=8,
            wrap=True,
        )
        if is_admin
        else ft.Row(
            [
                muted("Sign in to manage protected settings."),
                ft.TextButton("Sign In", on_click=open_login_dialog),
            ],
            spacing=8,
            wrap=True,
        )
    )

    # --- Admin accounts ---------------------------------------------------------

    def open_add_user_dialog(_=None):
        username_field = ft.TextField(label="Username", autofocus=True)
        password_field = ft.TextField(
            label="Password",
            password=True,
            can_reveal_password=True,
        )
        role_field = ft.Dropdown(
            label="Role",
            value=auth.ROLE_CHECKER,
            options=[
                ft.dropdown.Option(auth.ROLE_CHECKER, "Checker"),
                ft.dropdown.Option(auth.ROLE_ADMIN, "Admin"),
            ],
            width=320,
        )

        def close_dialog(_=None):
            page.pop_dialog()

        def submit_create(_=None):
            try:
                account = create_user(
                    (username_field.value or "").strip(),
                    password_field.value or "",
                    role=role_field.value or auth.ROLE_CHECKER,
                )
                page.pop_dialog()
                show_snack(f"User added — {account.username} ({account.role_label}).")
                navigate("settings")
            except (ValueError, PermissionError) as exc:
                show_snack(str(exc), error=True)

        password_field.on_submit = submit_create
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Add User / Checker"),
                content=ft.Column(
                    [
                        muted("Create a Checker or Admin account."),
                        username_field,
                        password_field,
                        role_field,
                    ],
                    tight=True,
                    spacing=12,
                    width=320,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Add", on_click=submit_create),
                ],
            )
        )

    def open_set_password_dialog(target_username: str):
        new_field = ft.TextField(
            label="New password",
            password=True,
            can_reveal_password=True,
            autofocus=True,
        )
        confirm_field = ft.TextField(
            label="Confirm new password",
            password=True,
            can_reveal_password=True,
        )

        def close_dialog(_=None):
            page.pop_dialog()

        def submit_set(_=None):
            if (new_field.value or "") != (confirm_field.value or ""):
                show_snack("Passwords do not match.", error=True)
                return
            try:
                set_user_password(target_username, new_field.value or "")
                page.pop_dialog()
                show_snack(f"Password set for {target_username}.")
            except (ValueError, PermissionError) as exc:
                show_snack(str(exc), error=True)

        confirm_field.on_submit = submit_set
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Set Password"),
                content=ft.Column(
                    [
                        muted(f"Set the password for {target_username}."),
                        new_field,
                        confirm_field,
                    ],
                    tight=True,
                    spacing=12,
                    width=320,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Save", on_click=submit_set),
                ],
            )
        )

    def open_set_role_dialog(target_username: str, current_role: str):
        if current_role == auth.ROLE_SUPER_ADMIN:
            show_snack("Super Admin role cannot be changed here.", error=True)
            return

        role_field = ft.Dropdown(
            label="Role",
            value=current_role,
            options=[
                ft.dropdown.Option(auth.ROLE_CHECKER, "Checker"),
                ft.dropdown.Option(auth.ROLE_ADMIN, "Admin"),
            ],
            width=320,
        )

        def close_dialog(_=None):
            page.pop_dialog()

        def submit_role(_=None):
            try:
                account = set_user_role(
                    target_username,
                    role_field.value or auth.ROLE_CHECKER,
                )
                page.pop_dialog()
                show_snack(
                    f"Role updated — {account.username} is now {account.role_label}."
                )
                navigate("settings")
            except (ValueError, PermissionError) as exc:
                show_snack(str(exc), error=True)

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Change Role"),
                content=ft.Column(
                    [
                        muted(f"Set the role for {target_username}."),
                        role_field,
                    ],
                    tight=True,
                    spacing=12,
                    width=320,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Save", on_click=submit_role),
                ],
            )
        )

    def confirm_delete_admin(target_username: str):
        def close_dialog(_=None):
            page.pop_dialog()

        def submit_delete(_=None):
            try:
                delete_admin_user(target_username)
                page.pop_dialog()
                show_snack(f"Deleted user — {target_username}.")
                navigate("settings")
            except (ValueError, PermissionError) as exc:
                show_snack(str(exc), error=True)

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Delete User"),
                content=ft.Text(
                    f"Delete the account '{target_username}'? This cannot be undone.",
                    font_family=FONT_FAMILY,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Delete", on_click=submit_delete),
                ],
            )
        )

    account_controls: list[ft.Control] = []
    if is_super_admin:
        try:
            admin_accounts = list_admin_users()
        except PermissionError:
            admin_accounts = []

        account_rows = ft.Column(spacing=4)
        for account in admin_accounts:
            trailing_actions = [
                ft.IconButton(
                    icon=ft.Icons.KEY,
                    icon_color=PRIMARY,
                    tooltip="Set password",
                    on_click=lambda _, name=account.username: open_set_password_dialog(
                        name
                    ),
                ),
            ]
            if account.role != auth.ROLE_SUPER_ADMIN:
                trailing_actions.insert(
                    0,
                    ft.IconButton(
                        icon=ft.Icons.BADGE,
                        icon_color=PRIMARY,
                        tooltip="Change role",
                        on_click=lambda _, name=account.username, role=account.role: open_set_role_dialog(
                            name, role
                        ),
                    ),
                )
            if account.username != admin_username:
                trailing_actions.append(
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color="#E53935",
                        tooltip="Delete account",
                        on_click=lambda _, name=account.username: confirm_delete_admin(
                            name
                        ),
                    )
                )
            if account.role == auth.ROLE_SUPER_ADMIN:
                leading_icon = ft.Icons.SHIELD
            elif account.role == auth.ROLE_CHECKER:
                leading_icon = ft.Icons.VERIFIED_USER
            else:
                leading_icon = ft.Icons.PERSON
            account_rows.controls.append(
                ft.ListTile(
                    leading=ft.Icon(leading_icon, color=PRIMARY),
                    title=ft.Text(account.username, font_family=FONT_FAMILY),
                    subtitle=muted(account.role_label),
                    trailing=ft.Row(trailing_actions, tight=True),
                )
            )

        account_controls.extend(
            [
                ft.Row(
                    [
                        action_button(
                            "Add User / Checker",
                            ft.Icons.PERSON_ADD,
                            on_click=open_add_user_dialog,
                        ),
                    ],
                    wrap=True,
                ),
                ft.Container(
                    content=account_rows,
                    border=ft.Border.all(1, "#E0E0E0"),
                    border_radius=8,
                    padding=4,
                ),
            ]
        )
    elif is_admin:
        account_controls.append(
            muted("Only Super Admin can add Checkers and manage user accounts.")
        )
    else:
        account_controls.append(muted("Sign in as Super Admin to manage users."))

    accounts_section = _card(
        "Admin Accounts",
        "Add Checkers and Admins, set passwords, and change roles.",
        admin_status,
        *account_controls,
    )

    # --- Barcode master list ----------------------------------------------------

    async def handle_barcode_master_pick(_=None):
        files = await file_picker.pick_files(
            dialog_title="Select Default Barcode Master List",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx"],
            allow_multiple=False,
            with_data=True,
        )
        if not files:
            return

        selected = files[0]
        try:
            if selected.path:
                barcode_catalog.set_default_master_path(selected.path)
                count = barcode_catalog.load_from_excel(barcode_catalog.get_master_path())
            elif selected.bytes:
                count = barcode_catalog.import_master_file(selected.bytes)
            else:
                show_snack("Could not read the selected Excel file.", error=True)
                return
            catalog_label.value = barcode_catalog.catalog_status_text()
            master_path_label.value = str(barcode_catalog.get_master_path())
            show_snack(f"Default barcode list set — {count:,} barcodes loaded.")
            page.update()
        except (ValueError, FileNotFoundError) as exc:
            show_snack(str(exc), error=True)
        except Exception:
            show_snack("Failed to import the barcode master list.", error=True)

    def on_update_barcode(_=None):
        if is_admin:
            page.run_task(handle_barcode_master_pick)
        else:
            open_login_dialog()

    barcode_section = _card(
        "Barcode Master List",
        "Set or update the default BarcodeMasterList.xlsx used for scanning.",
        ft.Text("Current file", size=13, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY, color=TEXT),
        master_path_label,
        catalog_label,
        ft.Row(
            [
                ft.ElevatedButton(
                    "Set / Update Default List",
                    icon=ft.Icons.TABLE_VIEW if is_admin else ft.Icons.LOCK,
                    bgcolor=PRIMARY if is_admin else "#9E9E9E",
                    color=ft.Colors.WHITE,
                    height=MIN_TOUCH,
                    on_click=on_update_barcode,
                ),
            ],
            wrap=True,
        ),
        muted("Only signed-in Admin or Super Admin can change this file."),
    )

    # --- Cloud sync -------------------------------------------------------------

    from flet.utils.platform_utils import is_mobile

    on_mobile = is_mobile()
    folder = cloud_sync.get_sync_folder()
    folder_label = muted(
        str(folder) if folder else "No sync folder selected yet."
    )
    oauth_status_label = muted("\n".join(cloud_sync.oauth_status_lines()))
    clear_folder_btn = ft.OutlinedButton(
        "Clear folder",
        height=MIN_TOUCH,
        disabled=folder is None,
    )
    path_field = ft.TextField(
        label="Or paste a folder path (PC)",
        value=str(folder) if folder else "",
        hint_text=r"e.g. C:\Users\...\OneDrive - Company\Reports",
        dense=True,
        expand=True,
        visible=not on_mobile,
    )

    def refresh_folder_ui():
        folder_now = cloud_sync.get_sync_folder()
        folder_label.value = (
            str(folder_now) if folder_now else "No sync folder selected yet."
        )
        clear_folder_btn.disabled = folder_now is None
        if not on_mobile:
            path_field.value = str(folder_now) if folder_now else (path_field.value or "")
        oauth_status_label.value = "\n".join(cloud_sync.oauth_status_lines())
        page.update()

    def apply_sync_folder(path: str | Path, *, label: str = "") -> None:
        cloud_sync.set_sync_folder(path)
        refresh_folder_ui()
        show_snack(
            f"Sync folder set — {label or path}"
            if label
            else f"Sync folder set — {path}"
        )

    async def pick_sync_folder(_=None):
        if not is_admin:
            open_login_dialog()
            return
        if on_mobile:
            # Android Drive/OneDrive shortcuts are not real folders — offer
            # writable local targets instead of the broken system picker.
            await choose_mobile_sync_folder()
            return
        try:
            path = await file_picker.get_directory_path(
                dialog_title="Choose a local OneDrive or Google Drive folder"
            )
        except Exception as exc:
            show_snack(f"Folder picker failed: {exc}", error=True)
            return
        if not path:
            return
        try:
            apply_sync_folder(path)
        except Exception as exc:
            show_snack(str(exc), error=True)

    async def choose_mobile_sync_folder(_=None):
        if not is_admin:
            open_login_dialog()
            return
        try:
            targets = await cloud_sync.resolve_mobile_sync_targets(page)
        except Exception as exc:
            show_snack(f"Could not list tablet folders: {exc}", error=True)
            return
        if not targets:
            show_snack(
                "No writable tablet folders found. Ask Super Admin to enable "
                "OneDrive / Google Drive sign-in.",
                error=True,
            )
            return

        selected = {"value": str(targets[0][1])}

        def close_dialog(_=None):
            page.pop_dialog()

        def confirm(_=None):
            raw = selected["value"]
            label = next((name for name, path in targets if str(path) == raw), "")
            try:
                page.pop_dialog()
                apply_sync_folder(raw, label=label or raw)
            except Exception as exc:
                show_snack(str(exc), error=True)

        radios = [
            ft.Radio(value=str(path), label=f"{name}  →  {path}")
            for name, path in targets
        ]
        group = ft.RadioGroup(
            value=selected["value"],
            content=ft.Column(radios, tight=True, spacing=8),
            on_change=lambda e: selected.update(value=e.control.value),
        )
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Choose tablet sync folder"),
                content=ft.Column(
                    [
                        muted(
                            "Android cannot open Google Drive / OneDrive from the "
                            "folder picker. Choose a local folder below. History → "
                            "Sync will copy reports there (e.g. Downloads / "
                            f"{cloud_sync.CLOUD_FOLDER_NAME}). "
                            "For direct cloud upload, use Sign in with OneDrive."
                        ),
                        group,
                    ],
                    tight=True,
                    spacing=12,
                    width=420,
                    height=360,
                    scroll=ft.ScrollMode.AUTO,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Use this folder", on_click=confirm),
                ],
            )
        )

    def save_path_field(_=None):
        if not is_admin:
            open_login_dialog()
            return
        typed = (path_field.value or "").strip()
        if not typed:
            show_snack("Enter a folder path first.", error=True)
            return
        try:
            apply_sync_folder(typed)
        except Exception as exc:
            show_snack(str(exc), error=True)

    def clear_sync_folder(_=None):
        cloud_sync.set_sync_folder(None)
        path_field.value = ""
        refresh_folder_ui()
        show_snack("Cloud folder cleared.")

    clear_folder_btn.on_click = clear_sync_folder

    def run_cloud_sign_in(provider: str):
        if not is_admin:
            open_login_dialog()
            return
        status = {"text": f"Signing in to {cloud_sync.PROVIDER_LABELS.get(provider, provider)}…"}
        status_label = ft.Text(status["text"], size=13, font_family=FONT_FAMILY)

        def close_dialog(_=None):
            page.pop_dialog()

        def do_sign_in():
            try:
                def on_progress(msg: str):
                    status_label.value = msg
                    page.update()

                cloud_sync.sign_in(provider, on_progress=on_progress)
                page.pop_dialog()
                refresh_folder_ui()
                show_snack(
                    f"Signed in to {cloud_sync.PROVIDER_LABELS.get(provider, provider)}."
                )
            except Exception as exc:
                status_label.value = str(exc)
                page.update()
                show_snack(str(exc), error=True)

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(
                    f"Sign in — {cloud_sync.PROVIDER_LABELS.get(provider, provider)}"
                ),
                content=ft.Column(
                    [
                        muted(
                            "A browser or device-code prompt may open. "
                            "Stay on this screen until sign-in finishes."
                        ),
                        status_label,
                    ],
                    tight=True,
                    spacing=12,
                    width=360,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton(
                        "Continue",
                        on_click=lambda _: page.run_thread(do_sign_in),
                    ),
                ],
            )
        )

    def sign_out_provider(provider: str):
        cloud_sync.sign_out(provider)
        refresh_folder_ui()
        show_snack(f"Signed out of {cloud_sync.PROVIDER_LABELS.get(provider, provider)}.")

    oauth_buttons: list[ft.Control] = []
    if cloud_sync.oauth_available(cloud_sync.PROVIDER_GOOGLE):
        if cloud_sync.is_signed_in(cloud_sync.PROVIDER_GOOGLE):
            oauth_buttons.append(
                ft.OutlinedButton(
                    "Sign out of Google Drive",
                    height=MIN_TOUCH,
                    on_click=lambda _: sign_out_provider(cloud_sync.PROVIDER_GOOGLE),
                )
            )
        else:
            oauth_buttons.append(
                ft.ElevatedButton(
                    "Sign in with Google Drive",
                    icon=ft.Icons.CLOUD,
                    bgcolor=PRIMARY,
                    color=ft.Colors.WHITE,
                    height=MIN_TOUCH,
                    on_click=lambda _: run_cloud_sign_in(cloud_sync.PROVIDER_GOOGLE),
                )
            )
    if cloud_sync.oauth_available(cloud_sync.PROVIDER_ONEDRIVE):
        if cloud_sync.is_signed_in(cloud_sync.PROVIDER_ONEDRIVE):
            oauth_buttons.append(
                ft.OutlinedButton(
                    "Sign out of OneDrive",
                    height=MIN_TOUCH,
                    on_click=lambda _: sign_out_provider(cloud_sync.PROVIDER_ONEDRIVE),
                )
            )
        else:
            oauth_buttons.append(
                ft.ElevatedButton(
                    "Sign in with OneDrive",
                    icon=ft.Icons.CLOUD_UPLOAD,
                    bgcolor=PRIMARY,
                    color=ft.Colors.WHITE,
                    height=MIN_TOUCH,
                    on_click=lambda _: run_cloud_sign_in(cloud_sync.PROVIDER_ONEDRIVE),
                )
            )

    def open_oauth_setup_dialog(_=None):
        if not is_super_admin:
            show_snack("Only Super Admin can enable Google / OneDrive login.", error=True)
            return
        creds = cloud_sync.resolve_credentials()
        g = creds.get("google") or {}
        m = creds.get("microsoft") or {}
        google_id = ft.TextField(
            label="Google OAuth Client ID",
            value=g.get("client_id") or "",
            dense=True,
        )
        google_secret = ft.TextField(
            label="Google OAuth Client Secret",
            value=g.get("client_secret") or "",
            password=True,
            can_reveal_password=True,
            dense=True,
        )
        ms_id = ft.TextField(
            label="Microsoft (Azure) Application (client) ID",
            value=m.get("client_id") or "",
            dense=True,
        )
        ms_tenant = ft.TextField(
            label="Microsoft tenant",
            value=m.get("tenant") or "organizations",
            hint_text="organizations (work) or consumers (personal)",
            dense=True,
        )

        def close_dialog(_=None):
            page.pop_dialog()

        def save_creds(_=None):
            try:
                cloud_sync.save_credentials(
                    google_client_id=google_id.value or "",
                    google_client_secret=google_secret.value or "",
                    microsoft_client_id=ms_id.value or "",
                    microsoft_tenant=ms_tenant.value or "organizations",
                )
                page.pop_dialog()
                show_snack("Cloud login settings saved. Re-open Settings to refresh buttons.")
                navigate("settings")
            except Exception as exc:
                show_snack(str(exc), error=True)

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Enable Google / OneDrive login"),
                content=ft.Column(
                    [
                        muted(
                            "Register an app once in Google Cloud Console and Azure "
                            "Portal, then paste the IDs here. Tablets need this — "
                            "they cannot use the Google Drive folder picker."
                        ),
                        google_id,
                        google_secret,
                        ms_id,
                        ms_tenant,
                    ],
                    tight=True,
                    spacing=10,
                    width=420,
                    scroll=ft.ScrollMode.AUTO,
                    height=360,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Save", on_click=save_creds),
                ],
            )
        )

    cloud_controls: list[ft.Control] = [
        muted(
            "Tablet: tap “Choose tablet folder” and pick Downloads (recommended). "
            "Android cannot select Google Drive / OneDrive in the system picker. "
            "PC: choose your real OneDrive folder on disk "
            "(e.g. OneDrive - DEKS Industries…)."
            if on_mobile
            else "PC: pick your real OneDrive/Google Drive folder on disk "
            "(e.g. OneDrive - DEKS Industries…). "
            "Tablet builds use a Downloads folder instead of the Drive picker."
        ),
        ft.Text(
            "Sync folder"
            + (" (tablet)" if on_mobile else " (PC OneDrive / Drive folder)"),
            weight=ft.FontWeight.W_600,
            font_family=FONT_FAMILY,
        ),
        folder_label,
        ft.Row(
            [
                ft.ElevatedButton(
                    "Choose tablet folder" if on_mobile else "Choose cloud folder",
                    icon=ft.Icons.FOLDER_OPEN if is_admin else ft.Icons.LOCK,
                    bgcolor=PRIMARY if is_admin else "#9E9E9E",
                    color=ft.Colors.WHITE,
                    height=MIN_TOUCH,
                    on_click=lambda _: page.run_task(pick_sync_folder),
                ),
                clear_folder_btn,
            ],
            spacing=12,
            wrap=True,
        ),
    ]
    if not on_mobile:
        cloud_controls.append(
            ft.Row(
                [
                    path_field,
                    ft.OutlinedButton(
                        "Use path",
                        height=MIN_TOUCH,
                        on_click=save_path_field,
                    ),
                ],
                spacing=8,
            )
        )
    cloud_controls.extend(
        [
            ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
            ft.Text(
                "Direct cloud login (optional)",
                weight=ft.FontWeight.W_600,
                font_family=FONT_FAMILY,
            ),
            oauth_status_label,
        ]
    )
    if oauth_buttons:
        cloud_controls.append(ft.Row(oauth_buttons, spacing=12, wrap=True))
    else:
        cloud_controls.append(
            muted(
                "Google Drive and OneDrive sign-in are not enabled yet. "
                "Ask Super Admin to tap “Enable Google / OneDrive login”. "
                "On tablets you can still sync to Downloads with the button above."
            )
        )
    if is_super_admin:
        cloud_controls.append(
            ft.TextButton(
                "Enable Google / OneDrive login…",
                icon=ft.Icons.KEY,
                on_click=open_oauth_setup_dialog,
            )
        )
    cloud_controls.append(
        muted("Only signed-in Admin or Super Admin can change cloud settings.")
    )

    cloud_section = _card(
        "Cloud Sync",
        "Send History reports to a sync folder or OneDrive/Google Drive. "
        f"Files go under '{cloud_sync.CLOUD_FOLDER_NAME} - <login user>/History'.",
        *cloud_controls,
    )

    return ft.Container(
        content=ft.Column(
            [
                section_title("Settings"),
                muted("Accounts, barcode master list, and cloud sync configuration"),
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                accounts_section,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                barcode_section,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                cloud_section,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=24,
        expand=True,
        bgcolor=BG_MAIN,
    )
