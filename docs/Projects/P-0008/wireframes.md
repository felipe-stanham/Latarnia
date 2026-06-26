# P-0008: Wireframes

## Screen: TOTP First-Run Setup [cap-009]

Shown only once, when no users exist in the platform DB.

```
+--------------------------------------------------------------+
|                     LATARNIA SETUP                           |
+--------------------------------------------------------------+
|                                                              |
|  Welcome. Scan the QR code below with your Authenticator    |
|  app (Google Authenticator, Microsoft Authenticator, etc.)   |
|                                                              |
|         +------------------+                                 |
|         |  [QR CODE IMAGE] |                                 |
|         |  128 x 128 px    |                                 |
|         +------------------+                                 |
|                                                              |
|  Can't scan? Enter this key manually:                        |
|  [ XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX ]                |
|                                                              |
|  Enter the 6-digit code from your app to confirm:           |
|  [ _ _ _ _ _ _ ]                                            |
|                                                              |
|                    [ Confirm Setup ]                         |
|                                                              |
|  ⚠ This page will not be shown again.                       |
+--------------------------------------------------------------+
```

## Screen: Login [cap-010]

```
+--------------------------------------------------------------+
|                        LATARNIA                              |
+--------------------------------------------------------------+
|                                                              |
|                   Enter your 6-digit code                    |
|              from your Authenticator app                     |
|                                                              |
|              [ _ _ _ _ _ _ ]                                |
|                                                              |
|                     [ Sign In ]                              |
|                                                              |
|  [error message if code invalid — shown inline]              |
+--------------------------------------------------------------+
```

## Screen: Dashboard (Authenticated User — Role-Filtered) [cap-015, cap-016]

Tiles are only shown for apps where user's role ≠ `none`. Tile content matches existing dashboard layout.

```
+--------------------------------------------------------------+
| LATARNIA           [username]  [role badge]  [Sign Out]      |
+--------------------------------------------------------------+
| Recent Activity |  Apps                                      |
+-----------------+--------------------------------------------+
|                 |                                            |
| [activity feed] | [App Tile: my_app]  [App Tile: other_app] |
|                 | Role: webUI-med     Role: full             |
|                 |                                            |
|                 | [App Tile hidden if role=none]             |
|                 |                                            |
|                 |  (Superuser sees all tiles)                |
|                 |                                            |
+-----------------+--------------------------------------------+
| * Superuser-only tab: [ Users & Roles ]                      |
+--------------------------------------------------------------+
```

## Screen: User & Role Management (Superuser Only) [cap-017, cap-018]

Accessible from the "Users & Roles" tab in the dashboard sidebar. Superuser-only.

```
+--------------------------------------------------------------+
| LATARNIA           [admin]  [superuser]  [Sign Out]          |
+--------------------------------------------------------------+
| Apps | Recent Activity | Users & Roles                       |
+--------------------------------------------------------------+
|                                                              |
|  USERS                                           [ + User ]  |
|  +----------------------------------------------------------+ |
|  | Name    | Superuser | Last Login  | Actions             | |
|  |---------|-----------|-------------|---------------------| |
|  | alice   | ✓         | 2026-05-12  | [Edit Roles]        | |
|  | bob     |           | 2026-05-10  | [Edit Roles]        | |
|  +----------------------------------------------------------+ |
|                                                              |
|  APP ROLE ASSIGNMENT — bob                                   |
|  +----------------------------------------------------------+ |
|  | App          | Role                         |            | |
|  |--------------|------------------------------|            | |
|  | my_app       | [webUI-med        ▼]          |            | |
|  | other_app    | [none             ▼]          |            | |
|  | third_app    | [full             ▼] ★        |            | |
|  +----------------------------------------------------------+ |
|  ★ full role requires superuser to assign                    |
|                                      [ Save Changes ]        |
|                                                              |
+--------------------------------------------------------------+
```

## Screen: Machine Token Management [cap-019]

Accessible from user settings or a dedicated "API Tokens" tab.

```
+--------------------------------------------------------------+
| LATARNIA           [admin]  [superuser]  [Sign Out]          |
+--------------------------------------------------------------+
| Apps | Recent Activity | Users & Roles | API Tokens          |
+--------------------------------------------------------------+
|                                                              |
|  API TOKENS                            [ + New Token ]       |
|  +----------------------------------------------------------+ |
|  | Label        | Apps               | Expires   | Actions | |
|  |--------------|--------------------|-----------|---------| |
|  | homeassist   | my_app (full)      | Never     | Revoke  | |
|  | claude-agent | my_app (webUI-med) | 2027-01-01| Revoke  | |
|  |              | other_app (webUI-low)|          |         | |
|  +----------------------------------------------------------+ |
|                                                              |
|  NEW TOKEN                                                   |
|  Label:   [ claude-agent                    ]                |
|  Expires: [ Never  ▼ ]                                       |
|  App Access:                                                 |
|    my_app     [ webUI-med ▼ ]                                |
|    other_app  [ none      ▼ ]                                |
|                              [ Generate Token ]              |
|                                                              |
|  ┌──────────────────────────────────────────────────────┐   |
|  │ ⚠ Token (shown once — copy now):                     │   |
|  │ eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...             │   |
|  │                              [ Copy ]                 │   |
|  └──────────────────────────────────────────────────────┘   |
|                                                              |
+--------------------------------------------------------------+
```
