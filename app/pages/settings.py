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
                ft.dropdown.Option(auth.ROLE_MONITOR_VIEWER, "Monitor Viewer"),
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
                ft.dropdown.Option(auth.ROLE_MONITOR_VIEWER, "Monitor Viewer"),
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
            elif account.role == auth.ROLE_MONITOR_VIEWER:
                leading_icon = ft.Icons.VISIBILITY
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
        "Add Checkers, Admins, and Monitor Viewers; set passwords and change roles.",
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
            # Primary tablet flow: Android Files app directory picker.
            await pick_from_android_files()
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

    async def pick_from_android_files(_=None):
        """Open the system Files picker so the user chooses a local save folder."""
        if not is_admin:
            open_login_dialog()
            return
        try:
            path = await file_picker.get_directory_path(
                dialog_title=(
                    "Choose save folder — Internal storage → Download "
                    "(not Google Drive / OneDrive)"
                ),
                initial_directory="/storage/emulated/0/Download",
            )
        except Exception as exc:
            show_snack(f"Files picker failed: {exc}", error=True)
            await choose_mobile_sync_folder()
            return
        if not path:
            # User cancelled — offer quick local fallbacks.
            await choose_mobile_sync_folder()
            return
        try:
            apply_sync_folder(path)
            show_snack(f"Save folder set — {path}")
        except Exception as exc:
            show_snack(str(exc), error=True)
            await choose_mobile_sync_folder()

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
                "No writable tablet folders found. Try again from Files: "
                "Internal storage → Download.",
                error=True,
            )
            return

        default = targets[0]
        for name, path in targets:
            if "shared downloads" in name.lower():
                default = (name, path)
                break
        selected = {"value": str(default[1])}

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

        def open_files_again(_=None):
            page.pop_dialog()
            page.run_task(pick_from_android_files)

        radios = []
        for name, path in targets:
            short = str(path)
            if len(short) > 64:
                short = "…" + short[-60:]
            radios.append(
                ft.Radio(
                    value=str(path),
                    label=f"{name}\n{short}",
                )
            )
        group = ft.RadioGroup(
            value=selected["value"],
            content=ft.Column(radios, tight=True, spacing=8),
            on_change=lambda e: selected.update(value=e.control.value),
        )
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Or pick a quick local folder"),
                content=ft.Column(
                    [
                        muted(
                            "Choose from Files is preferred. "
                            "In Files: ☰ menu → Internal storage → Download or "
                            "Documents. Do not select Google Drive / OneDrive.\n\n"
                            "Or use a quick folder below:"
                        ),
                        group,
                    ],
                    tight=True,
                    spacing=12,
                    width=420,
                    height=380,
                    scroll=ft.ScrollMode.AUTO,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Choose from Files…", on_click=open_files_again),
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
        if not cloud_sync.oauth_available(provider):
            show_snack(
                "Cloud sign-in is not set up in this app build yet. "
                "DEKS IT needs to register OneDrive/Google once — users only "
                "sign in with their normal email and password after that.",
                error=True,
            )
            return
        label = cloud_sync.PROVIDER_LABELS.get(provider, provider)
        status_label = ft.Text(
            f"Opening {label} sign-in…",
            size=13,
            font_family=FONT_FAMILY,
        )

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
                show_snack(f"Signed in to {label}.")
            except Exception as exc:
                status_label.value = str(exc)
                page.update()
                show_snack(str(exc), error=True)

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(f"Sign in — {label}"),
                content=ft.Column(
                    [
                        muted(
                            f"Sign in with your normal {label} email and password "
                            f"on the Microsoft/Google login page (or device code). "
                            "You do not enter Client ID or secrets here."
                        ),
                        status_label,
                    ],
                    tight=True,
                    spacing=12,
                    width=380,
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

    def start_cloud_sign_in(provider: str):
        if cloud_sync.oauth_available(provider):
            run_cloud_sign_in(provider)
            return
        show_snack(
            "Cloud sign-in is not configured in this build yet. "
            "Ask DEKS IT / Super Admin to enable it once — after that, "
            "everyone only uses normal email and password.",
            error=True,
        )

    if cloud_sync.oauth_available(cloud_sync.PROVIDER_ONEDRIVE) and cloud_sync.is_signed_in(
        cloud_sync.PROVIDER_ONEDRIVE
    ):
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
                bgcolor=PRIMARY if cloud_sync.oauth_available(cloud_sync.PROVIDER_ONEDRIVE) else "#9E9E9E",
                color=ft.Colors.WHITE,
                height=MIN_TOUCH,
                disabled=not is_admin,
                on_click=lambda _: start_cloud_sign_in(cloud_sync.PROVIDER_ONEDRIVE),
            )
        )

    if cloud_sync.oauth_available(cloud_sync.PROVIDER_GOOGLE) and cloud_sync.is_signed_in(
        cloud_sync.PROVIDER_GOOGLE
    ):
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
                bgcolor=PRIMARY if cloud_sync.oauth_available(cloud_sync.PROVIDER_GOOGLE) else "#9E9E9E",
                color=ft.Colors.WHITE,
                height=MIN_TOUCH,
                disabled=not is_admin,
                on_click=lambda _: start_cloud_sign_in(cloud_sync.PROVIDER_GOOGLE),
            )
        )

    def open_oauth_setup_dialog(_=None):
        """IT-only: bake-in override for app registration IDs (not end-user login)."""
        if not is_super_admin:
            show_snack("Only Super Admin / IT can configure cloud app registration.", error=True)
            return
        creds = cloud_sync.resolve_credentials()
        g = creds.get("google") or {}
        m = creds.get("microsoft") or {}
        google_id = ft.TextField(
            label="Google OAuth Client ID (IT only)",
            value=g.get("client_id") or "",
            dense=True,
        )
        google_secret = ft.TextField(
            label="Google OAuth Client Secret (IT only)",
            value=g.get("client_secret") or "",
            password=True,
            can_reveal_password=True,
            dense=True,
        )
        ms_id = ft.TextField(
            label="Microsoft Application (client) ID (IT only)",
            value=m.get("client_id") or "",
            dense=True,
        )
        ms_tenant = ft.TextField(
            label="Microsoft tenant",
            value=m.get("tenant") or "organizations",
            hint_text="organizations = DEKS work OneDrive",
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
                show_snack("Cloud app registration saved. Users can Sign in normally.")
                navigate("settings")
            except Exception as exc:
                show_snack(str(exc), error=True)

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("IT: cloud app registration"),
                content=ft.Column(
                    [
                        muted(
                            "End users never see these fields. This is the app’s "
                            "identity with Microsoft/Google (registered once). "
                            "After Save, Sign in asks only for the user’s normal "
                            "email and password on the Microsoft/Google website.\n\n"
                            "Preferred: put the IDs in the app build so every "
                            "tablet works without this screen."
                        ),
                        ms_id,
                        ms_tenant,
                        google_id,
                        google_secret,
                    ],
                    tight=True,
                    spacing=10,
                    width=440,
                    scroll=ft.ScrollMode.AUTO,
                    height=400,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Save", on_click=save_creds),
                ],
            )
        )

    async def save_todays_report_to_drive(_=None):
        if not is_admin:
            open_login_dialog()
            return
        from app.pages.history import _save_todays_report_to_files_app

        try:
            await _save_todays_report_to_files_app(
                page,
                file_picker,
                admin_username=admin_username,
                show_snack=show_snack,
            )
        except Exception as exc:
            show_snack(f"Save failed: {exc}", error=True)

    cloud_controls: list[ft.Control] = []
    if on_mobile:
        cloud_controls.extend(
            [
                ft.Text(
                    "Save reports to Google Drive",
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                muted(
                    "Samsung can show Google Drive in Files, but this app cannot "
                    "use “Use this folder” on Drive — Android only gives a shortcut "
                    "path that Python cannot write to.\n\n"
                    "What works: History → Save to Drive, or Sync today now below. "
                    "In the Save dialog, tap the menu (☰) and choose Google Drive "
                    "(or OneDrive / Downloads), then Save."
                ),
                ft.ElevatedButton(
                    "Save today's report to Drive…",
                    icon=ft.Icons.CLOUD_UPLOAD,
                    bgcolor=PRIMARY if is_admin else "#9E9E9E",
                    color=ft.Colors.WHITE,
                    height=MIN_TOUCH,
                    disabled=not is_admin,
                    on_click=lambda _: page.run_task(save_todays_report_to_drive),
                ),
            ]
        )
        if cloud_sync.oauth_available():
            cloud_controls.extend(
                [
                    ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Optional: Sign in for automatic upload",
                        weight=ft.FontWeight.W_600,
                        font_family=FONT_FAMILY,
                    ),
                    muted(
                        "Only if DEKS IT enabled cloud sign-in in this build. "
                        "You still use your normal Microsoft/Google password."
                    ),
                    oauth_status_label,
                    ft.Row(oauth_buttons, spacing=12, wrap=True),
                ]
            )
    else:
        cloud_controls.extend(
            [
                ft.Text(
                    "Save to cloud",
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                muted(
                    "Tap Sign in, then use your normal Microsoft or Google email and "
                    "password on their login page. No Client ID or secret for warehouse users."
                ),
                oauth_status_label,
                ft.Row(oauth_buttons, spacing=12, wrap=True),
            ]
        )
        if not cloud_sync.oauth_available():
            cloud_controls.append(
                muted(
                    "Not ready yet: DEKS IT must register OneDrive/Google for this app "
                    "once (or Super Admin uses IT setup below). After that, Sign in "
                    "works with normal username/password only."
                )
            )
    if is_super_admin:
        cloud_controls.append(
            ft.TextButton(
                "IT setup (app registration)…",
                icon=ft.Icons.KEY,
                on_click=open_oauth_setup_dialog,
            )
        )

    cloud_controls.extend(
        [
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            ft.Text(
                "Local folder (optional backup)",
                weight=ft.FontWeight.W_600,
                font_family=FONT_FAMILY,
            ),
            muted(
                "Tablet: only local folders (e.g. Download) — not Google Drive "
                "in the folder picker."
                if on_mobile
                else "Choose a OneDrive or Google Drive folder on this PC."
            ),
            folder_label,
            ft.Row(
                [
                    ft.OutlinedButton(
                        "Choose local folder" if on_mobile else "Choose cloud folder",
                        icon=ft.Icons.FOLDER_OPEN if is_admin else ft.Icons.LOCK,
                        height=MIN_TOUCH,
                        on_click=lambda _: page.run_task(pick_sync_folder),
                    ),
                    clear_folder_btn,
                ],
                spacing=12,
                wrap=True,
            ),
        ]
    )
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
    cloud_controls.append(
        muted("Only signed-in Admin or Super Admin can change cloud settings.")
    )

    # --- Daily auto-sync --------------------------------------------------------

    from datetime import time as dt_time

    from app import scheduled_sync

    auto_enabled = scheduled_sync.get_auto_sync_enabled()
    auto_time = scheduled_sync.get_auto_sync_time()
    auto_status = muted(scheduled_sync.auto_sync_status_text())
    auto_switch = ft.Switch(
        label="Auto-sync today's sessions",
        value=auto_enabled,
        disabled=not is_admin,
    )
    time_label = muted(f"Scheduled time: {auto_time}")

    def refresh_auto_status():
        auto_status.value = scheduled_sync.auto_sync_status_text()
        time_label.value = f"Scheduled time: {scheduled_sync.get_auto_sync_time()}"
        page.update()

    def save_auto_sync(enabled: bool | None = None, sync_time: str | None = None):
        if not is_admin:
            open_login_dialog()
            return
        try:
            scheduled_sync.set_auto_sync(
                enabled=auto_switch.value if enabled is None else enabled,
                sync_time=sync_time or scheduled_sync.get_auto_sync_time(),
            )
            refresh_auto_status()
            show_snack("Daily auto-sync settings saved.")
        except Exception as exc:
            show_snack(str(exc), error=True)
            auto_switch.value = scheduled_sync.get_auto_sync_enabled()
            page.update()

    def on_auto_switch(e):
        if not is_admin:
            auto_switch.value = scheduled_sync.get_auto_sync_enabled()
            page.update()
            open_login_dialog()
            return
        save_auto_sync(enabled=bool(e.control.value))

    auto_switch.on_change = on_auto_switch

    def open_time_picker(_=None):
        if not is_admin:
            open_login_dialog()
            return
        current = scheduled_sync.get_auto_sync_time()
        h, m = map(int, current.split(":"))

        def on_time_change(e):
            value = e.control.value
            if value is None:
                return
            stamp = f"{value.hour:02d}:{value.minute:02d}"
            save_auto_sync(enabled=auto_switch.value, sync_time=stamp)

        picker = ft.TimePicker(
            value=dt_time(hour=h, minute=m),
            help_text="Daily sync time",
            confirm_text="Save",
            cancel_text="Cancel",
            hour_format=ft.TimePickerHourFormat.H24,
            on_change=on_time_change,
        )
        page.show_dialog(picker)

    def run_auto_sync_now(_=None):
        if not is_admin:
            open_login_dialog()
            return

        if on_mobile:
            # Folder sync cannot write to Google Drive shortcuts; use Save dialog.
            page.run_task(save_todays_report_to_drive)
            return

        def work():
            try:
                result = scheduled_sync.run_todays_sessions_sync(
                    checker_username=admin_username,
                    force=True,
                )
                refresh_auto_status()
                if result is None:
                    show_snack("No sessions for today to sync (or already nothing to do).")
                else:
                    show_snack(
                        f"Synced {len(result.uploaded)} file(s) for today."
                    )
            except Exception as exc:
                show_snack(f"Sync failed: {exc}", error=True)

        show_snack("Syncing today's sessions…")
        page.run_thread(work)

    auto_sync_section = _card(
        "Daily Auto-Sync",
        (
            "On tablet, use Sync today now to open the Save dialog and pick "
            "Google Drive. Automatic silent upload to Drive needs Sign-in "
            "(IT setup) or a local Download folder."
            if on_mobile
            else (
                "When enabled, all sessions for today are synced around the time you set "
                "(while the app is open). Uses your Cloud Sync folder or signed-in Drive."
            )
        ),
        auto_switch,
        time_label,
        auto_status,
        ft.Row(
            [
                ft.OutlinedButton(
                    "Set time",
                    icon=ft.Icons.SCHEDULE,
                    height=MIN_TOUCH,
                    disabled=not is_admin,
                    on_click=open_time_picker,
                ),
                ft.ElevatedButton(
                    "Sync today now",
                    icon=ft.Icons.CLOUD_UPLOAD,
                    bgcolor=PRIMARY if is_admin else "#9E9E9E",
                    color=ft.Colors.WHITE,
                    height=MIN_TOUCH,
                    disabled=not is_admin,
                    on_click=run_auto_sync_now,
                ),
            ],
            spacing=12,
            wrap=True,
        ),
        muted(
            "Tip: leave the app open near the scheduled time. "
            "If the tablet was asleep, it will catch up within about 5 minutes "
            "after the set time when the app is active again."
            if not on_mobile
            else (
                "Tip: for Google Drive, tap Sync today now (or History → Save to Drive) "
                "and choose Google Drive in the Save dialog — not “Use this folder”."
            )
        ),
    )

    cloud_section = _card(
        "Cloud Sync",
        (
            "Save PDF reports into Google Drive via the Android Save dialog. "
            "Choosing a Drive folder with “Use this folder” does not work on tablet."
            if on_mobile
            else (
                "Send History reports to a sync folder or OneDrive/Google Drive. "
                f"Files go under '{cloud_sync.CLOUD_FOLDER_NAME} - <login user>/History'."
            )
        ),
        *cloud_controls,
    )

    # --- Who's online (Firebase) ------------------------------------------------

    from app import firebase_presence

    presence_status = muted(firebase_presence.presence_status_text())
    presence_list = ft.Column(spacing=8, tight=True)
    tablet_name_field = ft.TextField(
        label="This tablet's name",
        value=firebase_presence.get_device_label(),
        hint_text="e.g. Warehouse Tablet 1",
        dense=True,
        expand=True,
    )

    def format_last_seen(entry: firebase_presence.PresenceEntry) -> str:
        if not entry.last_seen_epoch:
            return "never"
        try:
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(entry.last_seen_epoch, tz=timezone.utc).astimezone()
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            return entry.last_seen or "?"

    def render_presence_rows(entries: list[firebase_presence.PresenceEntry]) -> None:
        presence_list.controls.clear()
        if not entries:
            presence_list.controls.append(muted("No devices reporting yet."))
            return
        for entry in entries:
            status = "Online" if entry.online else "Away"
            status_color = "#2E7D32" if entry.online else "#9E9E9E"
            user_name = entry.username or "(not logged in)"
            role_bit = f" ({entry.role})" if entry.role else ""
            device_bit = entry.device_label or "Device"
            if entry.is_this_device:
                device_bit = f"{device_bit} — this device"
            picker_bit = (
                f" · Picker: {entry.current_picker}"
                if entry.current_picker
                else ""
            )
            presence_list.controls.append(
                ft.Container(
                    bgcolor="#FAFAFA",
                    border=ft.Border.all(1, "#E0E0E0"),
                    border_radius=6,
                    padding=12,
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        user_name,
                                        weight=ft.FontWeight.W_600,
                                        font_family=FONT_FAMILY,
                                        expand=True,
                                    ),
                                    ft.Text(
                                        status,
                                        color=status_color,
                                        weight=ft.FontWeight.W_600,
                                        font_family=FONT_FAMILY,
                                    ),
                                ]
                            ),
                            muted(f"{device_bit}{role_bit}{picker_bit}"),
                            muted(
                                f"Last seen: {format_last_seen(entry)}"
                                + (f" · v{entry.app_version}" if entry.app_version else "")
                            ),
                        ],
                        spacing=4,
                        tight=True,
                    ),
                )
            )

    def refresh_presence(_=None):
        if not is_admin:
            open_login_dialog()
            return

        def work():
            try:
                entries = firebase_presence.fetch_presence()

                def apply():
                    render_presence_rows(entries)
                    presence_status.value = firebase_presence.presence_status_text()
                    page.update()

                apply()
            except Exception as exc:
                show_snack(f"Could not load online list: {exc}", error=True)

        if not firebase_presence.is_configured():
            presence_list.controls = [
                muted("Firebase is not configured yet. Super Admin: use Firebase setup.")
            ]
            presence_status.value = firebase_presence.presence_status_text()
            page.update()
            return
        show_snack("Refreshing online devices…")
        page.run_thread(work)

    def save_tablet_name(_=None):
        if not is_admin:
            open_login_dialog()
            return
        label = firebase_presence.set_device_label(tablet_name_field.value or "")
        tablet_name_field.value = label
        page.update()
        show_snack(f"Tablet name set to “{label}”.")

        def work():
            try:
                firebase_presence.publish_heartbeat(
                    username=admin_username,
                    role=admin_role,
                    online=True,
                )
            except Exception:
                pass

        if firebase_presence.is_configured():
            page.run_thread(work)

    def open_firebase_setup(_=None):
        if not is_super_admin:
            show_snack("Only Super Admin can configure Firebase.", error=True)
            return
        cfg = firebase_presence.resolve_config()
        api_key = ft.TextField(
            label="Web API key",
            value=cfg.get("api_key") or "",
            dense=True,
            password=True,
            can_reveal_password=True,
        )
        database_url = ft.TextField(
            label="Realtime Database URL",
            value=cfg.get("database_url") or "",
            dense=True,
            hint_text="https://….firebasedatabase.app",
        )
        project_id = ft.TextField(
            label="Project ID (optional)",
            value=cfg.get("project_id") or "",
            dense=True,
        )

        def close_dialog(_=None):
            page.pop_dialog()

        def save_cfg(_=None):
            try:
                firebase_presence.save_config(
                    api_key=api_key.value or "",
                    database_url=database_url.value or "",
                    project_id=project_id.value or "",
                )
                page.pop_dialog()
                presence_status.value = firebase_presence.presence_status_text()
                page.update()
                show_snack("Firebase settings saved.")
                refresh_presence()
            except Exception as exc:
                show_snack(str(exc), error=True)

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Firebase setup"),
                content=ft.Column(
                    [
                        muted(
                            "Create a Firebase project once (see docs/FIREBASE_SETUP.md). "
                            "Paste the Web API key and Realtime Database URL here. "
                            "Also enable Anonymous Authentication and publish the presence rules."
                        ),
                        api_key,
                        database_url,
                        project_id,
                    ],
                    tight=True,
                    spacing=10,
                    width=440,
                    height=360,
                    scroll=ft.ScrollMode.AUTO,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Save", on_click=save_cfg),
                ],
            )
        )

    presence_controls: list[ft.Control] = [
        presence_status,
        ft.Row(
            [
                tablet_name_field,
                ft.OutlinedButton(
                    "Save name",
                    height=MIN_TOUCH,
                    disabled=not is_admin,
                    on_click=save_tablet_name,
                ),
            ],
            spacing=8,
        ),
        ft.Row(
            [
                ft.ElevatedButton(
                    "Refresh",
                    icon=ft.Icons.REFRESH,
                    bgcolor=PRIMARY if is_admin else "#9E9E9E",
                    color=ft.Colors.WHITE,
                    height=MIN_TOUCH,
                    disabled=not is_admin,
                    on_click=refresh_presence,
                ),
            ],
            spacing=12,
            wrap=True,
        ),
        presence_list,
    ]
    if is_super_admin:
        presence_controls.insert(
            2,
            ft.TextButton(
                "Firebase setup…",
                icon=ft.Icons.CLOUD,
                on_click=open_firebase_setup,
            ),
        )

    presence_section = _card(
        "Who's online",
        "See which tablets have the app open and which user is logged in. "
        "Requires a one-time Firebase setup (Super Admin).",
        *presence_controls,
    )

    return ft.Container(
        content=ft.Column(
            [
                section_title("Settings"),
                muted("Accounts, barcode master list, cloud sync, and who's online"),
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                accounts_section,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                barcode_section,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                cloud_section,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                auto_sync_section,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                presence_section,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=24,
        expand=True,
        bgcolor=BG_MAIN,
    )
