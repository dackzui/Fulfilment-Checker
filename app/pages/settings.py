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

    folder = cloud_sync.get_sync_folder()
    folder_label = muted(
        str(folder) if folder else "No folder selected yet."
    )
    clear_folder_btn = ft.OutlinedButton(
        "Clear folder",
        height=MIN_TOUCH,
        disabled=folder is None,
    )

    def refresh_folder_ui():
        folder_now = cloud_sync.get_sync_folder()
        folder_label.value = (
            str(folder_now) if folder_now else "No folder selected yet."
        )
        clear_folder_btn.disabled = folder_now is None
        page.update()

    async def pick_sync_folder(_=None):
        if not is_admin:
            open_login_dialog()
            return
        path = await file_picker.get_directory_path(
            dialog_title="Choose Google Drive or OneDrive folder"
        )
        if not path:
            return
        try:
            cloud_sync.set_sync_folder(path)
            refresh_folder_ui()
            show_snack(f"Cloud folder set — {path}")
        except Exception as exc:
            show_snack(str(exc), error=True)

    def clear_sync_folder(_=None):
        cloud_sync.set_sync_folder(None)
        refresh_folder_ui()
        show_snack("Cloud folder cleared.")

    clear_folder_btn.on_click = clear_sync_folder

    cloud_section = _card(
        "Cloud Sync",
        "For tablets and PCs: choose the Google Drive or OneDrive folder on this "
        "device. History → Sync copies reports into a folder named "
        f"'{cloud_sync.CLOUD_FOLDER_NAME} - <login user>' — no cloud login or API keys.",
        ft.Text(
            "Cloud folder on this device",
            weight=ft.FontWeight.W_600,
            font_family=FONT_FAMILY,
        ),
        muted(
            "Install Google Drive or OneDrive on the tablet, then pick that "
            "synced folder here. Files sync to the cloud through the Drive app."
        ),
        folder_label,
        ft.Row(
            [
                ft.ElevatedButton(
                    "Choose cloud folder",
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
        muted("Only signed-in Admin or Super Admin can change the folder."),
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
