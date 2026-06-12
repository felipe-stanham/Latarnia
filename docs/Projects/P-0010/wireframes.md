# P-0010 Wireframes

Structural ASCII only — layout and conditional zones, not styling. These extend
the existing P-0008 dashboard; only the changed zones are detailed.

## Screen: Dashboard — Superuser view [cap-003, cap-004]

```
+----------------------------------------------------------------+
| HEADER: Latarnia | env=tst | user: admin (Superuser) | Logout  |
+----------------------------------------------------------------+
| [ App cards ... ]                                              |
+----------------------------------------------------------------+
| PLATFORM CONTROLS                                              |
|   [ Refresh ]   [ Restart Platform ]   <-- visible (Superuser) |
+----------------------------------------------------------------+
| PLATFORM LOGS  (panel visible for Superuser)                  |
|   +--------------------------------------------------------+   |
|   | 14:31 INFO  ... latarnia main log lines ...            |   |
|   +--------------------------------------------------------+   |
+----------------------------------------------------------------+
| RECENT ACTIVITY (all events incl. system)                     |
|   - 14:31 app_started  source=latarnik                        |
|   - 14:30 health_check source=system                          |
+----------------------------------------------------------------+
```

## Screen: Dashboard — non-Superuser view [cap-003, cap-004, cap-005]

```
+----------------------------------------------------------------+
| HEADER: Latarnia | env=tst | user: alice (User) | Logout       |
+----------------------------------------------------------------+
| [ App cards: only apps alice can see ]                         |
+----------------------------------------------------------------+
| PLATFORM CONTROLS                                              |
|   [ Refresh ]                          <-- NO Restart button   |
+----------------------------------------------------------------+
| (PLATFORM LOGS panel NOT rendered)                            |
+----------------------------------------------------------------+
| RECENT ACTIVITY (filtered: only full-role apps; no system)     |
|   - 14:31 app_started  source=latarnik   (alice has full)     |
|   (other_app + system events hidden)                          |
+----------------------------------------------------------------+
```

> Server enforces the same rules: `POST /api/system/restart` and
> `GET /api/logs/latarnia` return 403 for alice even if called directly;
> `/api/activity/recent` returns the already-filtered list.

## Screen: Users & Roles (Superuser only) [cap-006, cap-007, cap-008]

```
+----------------------------------------------------------------+
| USERS & ROLES                              [ + Invite User ]   |
+----------------------------------------------------------------+
| Username | Super | Active | Last login | Actions               |
|----------|-------|--------|------------|-----------------------|
| admin    |  yes  |  yes   | 14:20      | (self: no destructive)|
| alice    |  no   |  yes   | 13:05      | [Roles][Deactivate]   |
|          |       |        |            | [Re-issue setup][Del] |
| bob      |  no   |  no    | --         | [Roles][Activate]     |
|          |       |        |            | [Re-issue setup][Del] |
+----------------------------------------------------------------+
```

Action rules:
- `admin` row (the current Superuser / self): no **Delete** (self-delete blocked),
  no **Deactivate**. The last active Superuser also cannot be deleted (server 409).
- Active user (`alice`): shows **Deactivate**, **Re-issue setup**, **Delete**.
- Inactive user (`bob`): shows **Activate** (cap-007), **Re-issue setup**,
  **Delete**. **Activate** is disabled/replaced with a hint if the user has no
  TOTP credential (server returns 409 → "re-issue setup instead").

## Modal: Re-issue Setup result [cap-008]

```
+--------------------------------------------+
| Re-issue authenticator setup for: bob      |
+--------------------------------------------+
| This deactivates bob, ends their sessions, |
| and resets their authenticator. Continue?  |
|                          [ Cancel ] [ OK ] |
+--------------------------------------------+

  after OK:
+--------------------------------------------+
| New setup link (valid 24h, single use):    |
|  https://home.stanham.com:8443/auth/setup  |
|     ?token=XXXXXXXX            [ Copy ]     |
| Send this to bob. Their old device no       |
| longer works until they re-enroll.         |
+--------------------------------------------+
```

## Confirm: Delete user [cap-006]

```
+--------------------------------------------+
| Delete user: bob                           |
| Permanently removes bob, their credentials,|
| sessions, roles, and machine tokens. Roles |
| bob granted to others are kept (attribution|
| cleared). This cannot be undone.           |
|                       [ Cancel ] [ Delete ]|
+--------------------------------------------+
```
