"""Settings page — accounts, fleet backup note, and who's online."""

from __future__ import annotations

import flet as ft

from app import auth
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
                        muted("Sign in to manage users, cloud sync, and who's online."),
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
            value=auth.ROLE_PICKER,
            options=[
                ft.dropdown.Option(auth.ROLE_PICKER, "Picker"),
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
                    role=role_field.value or auth.ROLE_PICKER,
                )
                page.pop_dialog()
                show_snack(
                    f"User added — {account.username} ({account.role_label}). "
                    "Synced to Firebase."
                )
                navigate("settings")
            except RuntimeError as exc:
                page.pop_dialog()
                show_snack(str(exc), error=True)
                navigate("settings")
            except (ValueError, PermissionError) as exc:
                show_snack(str(exc), error=True)

        password_field.on_submit = submit_create
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Add User / Picker"),
                content=ft.Column(
                    [
                        muted("Create a Picker or Admin account."),
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
            value=current_role if current_role != "checker" else auth.ROLE_PICKER,
            options=[
                ft.dropdown.Option(auth.ROLE_PICKER, "Picker"),
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
                    role_field.value or auth.ROLE_PICKER,
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
            auth.sync_with_cloud(force=False)
        except Exception:
            pass
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
            elif account.role == auth.ROLE_PICKER:
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
                            "Add User / Picker",
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
            muted("Only Super Admin can add Pickers and manage user accounts.")
        )
    else:
        account_controls.append(muted("Sign in as Super Admin to manage users."))

    accounts_section = _card(
        "Admin Accounts",
        "Users and passwords sync to all tablets when Firebase is set up. "
        "Picker users appear in the New Scan picker dropdown. "
        "Add Pickers, Admins, and Monitor Viewers; set passwords and change roles.",
        admin_status,
        *account_controls,
    )

    # --- Fleet sync (controlled from Monitor) ----------------------------------

    fleet_sync_section = _card(
        "Daily scanner backup",
        "Schedule and destination are controlled from Top Pickers Monitor "
        "(Super Admin -> Settings -> Fleet data sync). "
        "When enabled, this tablet uploads scanner.db to Firebase around the "
        "set time while the app is open. History -> Sync still works for one-off reports.",
        muted("Open the Monitor app on the office PC to turn this on and set the time."),
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
        ga_measurement_id = ft.TextField(
            label="Google Analytics Measurement ID (optional)",
            value=cfg.get("ga_measurement_id") or "",
            dense=True,
            hint_text="G-XXXXXXXXXX",
        )
        ga_api_secret = ft.TextField(
            label="GA4 Measurement Protocol API secret (optional)",
            value=cfg.get("ga_api_secret") or "",
            dense=True,
            password=True,
            can_reveal_password=True,
        )

        def close_dialog(_=None):
            page.pop_dialog()

        def save_cfg(_=None):
            try:
                firebase_presence.save_config(
                    api_key=api_key.value or "",
                    database_url=database_url.value or "",
                    project_id=project_id.value or "",
                    ga_measurement_id=ga_measurement_id.value or "",
                    ga_api_secret=ga_api_secret.value or "",
                )
                page.pop_dialog()
                presence_status.value = firebase_presence.presence_status_text()
                page.update()
                from app import analytics

                show_snack(
                    "Firebase settings saved. " + analytics.status_text()
                )
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
                        muted(
                            "Optional — Google Analytics 4 (daily active tablets + "
                            "completed picks). See docs/FIREBASE_SETUP.md § Google Analytics."
                        ),
                        ga_measurement_id,
                        ga_api_secret,
                    ],
                    tight=True,
                    spacing=10,
                    width=440,
                    height=480,
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
                muted("Accounts, fleet backup status, and who's online"),
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                accounts_section,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                fleet_sync_section,
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
