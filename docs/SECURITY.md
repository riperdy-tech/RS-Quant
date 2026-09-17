# QuantDesk Security Architecture & Threat Model

Authoritative plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

---

## 1. Authentication & Role-Based Access Control (RBAC)

QuantDesk implements defense-in-depth security across browser, API, and process boundaries (§15.4, §16.1):

### 1.1 First-Run Bootstrap Enrollment
- The platform generates a cryptographically secure 128-bit hex token (`secrets.token_hex(16)`) at first boot.
- The token is displayed exclusively within the native OS desktop launcher window and is never printed in logs or included in URL parameters.
- The administrator types this token into the browser to initialize their password.
- Passwords are encrypted using **Argon2id** (`argon2-cffi`) with secure memory and iteration parameters.
- Upon successful enrollment, the bootstrap token is permanently deleted.

### 1.2 RBAC Roles
1. **Admin**: Full access. Can arm/disarm live mode, store exchange credentials, execute emergency stop, and launch backtests.
2. **Operator**: Can start/pause strategies, configure strategy parameters, and trigger emergency stop. Cannot alter exchange credentials or arm live mode.
3. **Viewer**: Read-only monitoring. All mutation buttons are removed from the UI. Direct mutation requests submitted to `/api/v1/commands` fail rigidly with `HTTP 403 Forbidden`.

---

## 2. Web & API Protections

### 2.1 Host & Origin Validation
- **Host Header Validation**: Every incoming HTTP request must match `127.0.0.1`, `localhost`, or configured local interfaces. External or forged Host headers receive `HTTP 403 Forbidden` to prevent DNS rebinding attacks.
- **Origin Header Check**: State-changing requests (`POST`, `PUT`, `DELETE`) require the `Origin` header to match authorized loopback origins.

### 2.2 CSRF Protection
- Sessions utilize secure, scoped session cookies with `SameSite=Strict`.
- Mutating endpoints require matching CSRF tokens in request headers.

### 2.3 Command Idempotency & Optimistic Concurrency (CAS)
- **Command Idempotency**: Commands carry a client-generated UUID `command_id`. Submitting identical commands returns `HTTP 202 Accepted` with the existing receipt; conflicting bodies return `HTTP 409 Conflict`.
- **CAS Revision Enforcement**: Dangerous commands submit `expected_state_version`. If another operator or event modified the state, the command fails with `HTTP 409 Conflict`.
- **Preview Tokens**: Two-step confirmations generate preview tokens bound to `(actor, target, command_type, current_version)`. Wrong-account tokens return `HTTP 403 Forbidden`.

---

## 3. Secret Management & Input Sanitization

### 3.1 Write-Only Keyring Storage
- Exchange API keys, secret passphrases, and private journal encryption keys are stored directly in the operating system's credential vault (**Windows Credential Manager** via `keyring`).
- The API settings endpoints are **write-only**: they return only key fingerprints or connection status, never raw keys or secrets.

### 3.2 Secret Scrubbing
- All logging handlers and the diagnostic bundle exporter pass output through `SecretRedactionFilter` and `DiagnosticBundleExporter`.
- Regular expressions identify API keys, secret passphrases, session cookies, and bearer tokens, replacing them with `[REDACTED]`.

### 3.3 CSV Formula Injection Escaping (§15.4)
- When exporting trades, orders, or backtest logs to CSV, all string values starting with spreadsheet formula triggers (`=`, `+`, `-`, `@`, `\t`, `\r`) are automatically escaped with a leading single quote `'`. This neutralizes Dynamic Data Exchange (DDE) and formula injection attacks when files are opened in Microsoft Excel.

### 3.4 Process Isolation
- ML model training and backtests run in dedicated subprocess workers without exchange keys or gateway network access.
- Code execution is restricted: no arbitrary Python consoles, terminal shells, SQL executors, or unverified code-upload strategy runners exist.
