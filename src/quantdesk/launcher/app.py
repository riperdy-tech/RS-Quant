"""QuantDesk Desktop Graphical Launcher and Single-Instance Controller (§16.1)."""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Any

from quantdesk.api.auth import auth_manager
from quantdesk.launcher.errors import diagnose_environment
from quantdesk.observability.diagnostics import DiagnosticBundleExporter
from quantdesk.supervisor.lock import AccountLockedError
from quantdesk.supervisor.manager import ActiveInstanceInfo, SupervisorManager


class LauncherApp:
    """Desktop Launcher orchestrating supervisor, single-instance checks, and GUI (§16.1)."""

    def __init__(
        self,
        account_dir: Path | str = "data/accounts/demo",
        account_name: str = "demo",
        mode: str = "DEMO",
        headless: bool = False,
        auto_open_browser: bool = True,
        preferred_port: int = 8000,
        spawn_api: bool = True,
    ):
        self.account_dir = Path(account_dir).resolve()
        self.account_name = account_name
        self.mode = mode
        self.headless = headless
        self.auto_open_browser = auto_open_browser
        self.preferred_port = preferred_port
        self.spawn_api = spawn_api

        self.supervisor = SupervisorManager(
            account_dir=self.account_dir,
            account_name=self.account_name,
            mode=self.mode,
            preferred_port=self.preferred_port,
        )
        self.active_instance: ActiveInstanceInfo | None = None
        self._root: Any = None
        self._is_running = False

    @property
    def base_url(self) -> str:
        if self.active_instance:
            return self.active_instance.base_url
        return self.supervisor.base_url

    @property
    def is_running(self) -> bool:
        return self._is_running or self.supervisor.is_running

    def start(self) -> dict[str, Any]:
        """Launches the system or activates existing running dashboard.

        Returns:
            Dict containing action taken and instance status (§16.1).
        """
        # 1. Single-Instance Check
        existing = self.supervisor.check_existing_instance()
        if existing:
            self.active_instance = existing
            if self.auto_open_browser:
                webbrowser.open(existing.base_url)

            if not self.headless:
                self._show_already_running_gui(existing)

            return {
                "action": "OPEN_EXISTING_DASHBOARD",
                "account_writer_process_count": 1,
                "base_url": existing.base_url,
                "pid": existing.pid,
                "epoch": existing.epoch,
            }

        # 2. Run Pre-flight Environment Diagnostics
        issues = diagnose_environment(self.account_dir)
        fatal_issues = [i for i in issues if not i.recoverable]
        if fatal_issues and not self.headless:
            self._show_fatal_errors_gui(fatal_issues)
            return {
                "action": "ABORTED_DIAGNOSTIC_FAILURE",
                "account_writer_process_count": 0,
                "issues": [i.code for i in fatal_issues],
            }

        # 3. Start Local Supervisor (acquires exclusive lock & resolves port)
        try:
            epoch = self.supervisor.start(spawn_api=self.spawn_api)
        except AccountLockedError as exc:
            existing = self.supervisor.check_existing_instance()
            base_url = existing.base_url if existing else f"http://127.0.0.1:{self.preferred_port}"
            pid = existing.pid if existing else (exc.owner_pid or 0)
            epoch = existing.epoch if existing else self.supervisor.lock.read_current_epoch()
            if self.auto_open_browser:
                webbrowser.open(base_url)
            return {
                "action": "OPEN_EXISTING_DASHBOARD",
                "account_writer_process_count": 1,
                "base_url": base_url,
                "pid": pid,
                "epoch": epoch,
            }

        self._is_running = True

        if self.auto_open_browser:
            webbrowser.open(self.supervisor.base_url)

        # 4. Launch Desktop GUI if not in headless mode
        if not self.headless:
            self._run_gui()

        return {
            "action": "STARTED_NEW_INSTANCE",
            "account_writer_process_count": 1,
            "base_url": self.supervisor.base_url,
            "pid": os.getpid(),
            "epoch": epoch,
        }

    def open_dashboard(self) -> None:
        """Opens default web browser to the active dashboard URL."""
        webbrowser.open(self.base_url)

    def run_diagnostics(self) -> list[str]:
        """Runs pre-flight diagnostics and returns list of issue descriptions."""
        issues = diagnose_environment(self.account_dir)
        if not issues:
            return ["Environment Healthy: All core runtime libraries and permissions verified."]
        return [f"[{i.code}] {i.title}: {i.description} -> {i.recommended_action}" for i in issues]

    def export_support_bundle(self, destination: Path | str | None = None) -> Path:
        """Generates a sanitized diagnostic support bundle."""
        dest_path = (
            Path(destination).resolve()
            if destination
            else self.account_dir / "diagnostics" / "support_bundle.json"
        )
        exporter = DiagnosticBundleExporter(data_dir=self.account_dir)
        return exporter.export_to_json(dest_path)

    def stop(self) -> None:
        """Gracefully terminates supervisor and shuts down GUI."""
        self._is_running = False
        self.supervisor.stop()
        if self._root:
            try:
                self._root.quit()
                self._root.destroy()
            except Exception:
                pass
            self._root = None

    def _show_already_running_gui(self, existing: ActiveInstanceInfo) -> None:
        """Displays friendly notice when a second launcher instance is invoked."""
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                "QuantDesk Already Running",
                f"QuantDesk is already active on PID {existing.pid}.\n\n"
                f"Dashboard URL: {existing.base_url}\n"
                "Opening the existing dashboard in your browser.",
            )
            root.destroy()
        except Exception:
            pass

    def _show_fatal_errors_gui(self, fatal_issues: list[Any]) -> None:
        """Displays error window explaining startup failures without terminal/PowerShell (§16.1)."""
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            details = "\n\n".join(
                f"• {i.title}:\n  {i.description}\n  Action: {i.recommended_action}"
                for i in fatal_issues
            )
            messagebox.showerror(
                "QuantDesk Startup Error",
                f"QuantDesk cannot start due to the following issue(s):\n\n{details}",
            )
            root.destroy()
        except Exception:
            pass

    def _run_gui(self) -> None:
        """Constructs and displays the main Tkinter Desktop Launcher window (§16.1)."""
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
        except ImportError:
            return

        self._root = tk.Tk()
        self._root.title("QuantDesk Desktop Launcher")
        self._root.geometry("520x420")
        self._root.resizable(False, False)

        # Main Layout Container
        pad_frame = ttk.Frame(self._root, padding=16)
        pad_frame.pack(fill=tk.BOTH, expand=True)

        # Header Title and Mode
        header_frame = ttk.Frame(pad_frame)
        header_frame.pack(fill=tk.X, pady=(0, 12))
        title_lbl = ttk.Label(
            header_frame,
            text="QuantDesk Desktop",
            font=("Segoe UI", 16, "bold"),
        )
        title_lbl.pack(side=tk.LEFT)

        mode_badge = ttk.Label(
            header_frame,
            text=f"[{self.mode}]",
            font=("Segoe UI", 10, "bold"),
            foreground="#2e7d32",
        )
        mode_badge.pack(side=tk.RIGHT, pady=4)

        # System Status Card
        status_card = ttk.LabelFrame(pad_frame, text=" System Status ", padding=10)
        status_card.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(
            status_card,
            text=f"Engine State: RUNNING (Epoch: {self.supervisor.lock.epoch})",
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=2)

        ttk.Label(
            status_card,
            text=f"API Endpoint: {self.supervisor.base_url}",
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=2)

        # One-Time Bootstrap Code Card (if un-bootstrapped per §16.1)
        if not auth_manager.is_bootstrapped and auth_manager.bootstrap_token:
            boot_card = tk.LabelFrame(
                pad_frame,
                text=" Initial Admin Setup Required ",
                padx=10,
                pady=8,
                fg="#b78103",
                bg="#fffde7",
                relief=tk.SOLID,
                bd=1,
            )
            boot_card.pack(fill=tk.X, pady=(0, 12))

            tk.Label(
                boot_card,
                text="Enter this one-time bootstrap code in your browser:",
                font=("Segoe UI", 8),
                bg="#fffde7",
            ).pack(anchor=tk.W)

            token_lbl = tk.Label(
                boot_card,
                text=auth_manager.bootstrap_token,
                font=("Consolas", 11, "bold"),
                fg="#b78103",
                bg="#fffde7",
            )
            token_lbl.pack(anchor=tk.W, pady=2)

        # Action Buttons
        btn_frame = ttk.Frame(pad_frame)
        btn_frame.pack(fill=tk.X, pady=4)

        open_btn = ttk.Button(
            btn_frame,
            text="Open Dashboard",
            command=self.open_dashboard,
        )
        open_btn.pack(fill=tk.X, pady=3)

        diag_btn = ttk.Button(
            btn_frame,
            text="Run Diagnostics",
            command=lambda: messagebox.showinfo(
                "Diagnostic Results", "\n\n".join(self.run_diagnostics())
            ),
        )
        diag_btn.pack(fill=tk.X, pady=3)

        export_btn = ttk.Button(
            btn_frame,
            text="Export Support Bundle",
            command=lambda: messagebox.showinfo(
                "Support Bundle",
                f"Bundle saved to:\n{self.export_support_bundle()}",
            ),
        )
        export_btn.pack(fill=tk.X, pady=3)

        stop_btn = ttk.Button(
            btn_frame,
            text="Stop Safely",
            command=self.stop,
        )
        stop_btn.pack(fill=tk.X, pady=(12, 3))

        # Handle window close (X button)
        self._root.protocol("WM_DELETE_WINDOW", self.stop)
        self._root.mainloop()
